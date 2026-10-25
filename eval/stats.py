"""Pre-registered statistics (docs/PREREGISTRATION.md §5). Pure: per-episode metric rows in, tests out.

    mann_whitney        two-sided Mann–Whitney U on MTTR (censored at the fault window)
    holm                Holm step-down adjustment within a family of comparisons
    cliffs_delta        effect size δ = P(x < y) − P(x > y)  (positive = x has lower values, e.g. lower MTTR)
    bootstrap_median_diff  percentile bootstrap 95 % CI of median(x) − median(y) (10 000 resamples)
    kaplan_meier        time-to-recovery survival curve with censoring
    logrank             two-group log-rank test (scipy.stats.logrank)
    non_inferior        H2/H5: upper CI bound of median difference < margin
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

BOOTSTRAP_RESAMPLES = 10_000
CI_LEVEL = 0.95
NI_MARGIN_S = 20.0                     # one tick (pre-registered H2/H5 margin)
ALPHA = 0.05


@dataclass(frozen=True)
class Comparison:
    a: str
    b: str
    n_a: int
    n_b: int
    median_a: float
    median_b: float
    u: float
    p: float
    p_holm: float
    cliffs_delta: float
    ci_low: float
    ci_high: float


def cliffs_delta(x: Sequence[float], y: Sequence[float]) -> float:
    xa, ya = np.asarray(x, float)[:, None], np.asarray(y, float)[None, :]
    return float(((xa < ya).sum() - (xa > ya).sum()) / (xa.size * ya.size))


def mann_whitney(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    r = stats.mannwhitneyu(x, y, alternative="two-sided", method="auto")
    return float(r.statistic), float(r.pvalue)


def holm(pvals: Sequence[float]) -> list[float]:
    """Holm–Bonferroni adjusted p-values, in the input order (monotone, capped at 1)."""
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj.tolist()


def bootstrap_median_diff(x: Sequence[float], y: Sequence[float], seed: int,
                          n: int = BOOTSTRAP_RESAMPLES) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    d = (np.median(xa[rng.integers(0, xa.size, (n, xa.size))], axis=1)
         - np.median(ya[rng.integers(0, ya.size, (n, ya.size))], axis=1))
    lo, hi = np.percentile(d, [100 * (1 - CI_LEVEL) / 2, 100 * (1 + CI_LEVEL) / 2])
    return float(lo), float(hi)


def compare(groups: dict[str, Sequence[float]], pairs: Sequence[tuple[str, str]], seed: int) -> list[Comparison]:
    """One family: Mann–Whitney per pair, Holm-adjusted together, with δ and bootstrap CIs."""
    raw = [mann_whitney(groups[a], groups[b]) for a, b in pairs]
    adj = holm([p for _, p in raw])
    out = []
    for k, ((a, b), (u, p)) in enumerate(zip(pairs, raw, strict=True)):
        lo, hi = bootstrap_median_diff(groups[a], groups[b], seed + k)
        out.append(Comparison(a, b, len(groups[a]), len(groups[b]), float(np.median(groups[a])),
                              float(np.median(groups[b])), u, p, adj[k], cliffs_delta(groups[a], groups[b]), lo, hi))
    return out


def non_inferior(c: Comparison, margin_s: float = NI_MARGIN_S) -> bool:
    """a is non-inferior to b on MTTR if the CI upper bound of median(a) − median(b) is below the margin."""
    return c.ci_high < margin_s


def kaplan_meier(times: Sequence[float], observed: Sequence[bool]) -> tuple[np.ndarray, np.ndarray]:
    """(event times, survival = P(not yet recovered)) as a right-continuous step function."""
    t = np.asarray(times, float)
    e = np.asarray(observed, bool)
    grid = np.unique(t[e])
    surv, s = [], 1.0
    for g in grid:
        at_risk = int((t >= g).sum())
        events = int(((t == g) & e).sum())
        s *= 1 - events / at_risk
        surv.append(s)
    return grid, np.asarray(surv)


def logrank(t_a: Sequence[float], e_a: Sequence[bool], t_b: Sequence[float], e_b: Sequence[bool]) -> float:
    x = stats.CensoredData.right_censored(np.asarray(t_a, float), ~np.asarray(e_a, bool))
    y = stats.CensoredData.right_censored(np.asarray(t_b, float), ~np.asarray(e_b, bool))
    return float(stats.logrank(x, y).pvalue)
