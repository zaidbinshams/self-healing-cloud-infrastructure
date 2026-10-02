"""§5.5 imputation on recorded real ticks (tests/fixtures/ticks_steady.jsonl).

Each rule is exercised by taking a real recorded tick and removing or altering exactly the input
that rule handles; everything else stays as recorded.
"""

from __future__ import annotations

import dataclasses
import json
import math

import pytest

from env.telemetry import (
    PROM_METRICS,
    DeploymentsResult,
    ImputeState,
    LocustResult,
    PodsResult,
    PromResult,
    RawTick,
    dumps,
    features_to_dict,
    impute,
)

D = "frontend"


def run(raws: list[RawTick], contract, state: ImputeState | None = None):
    state = state or ImputeState()
    out = []
    for raw in raws:
        f, state = impute(raw, state, contract)
        out.append(f)
    return out, state


def drop_prom(raw: RawTick, metric: str, dep: str, value: float | None = None) -> RawTick:
    """Remove `dep` from one query result (value=None) or replace its value (e.g. NaN)."""
    pr = raw.prom[metric]
    values = {k: v for k, v in pr.values.items() if k != dep}
    if value is not None:
        values[dep] = value
    return dataclasses.replace(raw, prom={**raw.prom, metric: dataclasses.replace(pr, values=values)})


def all_sources_ok(raw: RawTick) -> bool:
    return all(raw.source_ok().values()) and raw.locust.n > 0


# ----------------------------------------------------------------------------- the recording itself

def test_fixture_is_real_and_complete(steady, contract):
    header, ticks = steady
    assert header["n_requested"] == len(ticks) == 30
    assert header["contract_sha256"] == contract.sha256, "fixture predates the current contract: re-record"
    assert not header["git_dirty"], "fixture was recorded from uncommitted code"
    assert header["managed"] == list(contract.cluster.managed)
    assert [t["tick"] for t in ticks] == list(range(len(ticks)))


def test_replay_reproduces_recorded_features(steady, steady_raw, contract):
    """impute() is deterministic: replaying the raw ticks gives the features recorded live."""
    features, _ = run(steady_raw, contract)
    for rec, f in zip(steady[1], features, strict=True):
        assert json.loads(dumps(features_to_dict(f))) == rec["features"]   # same JSON round-trip as recording
        assert f.stale == rec["stale"]


def test_clean_tick_uses_values_verbatim(steady_raw, contract):
    """Rule 2: when every source answered, features are the raw values and the tick is not stale."""
    features, _ = run(steady_raw, contract)
    clean = [(r, f) for r, f in zip(steady_raw, features, strict=True) if all_sources_ok(r)]
    assert clean, "fixture has no tick where all sources answered"
    for raw, f in clean:
        assert f.p99_ms == raw.locust.p99_ms and f.rps == raw.locust.rps
        assert f.fail_ratio == raw.locust.failures / raw.locust.n
        for d in contract.cluster.managed:
            s = f.services[d]
            for m, got in (("cpu", s.cpu_cores), ("thr", s.throttle), ("mem", s.mem_bytes)):
                if d in raw.prom[m].values and math.isfinite(raw.prom[m].values[d]):
                    assert got == raw.prom[m].values[d]
            assert s.spec_replicas == raw.deployments.items[d].spec_replicas


# ----------------------------------------------------------------------------- rules 3 and 4

@pytest.mark.parametrize("metric", PROM_METRICS)
def test_rule3_missing_value_carries_forward(steady_raw, contract, metric):
    (f0,), state = run(steady_raw[:1], contract)
    prev = getattr(f0.services[D], {"cpu": "cpu_cores", "thr": "throttle", "mem": "mem_bytes"}[metric])
    assert math.isfinite(prev)
    f1, _ = impute(drop_prom(steady_raw[1], metric, D), state, contract)
    got = getattr(f1.services[D], {"cpu": "cpu_cores", "thr": "throttle", "mem": "mem_bytes"}[metric])
    assert got == prev
    assert f1.stale and f"prom/{metric}/{D}" in f1.imputed


def test_rule3_nan_is_missing(steady_raw, contract):
    """0/0 throttle comes back from Prometheus as NaN and must be treated as missing."""
    (f0,), state = run(steady_raw[:1], contract)
    f1, _ = impute(drop_prom(steady_raw[1], "thr", D, float("nan")), state, contract)
    assert f1.services[D].throttle == f0.services[D].throttle and f1.stale


def test_rule4_locf_expires_after_locf_max_ticks(steady_raw, contract):
    """A value may be carried locf_max_ticks times; after that it becomes 0 (still stale)."""
    n_missing = contract.telemetry.locf_max_ticks + 1
    (f0,), state = run(steady_raw[:1], contract)
    gapped = [drop_prom(r, "cpu", D) for r in steady_raw[1:1 + n_missing]]
    features, state = run(gapped, contract, state)
    carried = [f.services[D].cpu_cores for f in features]
    assert carried[:-1] == [f0.services[D].cpu_cores] * contract.telemetry.locf_max_ticks
    assert carried[-1] == 0.0
    assert all(f.stale for f in features)
    # the next real value is used again and clears staleness for that key
    f_back, _ = impute(steady_raw[1 + n_missing], state, contract)
    assert f"prom/cpu/{D}" not in f_back.imputed


