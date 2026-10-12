"""eval/stats.py against hand-checkable cases (pure)."""

from __future__ import annotations

import numpy as np
import pytest

from eval.stats import (
    bootstrap_median_diff,
    cliffs_delta,
    compare,
    holm,
    kaplan_meier,
    logrank,
    non_inferior,
)


def test_cliffs_delta_extremes_and_tie():
    assert cliffs_delta([1, 2], [3, 4]) == 1.0
    assert cliffs_delta([3, 4], [1, 2]) == -1.0
    assert cliffs_delta([1, 2], [1, 2]) == 0.0


def test_holm_matches_hand_computation():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])


def test_bootstrap_ci_contains_true_difference():
    rng = np.random.default_rng(0)
    lo, hi = bootstrap_median_diff(rng.normal(60, 5, 50), rng.normal(100, 5, 50), seed=1)
    assert lo < -40 + 5 and hi > -40 - 5 and hi < 0


def test_compare_and_non_inferiority():
    g = {"sac": [60.0] * 10, "runbook": [80.0] * 10, "k8s": [360.0] * 10}
    cs = compare(g, [("sac", "k8s"), ("sac", "runbook")], seed=0)
    assert cs[0].p_holm < 0.05 and cs[0].cliffs_delta == 1.0
    assert non_inferior(cs[1])


def test_kaplan_meier_with_censoring():
    t, s = kaplan_meier([40, 60, 60, 360], [True, True, True, False])
    assert t.tolist() == [40, 60] and s.tolist() == pytest.approx([0.75, 0.25])


def test_logrank_separates_fast_from_never():
    p = logrank([40] * 8, [True] * 8, [360] * 8, [False] * 8)
    assert p < 0.001
