"""scripts/steady_check.py rule (pure), on hand-written tick values."""

from __future__ import annotations

from scripts.steady_check import evaluate


def test_pass_on_calibration_like_ticks():
    v = evaluate([20.0] * 25 + [24.0] * 5, 0, cal_median_ms=24.0, cal_q95_ms=41.0, l_sla_ms=70.0)
    assert v.passed and v.median_ok and v.q95_ok and v.breach_ok


def test_fail_on_slow_cluster():
    v = evaluate([69.0] * 15 + [80.0] * 15, 0, cal_median_ms=24.0, cal_q95_ms=41.0, l_sla_ms=70.0)
    assert not v.passed and not v.median_ok and not v.q95_ok and not v.breach_ok


def test_single_breach_in_thirty_fails_the_two_percent_rule():
    v = evaluate([22.0] * 29 + [75.0], 0, cal_median_ms=24.0, cal_q95_ms=41.0, l_sla_ms=70.0)
    assert v.median_ok and v.q95_ok and not v.breach_ok and not v.passed
