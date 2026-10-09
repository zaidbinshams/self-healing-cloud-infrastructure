"""BoutiqueEnv episode-ending logic (§5.6): termination, truncation, NULL horizon, MTTR, phase.

The env is constructed without touching the cluster (Kubernetes clients make no calls until used);
only the pure bookkeeping methods are exercised. TEST calibration constants, not calibrated values.
"""

from __future__ import annotations

import pytest
from kubernetes import client

from env.boutique_env import BoutiqueEnv, EpisodeState
from env.contract import Calibration
from env.golden import load_golden
from env.injector import FaultPlan
from env.recorder import EventLog

CAL_TEST = Calibration(l_sla_ms=500.0, rps_base=50.0, u_base=40, calibrated_at="test", env_git_sha="test")


@pytest.fixture
def env(contract, limits, tmp_path):
    api = client.ApiClient(client.Configuration())
    e = BoutiqueEnv(contract=contract, calibration=CAL_TEST, golden=load_golden(contract), limits=limits,
                    agent_api=api, controller_api=api, prom_url="http://prom.invalid", locust_url="http://loc.invalid",
                    run="test", seed=0, events=EventLog(None), transitions_root=tmp_path)
    yield e
    e.close()


def anchored(env):
    """Give the clock deterministic boundaries so wall(T_t) is defined."""
    env.clock.anchor()
    for k in range(1, 40):
        env.clock._walls[k] = env.clock.wall(0) + k * env.c.clock.tick_s
    return env.clock.wall(0)


def test_fault_episode_terminates_on_recovery_with_mttr(env):
    w0 = anchored(env)
    ep = env.ep = EpisodeState(1, FaultPlan("F3", "cartservice", None, lead_in=2))
    ep.k_inject, ep.t_inject_wall = 2, w0 + 2 * 20.0 + 0.5
    #          ticks: 0     1     2      3      4     5     6
    ep.health = [True, True, False, False, True, True, True]
    ep.cured = [False, False, False, False, True, True, True]
    term, trunc, mttr = env._done_flags(6)
    assert (term, trunc) == (True, False) and ep.recovered_at == 4
    assert mttr == pytest.approx(4 * 20.0 - (2 * 20.0 + 0.5))          # wall(T_4) - t_inject
    assert env._phase(6) == "post" and env._phase(1) == "lead_in"


def test_fault_episode_truncates_after_fault_max_ticks(env):
    anchored(env)
    ep = env.ep = EpisodeState(1, FaultPlan("F2", "cartservice", 2, lead_in=3))
    ep.k_inject, ep.t_inject_wall = 3, 1.0
    n = 3 + env.c.episode.fault_max_ticks + 1
    ep.health, ep.cured = [False] * n, [False] * n
    assert env._done_flags(3 + env.c.episode.fault_max_ticks - 1) == (False, False, None)
    assert env._done_flags(3 + env.c.episode.fault_max_ticks) == (False, True, None)
    assert env._phase(5) == "fault"


def test_null_episode_never_terminates_and_truncates_at_horizon(env):
    anchored(env)
    env.ep = EpisodeState(1, FaultPlan("NULL", None, None, lead_in=4))
    env.ep.health, env.ep.cured = [True] * 20, [True] * 20
    horizon = 4 + env.c.episode.null_extra_ticks
    assert env._done_flags(horizon - 1) == (False, False, None)
    assert env._done_flags(horizon) == (False, True, None)


def test_truth_only_in_info(env):
    env.ep = EpisodeState(1, FaultPlan("F1", "productcatalogservice", "600ms", lead_in=2))
    truth = env._truth()
    assert truth == {"fault": "F1", "target": "productcatalogservice", "severity": "600ms", "lead_in": 2}
    assert env.observation_space["obs"].shape == (36,) and env.action_space.n == 12
