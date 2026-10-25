"""Paper B figures from data/paperB/analysis.json and the recorded diag runs (print, light surface).

Palette: the first three validated categorical slots (all-pairs CVD-safe), marker shapes as secondary
encoding, direct labels on every series, recessive grid, one y-axis per panel.

Usage:  python -m eval.paperb_analysis && python -m eval.paperb_figures [--out paper_b/latex/figures]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from env.contract import REPO_ROOT
from eval.paperb_analysis import DATA, read_jsonl

SURFACE = "#fcfcfb"
INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
COL_W_IN = 3.4                          # single IEEE/ACM column
TREATMENT_A_RUN = "diag/latency-treatA-u40.jsonl"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.5, "pdf.fonttype": 42,
})


def _save(fig: plt.Figure, out: Path, name: str) -> None:
    if not fig.get_layout_engine():
        fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", dpi=300)
    plt.close(fig)


def fig_decomposition(a: dict, out: Path) -> None:
    """Per-run CV of client vs server p99, and their correlation with network RTT (two panels)."""
    rows = a["A1_decomposition"]
    labels = [r["run"] for r in rows]
    y = list(range(len(rows)))[::-1]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(2 * COL_W_IN, 2.3), sharey=True)
    for ax, (kc, ks), title, xlab in ((ax1, ("cv_client_p99", "cv_server_p99"), "Tick-to-tick variability", "CV of tick p99"),
                                      (ax2, ("r_client_net", "r_server_net"), "Coupling to network RTT", "Pearson r with ping p99")):
        ax.scatter([r[kc] for r in rows], y, color=ORANGE, marker="s", s=22, zorder=3, label="client p99 (Locust)")
        ax.scatter([r[ks] for r in rows], y, color=BLUE, marker="o", s=22, zorder=3, label="server p99 (frontend logs)")
        ax.set_title(title, loc="left", color=INK)
        ax.set_xlabel(xlab)
    ax1.set_yticks(y, labels)
    ax2.axvline(0, color=MUTED, lw=0.8)
    h, lab = ax1.get_legend_handles_labels()
    fig.legend(h, lab, loc="upper center", ncol=2, frameon=False, fontsize=7, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    _save(fig, out, "b1_decomposition")


def fig_timeseries(out: Path) -> None:
    """After the runtime fix: client p99 follows the network, server p99 does not (one run, per tick)."""
    rows = [r for r in read_jsonl(DATA / TREATMENT_A_RUN) if not r.get("errors")]
    t = [r["tick"] for r in rows]
    series = [("client p99", [r["client"]["p99_ms"] for r in rows], ORANGE, "s"),
              ("ping p99 (B→A)", [r["net"]["p99_ms"] for r in rows], AQUA, "^"),
              ("server p99", [r["server"]["p99_ms"] for r in rows], BLUE, "o")]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.1))
    for name, ys, c, m in series:
        ax.plot(t, ys, color=c, marker=m, ms=3)
        ax.annotate(name, (t[-1], ys[-1]), xytext=(4, 0), textcoords="offset points", va="center", fontsize=7, color=INK_2)
    ax.set_xlabel("tick (20 s)")
    ax.set_ylabel("latency (ms)")
    ax.set_xlim(t[0], t[-1] + 7)
    _save(fig, out, "b2_timeseries_treatA")


def fig_estimator(a: dict, out: Path) -> None:
    """Sampling CV of the nearest-rank quantile vs expected tail samples k = n(1-q), log-log, with the fit."""
    a3 = a["A3_estimator_noise"]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.3))
    for q, c, m in ((0.90, AQUA, "^"), (0.95, BLUE, "o"), (0.99, ORANGE, "s")):
        cells = sorted((x for x in a3["cells"] if x["q"] == q), key=lambda x: x["k_tail"])
        ks, cvs = [x["k_tail"] for x in cells], [x["bootstrap_cv"] for x in cells]
        ax.plot(ks, cvs, color=c, marker=m, ms=4, lw=1.0)
        ax.annotate(f"p{round(q * 100)}", (ks[0], cvs[0]), xytext=(-4, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=7, color=INK_2)
    fit = a3["fit"]
    kk = [1.5, 300.0]
    ax.plot(kk, [fit["a"] * k ** fit["b"] for k in kk], color=MUTED, lw=0.9, ls="--")
    ax.annotate(f"fit: CV = {fit['a']:.2f}·k^{fit['b']:.2f}", (60, fit["a"] * 60 ** fit["b"]), xytext=(0, 6),
                textcoords="offset points", fontsize=7, color=INK_2)
    ax.axhline(0.10, color=GRID, lw=1.2, zorder=0)
    ax.set_xscale("log")
    ax.set_yscale("log")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        axis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_yticks([0.03, 0.05, 0.1, 0.2])
    ax.set_xlabel("expected tail samples per window, k = n(1−q)")
    ax.set_ylabel("sampling CV (bootstrap)")
    _save(fig, out, "b3_estimator_noise")


def fig_detectability(a: dict, out: Path) -> None:
    """Fault peak before remedy as a multiple of each signal's calibrated threshold (1 = threshold)."""
    rows = a["A5_detectability"]
    short = {"cartservice": "cart", "currencyservice": "currency"}
    labels = [f"F4 surge ×{r['severity']}" if r["fault"] == "F4" else f"F2 {short[r['target']]}, {r['severity']} worker"
              for r in rows]
    y = list(range(len(rows)))[::-1]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.2))
    ax.scatter([r["client p99"]["peak_over_threshold"] for r in rows], y, color=ORANGE, marker="s", s=24, zorder=3,
               label=f"client p99 (threshold {rows[0]['client p99']['threshold_ms']:.0f} ms)")
    ax.scatter([r["server p95"]["peak_over_threshold"] for r in rows], y, color=BLUE, marker="o", s=24, zorder=3,
               label=f"server p95 (threshold {rows[0]['server p95']['threshold_ms']:.0f} ms)")
    ax.axvline(1.0, color=INK_2, lw=0.9)
    ax.annotate("threshold", (1.0, max(y) + 0.45), xytext=(3, 0), textcoords="offset points", fontsize=7, color=INK_2)
    ax.set_yticks(y, labels)
    ax.set_xlabel("fault peak / calibrated threshold")
    ax.set_ylim(-0.6, max(y) + 0.8)
    fig.legend(loc="upper center", ncol=1, frameon=False, fontsize=7, bbox_to_anchor=(0.55, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.80))
    _save(fig, out, "b4_detectability")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, default=DATA / "paperB" / "analysis.json")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "paper_b" / "latex" / "figures")
    args = ap.parse_args(argv)
    a = json.loads(args.analysis.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    fig_decomposition(a, args.out)
    fig_timeseries(args.out)
    fig_estimator(a, args.out)
    fig_detectability(a, args.out)
    print(f"wrote figures to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
