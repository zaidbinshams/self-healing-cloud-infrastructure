"""Paper A figures from eval/analyze.py output and training rows (print, light surface).

Fixed policy → colour mapping (colour follows the policy in every figure), validated 5-slot palette
(adjacent pairs), line styles/markers as secondary encoding, direct labels, one y-axis per panel.

    a1_km.pdf            pooled time-to-recovery (Kaplan–Meier) per policy over F1–F4
    a2_mttr_by_fault.pdf MTTR per fault type and policy (dots = episodes, bar = median; censored at 360 s)
    a3_training.pdf      training return (rolling mean) warm vs cold start
    a4_edge.pdf          median MTTR, main vs edge condition, runbook and PER-SAC
    a5_seed1.pdf         median MTTR, main block vs seed-1 block: PER-SAC seed 0 → 1, runbook main → anchor

Usage:  python -m eval.plots --analysis data/eval/analysis.json --main data/eval/main \\
            [--edge data/eval/edge] [--train sac_per=data/m4/sac_per_s0 ...] [--out paper_a/latex/figures]
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from env.contract import REPO_ROOT
from eval.analyze import FAULTS, by, final_valid, mttr, read_rows

SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
STYLE = {   # policy: (label, colour, linestyle, marker)
    "sac_per": ("PER-SAC (warm)", "#2a78d6", "-", "o"),
    "runbook": ("runbook", "#eb6834", "--", "s"),
    "sac_per_cold": ("PER-SAC (cold)", "#1baf7a", "-.", "^"),
    "k8s_hpa": ("Kubernetes + HPA", "#eda100", ":", "D"),
    "k8s_default": ("Kubernetes", "#e87ba4", (0, (5, 2, 1, 2)), "v"),
}
ORDER = list(STYLE)
SEED1 = ("sac_per_s1", ("PER-SAC (warm, seed 1)", STYLE["sac_per"][1], ":", "o"))   # same policy, same colour
COL_W_IN = 3.4
ROLL = 10
LABEL_GAP = 0.08                       # KM end labels, in survival units

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.5, "pdf.fonttype": 42,
})


def _save(fig: plt.Figure, out: Path, name: str, rect: tuple[float, float, float, float] = (0, 0, 1, 1)) -> None:
    fig.tight_layout(rect=rect)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", dpi=300)
    plt.close(fig)


def dodge(ys: list[float], gap: float) -> list[float]:
    """Label positions near ys with at least `gap` between neighbours (order preserved by value)."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in itertools.pairwise(order):
        out[b] = max(out[b], out[a] + gap)
    ceiling = max(ys) if ys else 0.0
    for a, b in itertools.pairwise(order[::-1]):             # then push back under the top value
        out[a] = min(out[a], ceiling)
        out[b] = min(out[b], out[a] - gap)
    return out


def fig_km(analysis: dict[str, Any], out: Path, censor_s: float) -> None:
    curves = analysis["km"]["curves"]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.4))
    ends = []
    for p in (p for p in ORDER if p in curves):
        label, c, ls, _ = STYLE[p]
        t = [0.0, *curves[p]["t"], censor_s]
        s = [1.0, *curves[p]["survival"]]
        s.append(s[-1])
        ax.step(t, s, where="post", color=c, ls=ls)
        ends.append((s[-1], label, c, ls))
    ys = dodge([e[0] for e in ends], LABEL_GAP)
    for y, (_, label, c, ls) in zip(ys, ends, strict=True):
        ax.plot([censor_s * 1.02, censor_s * 1.09], [y, y], color=c, ls=ls, lw=1.2, clip_on=False)
        ax.annotate(label, (censor_s * 1.10, y), xytext=(2, 0), textcoords="offset points", va="center",
                    fontsize=6.5, color=INK_2, annotation_clip=False)
    ax.set_xlim(0, censor_s)
    ax.set_ylim(-0.06, 1.06)
    ax.set_xlabel("time since injection (s)")
    ax.set_ylabel("P(not yet recovered)")
    _save(fig, out, "a1_km")


def fig_mttr_by_fault(rows: list[dict[str, Any]], out: Path, censor_s: float) -> None:
    policies = [p for p in ORDER if by(rows, p)]
    fig, axes = plt.subplots(1, len(FAULTS), figsize=(2 * COL_W_IN, 2.2), sharey=True)
    rng = np.random.default_rng(0)
    for ax, f in zip(axes, FAULTS, strict=True):
        for i, p in enumerate(policies):
            xs = mttr(by(rows, p, f))
            if not xs:
                continue
            y = i + rng.uniform(-0.15, 0.15, len(xs))
            ax.scatter(xs, y, s=8, color=STYLE[p][1], marker=STYLE[p][3], alpha=0.7, linewidths=0)
            ax.plot([np.median(xs)] * 2, [i - 0.32, i + 0.32], color=INK, lw=1.5)
        ax.axvline(censor_s, color=MUTED, lw=0.8, ls=":")
        ax.set_title(f, loc="left", color=INK)
        ax.set_xlim(0, censor_s * 1.05)
        ax.set_xlabel("MTTR (s)")
    axes[0].set_yticks(range(len(policies)), [STYLE[p][0] for p in policies])
    _save(fig, out, "a2_mttr_by_fault")


