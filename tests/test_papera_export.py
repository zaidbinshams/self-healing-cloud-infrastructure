"""eval/papera_export.py pure helpers on hand-written rows."""

from __future__ import annotations

from eval.papera_export import sig4, summary


def row(policy, fault, recovered, mttr, actions=1, fr=False):
    return {"policy": policy, "fault": fault, "recovered": recovered, "mttr_censored_s": mttr, "sla_penalty": 1.0,
            "actions": actions, "false_remediation": fr}


def test_summary_censored_and_recovered_medians_and_pooled_row():
    rows = [row("a", "F1", True, 60.0), row("a", "F1", False, 360.0), row("a", "F1", True, 80.0),
            row("a", "NULL", False, 360.0, actions=0)]
    out = {(r["policy"], r["fault"]): r for r in summary(rows)}
    assert out[("a", "F1")]["recovered"] == 2
    assert out[("a", "F1")]["mttr_median_censored_s"] == 80.0
    assert out[("a", "F1")]["mttr_median_recovered_s"] == 70.0
    assert out[("a", "NULL")]["null_with_action"] == 0
    assert out[("a", "F1-F4")]["n"] == 3


def test_sig4_keeps_small_p_values():
    assert sig4(6.386123e-05) == 6.386e-05 and sig4(79.0566) == 79.06 and sig4(3) == 3
