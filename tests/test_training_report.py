"""scripts/training_report.py summaries on hand-written rows (pure)."""

from __future__ import annotations

from scripts.training_report import probe_table, summarize


def row(fault, recovered=False, mttr=None, actions=0, ret=-1.0, interrupted=False, after=None):
    r = {"fault": fault, "recovered": recovered, "mttr_s": mttr, "actions": actions, "return": ret,
         "interrupted": interrupted, "alpha": 0.05}
    if after is not None:
        r["after_train_episodes"] = after
    return r


def test_summarize_counts_and_rates():
    s = summarize([row("F1", True, 80.0), row("F2", False), row("NULL", actions=1), row("F3", True, 60.0),
                   row("F4", True, 100.0, interrupted=True)])
    assert s["episodes"] == 4 and s["fault_episodes"] == 3 and s["recovered"] == 2
    assert s["mttr_median_s"] == 70.0 and s["null_with_action"] == 1 and s["null_episodes"] == 1


def test_probe_table_groups_by_training_episode():
    pt = probe_table([row("F1", True, 80.0, after=0), row("NULL", after=0), row("F2", True, 60.0, after=75)])
    assert [p["after_train_episodes"] for p in pt] == [0, 75] and pt[1]["recovered"] == 1