def test_rule4_first_tick_without_history_is_zero(steady_raw, contract):
    f, _ = impute(drop_prom(steady_raw[0], "mem", D), ImputeState(), contract)
    assert f.services[D].mem_bytes == 0.0 and f.stale


# ----------------------------------------------------------------------------- rules 1 and 5

def test_rule1_no_pods_is_true_zero_not_stale(steady_raw, contract):
    """status.replicas == 0 (F3): series vanish; cpu/thr/mem are true zeros, not imputation."""
    target = "cartservice"
    raw = steady_raw[1]
    items = dict(raw.deployments.items)
    items[target] = dataclasses.replace(items[target], spec_replicas=0, status_replicas=0, available_replicas=0)
    for m in PROM_METRICS:
        raw = drop_prom(raw, m, target)
    raw = dataclasses.replace(raw, deployments=dataclasses.replace(raw.deployments, items=items))
    _, state = run(steady_raw[:1], contract)
    f, _ = impute(raw, state, contract)
    s = f.services[target]
    assert (s.cpu_cores, s.throttle, s.mem_bytes) == (0.0, 0.0, 0.0)
    assert not any(k.endswith(f"/{target}") for k in f.imputed)
    unmodified, _ = impute(steady_raw[1], state, contract)
    assert f.stale == unmodified.stale      # zero pods adds no staleness of its own


@pytest.mark.parametrize(("bump", "want"), [(2, 2.0), (-1, 0.0)])
def test_rule5_restart_delta(steady_raw, contract, bump, want):
    """Delta = increase of the summed restartCount; a decrease (pod replaced) gives 0."""
    _, state = run(steady_raw[:1], contract)
    raw = steady_raw[1]
    restarts = dict(raw.pods.restarts)
    restarts[D] = state.prev_restart_sum[D] + bump
    raw = dataclasses.replace(raw, pods=dataclasses.replace(raw.pods, restarts=restarts))
    f, _ = impute(raw, state, contract)
    assert f.services[D].restarts_delta == want


def test_first_tick_restart_delta_is_zero(steady_raw, contract):
    f, _ = impute(steady_raw[0], ImputeState(), contract)
    assert all(s.restarts_delta == 0.0 for s in f.services.values())


# ----------------------------------------------------------------------------- rules 6 and 7

@pytest.mark.parametrize("locust", [LocustResult(False, error_type="ConnectTimeout"),
                                    LocustResult(True, n=0)])
def test_rule6_locust_down_carries_forward(steady_raw, contract, locust):
    (f0,), state = run(steady_raw[:1], contract)
    f1, _ = impute(dataclasses.replace(steady_raw[1], locust=locust), state, contract)
    assert (f1.p99_ms, f1.fail_ratio, f1.rps) == (f0.p99_ms, f0.fail_ratio, f0.rps)
    assert f1.stale


def test_rule7_whole_prometheus_failure(steady_raw, contract):
    (f0,), state = run(steady_raw[:1], contract)
    raw = steady_raw[1]
    failed = {m: PromResult(False, m, raw.t_to_wall, error_type="deadline") for m in PROM_METRICS}
    f1, _ = impute(dataclasses.replace(raw, prom=failed), state, contract)
    for d in contract.cluster.managed:
        assert f1.services[d].cpu_cores == f0.services[d].cpu_cores
        assert f1.services[d].throttle == f0.services[d].throttle
    assert f1.stale and set(PROM_METRICS) <= set(f1.failed_sources)


def test_rule7_kube_failure(steady_raw, contract):
    (f0,), state = run(steady_raw[:1], contract)
    raw = dataclasses.replace(steady_raw[1], deployments=DeploymentsResult(False, error_type="ReadTimeoutError"),
                              pods=PodsResult(False, error_type="ReadTimeoutError"))
    f1, _ = impute(raw, state, contract)
    for d in contract.cluster.managed:
        assert f1.services[d].spec_replicas == f0.services[d].spec_replicas
        assert f1.services[d].available_replicas == f0.services[d].available_replicas
    assert f1.stale and {"deployments", "pods"} <= set(f1.failed_sources)


# ----------------------------------------------------------------------------- generation tracking

def test_rollout_recency_counts_ticks_since_generation_change(steady_raw, contract):
    _, state = run(steady_raw[:1], contract)
    assert state.ticks_since_gen_change[D] is None           # no change since episode start
    raws = steady_raw[1:4]
    bumped = []
    for r in raws:
        items = dict(r.deployments.items)
        items[D] = dataclasses.replace(items[D], generation=items[D].generation + 1)
        bumped.append(dataclasses.replace(r, deployments=dataclasses.replace(r.deployments, items=items)))
    features, _ = run(bumped, contract, state)
    assert [f.services[D].ticks_since_gen_change for f in features] == [0, 1, 2]
