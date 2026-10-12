"""eval/run_eval.py pure helpers: interleaving order and resume bookkeeping."""

from __future__ import annotations

from eval.run_eval import done_pairs, policy_order

P = ["k8s_default", "k8s_hpa", "runbook", "sac_per", "sac_per_cold"]


def test_policy_order_is_a_reproducible_permutation_that_varies_by_entry():
    assert sorted(policy_order(P, 3, 2026)) == sorted(P)
    assert policy_order(P, 3, 2026) == policy_order(P, 3, 2026)
    assert len({tuple(policy_order(P, i, 2026)) for i in range(20)}) > 5


def test_done_pairs_ignores_invalid_and_interrupted_rows():
    rows = [{"index": 0, "policy": "runbook", "valid": True},
            {"index": 1, "policy": "runbook", "valid": False},
            {"index": 2, "policy": "sac_per", "valid": True, "interrupted": True}]
    assert done_pairs(rows) == {(0, "runbook")}
