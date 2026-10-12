"""eval/make_schedule.py: counts, balance, determinism, edge subset (pure)."""

from __future__ import annotations

from collections import Counter

from env.contract import load_contract
from eval.make_schedule import build_schedule, edge_subset

C = load_contract()


def test_schedule_counts_and_balance():
    s = build_schedule(C, 2026, 10)
    assert len(s) == 50 and [e["index"] for e in s] == list(range(50))
    assert Counter(e["fault"] for e in s) == {f: 10 for f in ("F1", "F2", "F3", "F4", "NULL")}
    f2 = Counter((e["target"], e["severity"]) for e in s if e["fault"] == "F2")
    assert len(f2) == 4 and max(f2.values()) - min(f2.values()) <= 1
    for f in ("F3", "F4"):
        assert sorted(Counter((e["target"], e["severity"]) for e in s if e["fault"] == f).values()) == [5, 5]
    lo, hi = C.episode.lead_in_ticks
    assert all(lo <= e["lead_in"] <= hi for e in s)
    assert all(e["target"] is None and e["severity"] is None for e in s if e["fault"] == "NULL")


def test_schedule_is_deterministic_and_seed_dependent():
    assert build_schedule(C, 2026, 10) == build_schedule(C, 2026, 10)
    assert build_schedule(C, 2026, 10) != build_schedule(C, 2027, 10)


def test_edge_subset_takes_first_four_of_each_fault():
    s = build_schedule(C, 2026, 10)
    idx = edge_subset(s, 4)
    assert len(idx) == 20 and idx == sorted(idx)
    assert Counter(s[i]["fault"] for i in idx) == {f: 4 for f in ("F1", "F2", "F3", "F4", "NULL")}
