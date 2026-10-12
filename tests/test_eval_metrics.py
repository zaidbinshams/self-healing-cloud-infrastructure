"""eval/metrics.py on tiny hand-written transition records (pure)."""

from __future__ import annotations

import pytest

from env.contract import Calibration, load_contract
from eval.metrics import censor_s, episode_metrics

C = load_contract()
CAL = Calibration(l_sla_ms=70.0, rps_base=34.0, u_base=30, calibrated_at="t", env_git_sha="x")


def rec(tick, *, a_chosen=0, a_exec=0, lat=20.0, fail=0.0, fault="F2", valid=True, exec_error=False, stale=False,
        truncated=False, reward=-0.0):
    return {"tick": tick, "fault": fault, "target": "cartservice", "severity": 1, "a_chosen": a_chosen,
            "a_exec": a_exec, "valid": valid, "exec_error": exec_error, "stale": stale, "truncated": truncated,
            "reward": reward, "decision_latency_s": 0.5,
            "raw": {"features": {"latency_ms": lat, "fail_ratio": fail}}}


def test_recovered_episode_mttr_and_penalty():
    rs = [rec(0), rec(1, lat=140.0), rec(2, a_chosen=2, a_exec=2, lat=140.0), rec(3), rec(4), rec(5)]
    m = episode_metrics(rs, {"recovered": True, "mttr_s": 40.0}, "runbook", 3, CAL, C)
    assert m.recovered and m.mttr_s == 40.0 and m.mttr_censored_s == 40.0 and m.event_observed
    assert m.actions == 1 and not m.false_remediation and m.mask_violations == 0
    assert m.sla_penalty == pytest.approx(2 * (1 / 3))           # log2(140/70)/3 = 1/3 per breached tick


def test_unrecovered_episode_is_censored():
    m = episode_metrics([rec(0, lat=200.0)], {"recovered": False, "mttr_s": None}, "k8s_default", 0, CAL, C)
    assert not m.recovered and m.mttr_s is None and not m.event_observed and m.mttr_censored_s == censor_s(C)


def test_null_episode_action_is_false_remediation_and_mask_violation_counted():
    rs = [rec(0, fault="NULL", a_chosen=5, a_exec=5), rec(1, fault="NULL", a_chosen=6, a_exec=0)]
    m = episode_metrics(rs, {"recovered": False}, "sac_per", 7, CAL, C)
    assert m.false_remediation and m.actions == 1 and m.mask_violations == 1 and not m.recovered


def test_exec_error_and_missed_tick_are_not_mask_violations():
    rs = [rec(0, a_chosen=5, a_exec=0, exec_error=True), rec(1, a_chosen=5, a_exec=0, valid=False)]
    m = episode_metrics(rs, {"recovered": False}, "sac_per", 1, CAL, C)
    assert m.mask_violations == 0 and m.exec_errors == 1 and not m.valid
