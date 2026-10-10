"""Pure env-core logic: golden hashing, reward/health/recovery, action catalog, mask, lock (§5.2, §5.6, §5.8).

Golden templates are the real committed golden_live.json; everything else is hand-written input.
"""

from __future__ import annotations

import copy
import math

import pytest

from env.golden import RESTARTED_AT_ANNOTATION, load_golden, template_hash
from env.k8s_actions import (
    CATALOG,
    RESTORE,
    SCALE,
    Action,
    Lock,
    compute_mask,
    lock_should_clear,
    rollout_complete,
    scale_target,
)
from env.reward import (
    healthy,
    latency_violation,
    recovery_start,
    replica_surplus,
    reward,
    sla_violation,
)
from env.telemetry import DeploymentStatus

L_SLA_MS_TEST = 500.0
BASE = {"frontend": 1, "cartservice": 1, "currencyservice": 1, "productcatalogservice": 1}


# ----------------------------------------------------------------------------- golden

def test_golden_loads_and_hash_ignores_restarted_at(contract):
    golden = load_golden(contract)
    assert set(golden) == set(contract.cluster.managed)
    tpl = copy.deepcopy(golden["frontend"].template)
    tpl.setdefault("metadata", {}).setdefault("annotations", {})[RESTARTED_AT_ANNOTATION] = "2026-10-09T00:00:00Z"
    assert template_hash(tpl) == golden["frontend"].template_hash          # rollout restart is not drift
    tpl["spec"]["containers"][0].setdefault("env", []).append({"name": "EXTRA_LATENCY", "value": "600ms"})
    assert template_hash(tpl) != golden["frontend"].template_hash          # F1 is drift


def test_golden_carries_contract_overrides(contract):
    golden = load_golden(contract)
    env = {e["name"]: e["value"] for e in golden["cartservice"].template["spec"]["containers"][0].get("env", [])}
    assert env.get("DOTNET_ThreadPool_ForceMinWorkerThreads") == "0x20"
    assert golden["frontend"].template["spec"]["containers"][0]["resources"]["limits"]["cpu"] == "400m"


# ----------------------------------------------------------------------------- reward

@pytest.mark.parametrize(("p99", "want"), [(0.0, 0.0), (L_SLA_MS_TEST, 0.0), (2 * L_SLA_MS_TEST, 1 / 3),
                                           (8 * L_SLA_MS_TEST, 1.0), (1e9, 1.0)])
def test_latency_violation(p99, want):
    assert latency_violation(p99, L_SLA_MS_TEST) == pytest.approx(want)


def test_sla_violation_combines_and_clips(contract):
    assert sla_violation(L_SLA_MS_TEST, 0.0, L_SLA_MS_TEST, contract) == 0.0
    assert sla_violation(L_SLA_MS_TEST, 0.105, L_SLA_MS_TEST, contract) == pytest.approx(0.5)   # (0.105-0.01)/0.19
    assert sla_violation(8 * L_SLA_MS_TEST, 0.2, L_SLA_MS_TEST, contract) == 1.0
    with pytest.raises(ValueError):
        sla_violation(float("nan"), 0.0, L_SLA_MS_TEST, contract)


def test_reward_bounds_and_costs(contract):
    spec_max = dict(contract.replicas.max)
    assert replica_surplus(BASE, contract) == 0.0
    assert replica_surplus(spec_max, contract) == 1.0
    assert reward(0.0, "NOOP", 0.0, contract) == 0.0
    assert reward(1.0, "RESTART", 1.0, contract) == pytest.approx(-1.09)               # documented floor
    assert reward(0.2, "SCALE", 0.25, contract) == pytest.approx(-(0.2 + 0.02 + 0.05 * 0.25))


def test_healthy_and_recovery(contract):
    assert healthy(L_SLA_MS_TEST, 0.01, L_SLA_MS_TEST, contract)
    assert not healthy(L_SLA_MS_TEST + 1, 0.0, L_SLA_MS_TEST, contract)
    #       tick:  0     1      2      3     4     5     6
    health = [True, False, False, True, True, True, True]
    cured = [True, False, False, False, False, True, True]
    assert recovery_start(health, cured, k_inject=1, contract=contract) == 3      # H3,H4,H5 and cured at 5
    assert recovery_start(health, [False] * 7, k_inject=1, contract=contract) is None
    assert recovery_start([True] * 7, [True] * 7, k_inject=2, contract=contract) == 3   # strictly after injection


