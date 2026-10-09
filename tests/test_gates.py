"""Gate arithmetic (scripts/gates.py) on hand-written run records."""

from __future__ import annotations

from scripts.gates import g1, g2, g3, g4, g5, g6, g7


def tick(ep, k, t, *, stale=False, a=0, lat=1.0, p99=100.0, fail=0.0):
    return {"episode": ep, "tick": k, "t_wall": t, "stale": stale, "a_exec": a, "decision_latency_s": lat,
            "raw": {"features": {"latency_ms": p99, "fail_ratio": fail}}}


def test_g1_jitter_and_latency():
    ep = [tick(1, k, 20.0 * k + (0.1 if k % 2 else 0.0)) for k in range(10)]
    assert g1([ep], 20.0).ok
    slow = [tick(1, k, 20.0 * k, lat=5.0) for k in range(10)]
    assert not g1([slow], 20.0).ok


def test_g2_stale_share():
    ok = [[tick(1, k, k, stale=(k == 0)) for k in range(200)]]      # 0.5%
    bad = [[tick(1, k, k, stale=(k < 3)) for k in range(200)]]      # 1.5%
    assert g2(ok).ok and not g2(bad).ok


def test_g3_recovery_rate():
    eps = [{"episode": i, "fault": "F1", "recovered": i != 0} for i in range(20)]   # 95%
    assert g3(eps).ok
    assert not g3(eps + [{"episode": 99, "fault": "F2", "recovered": False}]).ok
    assert g3(eps + [{"episode": 98, "fault": "NULL", "recovered": None}]).ok       # NULL not counted


def test_g4_no_actions_in_null():
    eps = [{"episode": 1, "fault": "NULL"}, {"episode": 2, "fault": "F3"}]
    assert g4(eps, [[tick(1, 0, 0)], [tick(2, 0, 0, a=10)]]).ok
    assert not g4(eps, [[tick(1, 0, 0, a=6)]]).ok


def test_g5_reset_success():
    ev = [{"event": "reset_done"}] * 50 + [{"event": "reset_hard"}]
    assert g5(ev).ok
    assert not g5(ev + [{"event": "reset_hard"}]).ok


def test_g6_cv_and_null_breaches():
    eps = [{"episode": 1, "fault": "NULL"}]
    calm = [[tick(1, k, k, p99=100.0) for k in range(100)]]
    assert g6({"g6": {"cv": 0.2}}, eps, calm, 200.0, 0.01).ok
    assert not g6({"g6": {"cv": 0.3}}, eps, calm, 200.0, 0.01).ok
    breachy = [[tick(1, k, k, p99=300.0 if k < 3 else 100.0) for k in range(100)]]   # 3%
    assert not g6({"g6": {"cv": 0.2}}, eps, breachy, 200.0, 0.01).ok


def test_g7_requires_every_f2_severity(contract):
    f4 = {"fault": "F4", "g7_f4_bottleneck_ok": True}
    f2 = [{"fault": "F2", "severity": w, "g7_f2_throttle_ok": True} for w in contract.episode.f2_workers]
    assert g7([f4, *f2], contract).ok
    assert not g7([f4, f2[-1]], contract).ok                       # a severity not smoke-tested
    assert not g7([{**f4, "g7_f4_bottleneck_ok": False}, *f2], contract).ok
