"""§5.3 observation normalization on recorded real ticks (tests/fixtures/ticks_steady.jsonl).

calibration.json does not exist yet in M3 step 1, so L_SLA and RPS_base below are fixed TEST
constants (human-approved 2026-10-02), not calibrated values. Limits come from the real
golden_live.json.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from env.contract import OBS_DIM
from env.telemetry import (
    IDX_IN_FLIGHT,
    IDX_STALE,
    IDX_TICKS_SINCE_ACTION,
    ImputeState,
    TelemetryError,
    build_obs,
    impute,
    norm_p99,
)

L_SLA_MS_TEST = 500.0
RPS_BASE_TEST = 50.0


def svc_index(contract, d: str, j: int) -> int:
    return 5 + 7 * contract.cluster.managed.index(d) + j


@pytest.fixture(scope="module")
def steady_features(steady_raw, contract):
    state, out = ImputeState(), []
    for raw in steady_raw:
        f, state = impute(raw, state, contract)
        out.append(f)
    return out


def obs_for(f, prev, contract, limits, *, in_flight=False, ticks_since_action=None):
    return build_obs(f, prev, contract=contract, limits=limits, l_sla_ms=L_SLA_MS_TEST, rps_base=RPS_BASE_TEST,
                     in_flight=in_flight, ticks_since_action=ticks_since_action)


def test_every_recorded_tick_gives_a_valid_observation(steady_features, contract, limits):
    prev = None
    for f in steady_features:
        obs = obs_for(f, prev, contract, limits)
        assert obs.shape == (OBS_DIM,) and obs.dtype == np.float32
        assert np.all(np.isfinite(obs)) and np.all(obs >= -1.0) and np.all(obs <= 1.0)
        assert obs[IDX_STALE] == (1.0 if f.stale else 0.0)
        prev = f


def test_global_features_match_formulas(steady_features, contract, limits):
    f0, f1 = steady_features[0], steady_features[1]
    obs0, obs1 = obs_for(f0, None, contract, limits), obs_for(f1, f0, contract, limits)
    p99 = min(1.0, math.log2(1 + f1.p99_ms / L_SLA_MS_TEST) / math.log2(21))
    assert obs1[0] == pytest.approx(p99, abs=1e-6)
    assert obs1[1] == pytest.approx(f1.fail_ratio, abs=1e-6)
    assert obs1[2] == pytest.approx(min(1.0, f1.rps / (3 * RPS_BASE_TEST)), abs=1e-6)
    assert obs0[3] == 0.0 and obs0[4] == 0.0                       # first tick of the episode
    assert obs1[3] == pytest.approx(obs1[0] - obs0[0], abs=1e-6)
    assert obs1[4] == pytest.approx(obs1[1] - obs0[1], abs=1e-6)


@pytest.mark.parametrize(("p99_ms", "want"), [(0.0, 0.0), (L_SLA_MS_TEST, math.log2(2) / math.log2(21)),
                                              (20 * L_SLA_MS_TEST, 1.0), (1e9, 1.0)])
def test_p99_scale(p99_ms, want):
    assert norm_p99(p99_ms, L_SLA_MS_TEST) == pytest.approx(want)


def test_service_features_match_formulas(steady_features, contract, limits):
    f = steady_features[-1]
    obs = obs_for(f, steady_features[-2], contract, limits)
    for d in contract.cluster.managed:
        s, lim = f.services[d], limits[d]
        pods = max(1.0, s.status_replicas)
        assert obs[svc_index(contract, d, 0)] == pytest.approx(min(1.0, s.cpu_cores / (lim.cpu_cores * pods)), abs=1e-6)
        assert obs[svc_index(contract, d, 1)] == pytest.approx(min(1.0, s.throttle), abs=1e-6)
        assert obs[svc_index(contract, d, 2)] == pytest.approx(s.mem_bytes / (lim.memory_bytes * pods), abs=1e-6)
        assert obs[svc_index(contract, d, 3)] == pytest.approx(min(1.0, s.available_replicas / contract.replicas.base[d]))
        assert obs[svc_index(contract, d, 4)] == pytest.approx(s.spec_replicas / contract.replicas.max[d])
    # frontend max is 3 replicas: one replica reads as 1/3
    assert obs[svc_index(contract, "frontend", 4)] == pytest.approx(1 / 3)


def test_cpu_util_uses_live_replicas_and_golden_limit(steady_features, contract, limits):
    f = steady_features[-1]
    d = "frontend"
    two = dataclasses.replace(f, services={**f.services, d: dataclasses.replace(f.services[d], status_replicas=2.0)})
    one_obs, two_obs = obs_for(f, None, contract, limits), obs_for(two, None, contract, limits)
    assert two_obs[svc_index(contract, d, 0)] == pytest.approx(one_obs[svc_index(contract, d, 0)] / 2, abs=1e-6)
    assert limits[d].cpu_cores == pytest.approx(0.4)        # golden_overrides frontend 400m


@pytest.mark.parametrize(("delta", "want"), [(0.0, 0.0), (1.0, 1 / 3), (3.0, 1.0), (7.0, 1.0)])
def test_restarts_capped_at_three(steady_features, contract, limits, delta, want):
    f = steady_features[-1]
    d = "cartservice"
    g = dataclasses.replace(f, services={**f.services, d: dataclasses.replace(f.services[d], restarts_delta=delta)})
    assert obs_for(g, None, contract, limits)[svc_index(contract, d, 5)] == pytest.approx(want)


@pytest.mark.parametrize(("ticks", "want"), [(None, 0.0), (0, 1.0), (5, math.exp(-1)), (20, math.exp(-4))])
def test_rollout_recency(steady_features, contract, limits, ticks, want):
    f = steady_features[-1]
    d = "currencyservice"
    g = dataclasses.replace(f, services={**f.services, d: dataclasses.replace(f.services[d],
                                                                              ticks_since_gen_change=ticks)})
    assert obs_for(g, None, contract, limits)[svc_index(contract, d, 6)] == pytest.approx(want, abs=1e-6)


@pytest.mark.parametrize(("ticks_since_action", "want"), [(None, 1.0), (0, 0.0), (3, 0.3), (15, 1.0)])
def test_env_features(steady_features, contract, limits, ticks_since_action, want):
    obs = obs_for(steady_features[-1], None, contract, limits, in_flight=True, ticks_since_action=ticks_since_action)
    assert obs[IDX_IN_FLIGHT] == 1.0
    assert obs[IDX_TICKS_SINCE_ACTION] == pytest.approx(want)


def test_non_finite_feature_raises(steady_features, contract, limits):
    """Rule 8: a NaN must never reach the observation."""
    bad = dataclasses.replace(steady_features[-1], p99_ms=float("nan"))
    with pytest.raises((TelemetryError, ValueError)):
        obs_for(bad, None, contract, limits)