def fig_training(train: dict[str, list[dict[str, Any]]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.2))
    style = STYLE | dict([SEED1])
    ends = []
    for p in (p for p in ("sac_per", SEED1[0], "sac_per_cold") if p in train):
        ret = [r["return"] for r in train[p] if not r.get("interrupted")]
        if len(ret) < ROLL:
            continue
        roll = np.convolve(ret, np.ones(ROLL) / ROLL, mode="valid")
        x = np.arange(ROLL, len(ret) + 1)
        label, c, ls, _ = style[p]
        ax.plot(x, roll, color=c, ls=ls)
        ends.append((x[-1], roll[-1], label))
    span = np.ptp([e[1] for e in ends]) if ends else 0.0
    ys = dodge([e[1] for e in ends], max(span, 1.0) * 0.12)
    for y, (x_end, _, label) in zip(ys, ends, strict=True):
        ax.annotate(label, (x_end, y), xytext=(3, 0), textcoords="offset points", va="center",
                    fontsize=7, color=INK_2, annotation_clip=False)
    ax.set_xlabel("training episode")
    ax.set_ylabel(f"return ({ROLL}-episode mean)")
    _save(fig, out, "a3_training")


def _paired_medians(pairs: list[tuple[str, dict, dict]], xlabels: tuple[str, str], out: Path, name: str) -> None:
    """Per-fault median MTTR in two conditions as dumbbells: hollow = first condition, filled = second."""
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.7))
    width = 0.32
    for k, (p, left, right) in enumerate(pairs):
        label, c, ls, m = STYLE[p]
        dx = (k - (len(pairs) - 1) / 2) * width
        for j, f in enumerate(FAULTS):
            a, b = left.get(f), right.get(f)
            if a and b:
                ya, yb = a["mttr_median_s"], b["mttr_median_s"]
                ax.plot([j + dx] * 2, [ya, yb], color=c, ls=ls, lw=1.2)
                ax.plot(j + dx, ya, marker=m, ms=5, mfc=SURFACE, mec=c, ls="none")
                ax.plot(j + dx, yb, marker=m, ms=5, color=c, ls="none")
        ax.plot([], [], color=c, ls=ls, marker=m, ms=4, label=label)
    ax.plot([], [], marker="o", ms=4, mfc=SURFACE, mec=MUTED, ls="none", label=xlabels[0])
    ax.plot([], [], marker="o", ms=4, color=MUTED, ls="none", label=xlabels[1])
    ax.set_xticks(range(len(FAULTS)), list(FAULTS))
    ax.set_xlim(-0.6, len(FAULTS) - 0.4)
    ax.set_ylabel("median MTTR (s)")
    ax.legend(frameon=False, fontsize=6.5, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    _save(fig, out, name)


def _by_fault(summary: list[dict[str, Any]], policy: str) -> dict[str, dict[str, Any]]:
    return {s["fault"]: s for s in summary if s["policy"] == policy}


def fig_edge(analysis: dict[str, Any], out: Path) -> None:
    if "H5_edge" not in analysis:
        return
    main, edge = analysis["summary"], analysis["H5_edge"]["summary"]
    _paired_medians([(p, _by_fault(main, p), _by_fault(edge, p)) for p in ("runbook", "sac_per")],
                    ("main", "edge (netem)"), out, "a4_edge")


def fig_seed1(analysis: dict[str, Any], out: Path) -> None:
    if "seed1" not in analysis:
        return
    main, block = analysis["summary"], analysis["seed1"]["summary"]
    _paired_medians([("runbook", _by_fault(main, "runbook"), _by_fault(block, "runbook")),
                     ("sac_per", _by_fault(main, "sac_per"), _by_fault(block, analysis["seed1"]["agent"]))],
                    ("main (seed 0)", "seed-1 block"), out, "a5_seed1")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, required=True)
    ap.add_argument("--main", type=Path, required=True)
    ap.add_argument("--train", action="append", default=[])
    ap.add_argument("--censor-s", type=float, default=360.0)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "paper_a" / "latex" / "figures")
    args = ap.parse_args(argv)
    analysis = json.loads(args.analysis.read_text())
    rows, _ = final_valid(read_rows(args.main / "episodes.jsonl"))
    train = {k: read_rows(Path(v) / "episodes.jsonl") for k, v in (t.split("=", 1) for t in args.train)}
    args.out.mkdir(parents=True, exist_ok=True)
    fig_km(analysis, args.out, args.censor_s)
    fig_mttr_by_fault(rows, args.out, args.censor_s)
    fig_training(train, args.out)
    fig_edge(analysis, args.out)
    fig_seed1(analysis, args.out)
    print(f"wrote figures to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