# ----------------------------------------------------------------------------- catalog, mask, lock

def test_catalog_matches_contract(contract):
    assert len(CATALOG) == 12 and CATALOG[0].kind == "NOOP"
    assert [a.name for a in CATALOG if a.kind == RESTORE] == [
        "RESTORE productcatalogservice", "RESTORE cartservice", "RESTORE currencyservice"]
    assert {a.target for a in CATALOG if a.target} <= set(contract.cluster.managed)


def test_mask_rules(contract):
    m = compute_mask(BASE, lock_held=False, contract=contract)
    assert m.dtype.name == "int8" and m.shape == (12,)
    assert m.tolist() == [1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1]          # SCALE_DOWN fe invalid at 1
    m = compute_mask({**BASE, "frontend": 3, "cartservice": 2, "currencyservice": 0}, False, contract)
    assert m[5] == 0 and m[6] == 1 and m[7] == 0                         # fe at max, can scale down; cart at max
    assert m[3] == 0 and m[8] == 1 and m[11] == 1                        # currency at 0: no RESTART; SCALE_UP ok
    assert all(m[a.id] == 1 for a in CATALOG if a.kind == RESTORE)       # never masked by drift
    assert compute_mask(BASE, lock_held=True, contract=contract).tolist() == [1] + [0] * 11


@pytest.mark.parametrize(("delta", "spec", "want"), [(+1, 1, 2), (+1, 3, None), (-1, 1, None), (-1, 2, 1)])
def test_executor_replica_bounds(contract, delta, spec, want):
    assert scale_target(Action(5 if delta > 0 else 6, SCALE, "frontend", delta), spec, contract) == want


def status(**kw):
    base = {"spec_replicas": 1, "status_replicas": 1, "available_replicas": 1, "updated_replicas": 1,
            "generation": 3, "observed_generation": 3}
    return DeploymentStatus(**{**base, **kw})


def test_rollout_complete():
    assert rollout_complete(status())
    assert not rollout_complete(status(observed_generation=2))
    assert not rollout_complete(status(spec_replicas=2))                 # scaling up, not yet available


def test_lock_clearing(contract):
    lock = Lock(CATALOG[2], "cartservice", 0.0, 100.0, gen_before=3)
    assert lock_should_clear(lock, status(), 105.0, contract) is None                 # generation not advanced yet
    assert lock_should_clear(lock, status(generation=4, observed_generation=3), 110.0, contract) is None
    assert lock_should_clear(lock, status(generation=4, observed_generation=4), 120.0, contract) == "rollout_complete"
    assert lock_should_clear(lock, None, 100.0 + contract.clock.inflight_timeout_s, contract) == "stalled"
    no_call = Lock(CATALOG[10], "cartservice", 0.0, 100.0, gen_before=3, no_call=True)
    assert lock_should_clear(no_call, status(), 101.0, contract) == "no_call"
    assert math.isfinite(contract.clock.inflight_timeout_s)


def test_transition_recorder_flushes_and_validates(tmp_path):
    import json

    import numpy as np

    from env.recorder import TRANSITION_FIELDS, TransitionRecorder
    rec = TransitionRecorder(tmp_path, "run1", 3)
    row = dict.fromkeys(TRANSITION_FIELDS)
    row.update(obs=np.zeros(36, dtype=np.float32), mask=np.ones(12, dtype=np.int8), tick=0)
    rec.append(row)
    line = json.loads((tmp_path / "run1" / "episode_3.jsonl").read_text())   # visible before close: flushed
    assert line["obs"] == [0.0] * 36 and line["mask"] == [1] * 12
    with pytest.raises(ValueError):
        rec.append({"tick": 1})
    rec.close()


def test_latest_pod_change_ignores_future_clock_jump_stamps():
    from env.boutique_env import SETTLE_AFTER_POD_CHANGE_S, latest_pod_change
    now = 1_000_000.0
    jumped = now + 5.5 * 3600            # kubelet stamp from a boot with the RTC read as IST
    assert latest_pod_change([now - 100, jumped, now - 40], now) == (now - 40, 1)
    assert latest_pod_change([now + 1.0], now) == (now + 1.0, 0)          # small skew is kept
    assert latest_pod_change([now + SETTLE_AFTER_POD_CHANGE_S + 1], now) == (None, 1)
    assert latest_pod_change([], now) == (None, 0)
