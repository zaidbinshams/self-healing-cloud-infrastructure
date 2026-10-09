"""Scripted runbook rules R0-R6 (CLAUDE.md §5.11) on hand-written observations.

Each scenario builds the observation pattern a fault produces; the runbook never sees the fault.
TEST calibration constants (L_SLA 500 ms, RPS_base 50).
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from agents.runbook import Runbook, read_obs
from env.contract import Calibration
from env.k8s_actions import compute_mask
from env.telemetry import norm_latency

CAL = Calibration(l_sla_ms=500.0, rps_base=50.0, u_base=40, calibrated_at="test", env_git_sha="test")
BASE = {"frontend": 1, "cartservice": 1, "currencyservice": 1, "productcatalogservice": 1}


def make_obs(contract, *, latency_ms=100.0, fail=0.0, rps=50.0, throttle=None, spec=None, since=None,
             in_flight=False):
    o = np.zeros(36, dtype=np.float32)
    o[0], o[1], o[2] = norm_latency(latency_ms, CAL.l_sla_ms), fail, min(1.0, rps / (3 * CAL.rps_base))
    spec = {**BASE, **(spec or {})}
    for i, d in enumerate(contract.cluster.managed):
        b = 5 + 7 * i
        o[b + 1] = (throttle or {}).get(d, 0.0)
        o[b + 4] = spec[d] / contract.replicas.max[d]
        t = (since or {}).get(d)
        o[b + 6] = 0.0 if t is None else math.exp(-t / 5)
    o[33] = 1.0 if in_flight else 0.0
    return o, compute_mask(spec, lock_held=in_flight, contract=contract)


def run(rb, seq):
    out = None
    for obs, mask in seq:
        out = rb.act(obs, mask)
    return out


def test_read_obs_roundtrip(contract):
    o, _ = make_obs(contract, latency_ms=400, rps=75, throttle={"cartservice": 0.7}, spec={"frontend": 2},
                    since={"productcatalogservice": 3})
    v = read_obs(o, contract, CAL)
    assert v.healthy and v.rps == pytest.approx(75, rel=1e-5) and v.spec["frontend"] == 2
    assert v.throttle["cartservice"] == pytest.approx(0.7) and v.ticks_since_gen_change["productcatalogservice"] == 3
    assert not read_obs(make_obs(contract, latency_ms=600)[0], contract, CAL).healthy


def test_r0_in_flight_noop(contract):
    rb = Runbook(contract, CAL)
    assert rb.act(*make_obs(contract, spec={"cartservice": 0}, in_flight=True))[:2] == (0, "R0")


def test_r1_scaled_to_zero_restores(contract):                       # F3 pattern
    rb = Runbook(contract, CAL)
    assert rb.act(*make_obs(contract, latency_ms=2000, spec={"cartservice": 0}))[:2] == (10, "R1")


def test_r2_restores_unexplained_recent_change(contract):            # F1 pattern
    rb = Runbook(contract, CAL)
    bad = {"latency_ms": 1500}
    run(rb, [make_obs(contract, **bad, since={"productcatalogservice": 1})])
    assert rb.act(*make_obs(contract, **bad, since={"productcatalogservice": 2}))[:2] == (9, "R2")


def test_r2_ignores_changes_the_runbook_caused(contract):
    rb = Runbook(contract, CAL)
    rb.act(*make_obs(contract, latency_ms=1500, throttle={"cartservice": 0.8}))        # tick 0
    rb.my_actions.append((0, 2))                                                   # pretend: RESTART cart at tick 0
    rb.tick = 2
    _aid, rule = rb.decide(*make_obs(contract, latency_ms=1500, since={"cartservice": 2}))
    assert rule != "R2"


def test_r3_surge_scales_frontend(contract):                         # F4 pattern
    rb = Runbook(contract, CAL)
    surge = {"latency_ms": 1500, "rps": 120, "throttle": {"frontend": 0.6}}
    run(rb, [make_obs(contract, **surge)])
    assert rb.act(*make_obs(contract, **surge))[:2] == (5, "R3")
    rb2 = Runbook(contract, CAL)
    run(rb2, [make_obs(contract, **surge, spec={"frontend": 3})])
    assert rb2.act(*make_obs(contract, **surge, spec={"frontend": 3}))[0] != 5      # at max: no scale up


def test_r4_hot_pod_restarts_with_cooldown(contract):                # F2 pattern
    rb = Runbook(contract, CAL)
    hot = {"latency_ms": 1500, "rps": 50, "throttle": {"cartservice": 0.8, "currencyservice": 0.5}}
    run(rb, [make_obs(contract, **hot)])
    assert rb.act(*make_obs(contract, **hot))[:2] == (2, "R4")                     # argmax throttle
    assert rb.act(*make_obs(contract, **hot))[0] != 2                              # cooldown


def test_r5_scales_back_after_healthy_period(contract):
    rb = Runbook(contract, CAL)
    calm = make_obs(contract, spec={"frontend": 2})
    for _ in range(contract.runbook.scaleback_healthy_ticks - 1):
        rb.act(*calm)
    assert rb.act(*calm)[:2] == (6, "R5")


def test_r6_null_is_noop(contract):
    rb = Runbook(contract, CAL)
    for _ in range(10):
        assert rb.act(*make_obs(contract))[:2] == (0, "R6")


def test_epsilon_takes_valid_random_actions(contract):
    rb = Runbook(contract, CAL, epsilon=1.0, rng=random.Random(3))
    obs, mask = make_obs(contract, spec={"frontend": 3})
    for _ in range(50):
        aid, _rule, source = rb.act(obs, mask)
        assert mask[aid] == 1 and source == "eps"
