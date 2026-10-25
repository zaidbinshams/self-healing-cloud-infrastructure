"""Draw the Paper A figures from paper_a/figure_data/*.csv only (no raw data, no project install needed).

Needs Python 3.9+ with matplotlib and numpy:   pip install matplotlib numpy
Run from the repository root or from paper_a/:  python paper_a/make_figures.py   (or: python make_figures.py)
Output: paper_a/latex/figures/*.pdf (vector, for LaTeX) and *.png (preview), overwriting the shipped ones.
        With --slides: paper_a/slides/*.png at 600 dpi for presentations (the LaTeX figures are not touched).

    a1_km            pooled time to recovery (Kaplan-Meier), main evaluation        <- km_curves.csv
    a2_mttr_by_fault MTTR per fault and policy, one dot per episode, bar = median    <- episodes_main.csv
    a3_training      training return, 10-episode rolling mean                        <- training.csv
    a4_edge          median MTTR per fault: main vs netem edge subset                <- summary_main.csv, summary_edge.csv
    a5_seed1         median MTTR per fault: main block vs seed-1 block               <- summary_main.csv, summary_seed1.csv

Change labels, sizes or colours in the CONFIG block below; never change the data.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ------------------------------------------------------------------------------------------------ CONFIG
HERE = Path(__file__).resolve().parent
DATA = HERE / "figure_data"
OUT = HERE / "latex" / "figures"
SLIDES_OUT = HERE / "slides"
COL_W_IN = 3.4                    # IEEE one-column width (3.5 in) minus a margin; full width = 2 * COL_W_IN
CENSOR_S = 360.0                  # MTTR censoring point (fault window, 18 ticks x 20 s)
ROLL = 10                         # rolling-mean window for the training curve
FAULTS = ("F1", "F2", "F3", "F4")
# policy id in the CSVs -> (label in the figure, colour, line style, marker). Colour follows the policy everywhere.
STYLE = {
    "sac_per": ("PER-SAC (warm)", "#2a78d6", "-", "o"),
    "runbook": ("runbook", "#eb6834", "--", "s"),
    "sac_per_cold": ("PER-SAC (cold)", "#1baf7a", "-.", "^"),
    "k8s_hpa": ("Kubernetes + HPA", "#eda100", ":", "D"),
    "k8s_default": ("Kubernetes", "#e87ba4", (0, (5, 2, 1, 2)), "v"),
}
TRAIN_STYLE = {   # training runs: run id -> (label, colour, line style)
    "sac_per_s0": ("PER-SAC (warm)", "#2a78d6", "-"),
    "sac_per_s1": ("PER-SAC (warm, seed 1)", "#2a78d6", ":"),
    "sac_per_cold_s0": ("PER-SAC (cold)", "#1baf7a", "-."),
}
SURFACE, INK, INK_2, MUTED, GRID = "#ffffff", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.5,
    "pdf.fonttype": 42, "ps.fonttype": 42,          # embed TrueType fonts (IEEE PDF eXpress)
})
# ------------------------------------------------------------------------------------------------ helpers

SLIDES = False


def read(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(newline="") as f:
        return list(csv.DictReader(f))


def save(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    if SLIDES:
        fig.savefig(SLIDES_OUT / f"{name}.png", dpi=600)
        print(f"wrote {SLIDES_OUT / name}.png")
    else:
        for ext in ("pdf", "png"):
            fig.savefig(OUT / f"{name}.{ext}", dpi=300)
        print(f"wrote {OUT / name}.pdf")
    plt.close(fig)


def spread(ys: list[float], gap: float) -> list[float]:
    """Move end labels apart so they don't overlap (order kept)."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):  # noqa: RUF007  (itertools.pairwise needs Python 3.10)
        out[b] = max(out[b], out[a] + gap)
    return out


# ------------------------------------------------------------------------------------------------ figures


def fig_km() -> None:
    rows = read("km_curves.csv")
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.4))
    ends = []
    for p, (label, c, ls, _) in STYLE.items():
        pts = [(float(r["t_s"]), float(r["p_not_recovered"])) for r in rows if r["policy"] == p]
        t = [0.0, *[x for x, _ in pts], CENSOR_S]
        s = [1.0, *[y for _, y in pts]]
        s.append(s[-1])
        ax.step(t, s, where="post", color=c, ls=ls)
        ends.append((s[-1], label, c, ls))
    for y, (_, label, c, ls) in zip(spread([e[0] for e in ends], 0.08), ends):
        ax.plot([CENSOR_S * 1.02, CENSOR_S * 1.09], [y, y], color=c, ls=ls, lw=1.2, clip_on=False)
        ax.annotate(label, (CENSOR_S * 1.10, y), xytext=(2, 0), textcoords="offset points", va="center",
                    fontsize=6.5, color=INK_2, annotation_clip=False)
    ax.set_xlim(0, CENSOR_S)
    ax.set_ylim(-0.06, 1.06)
    ax.set_xlabel("time since injection (s)")
    ax.set_ylabel("P(not yet recovered)")
    save(fig, "a1_km")


def fig_mttr_by_fault() -> None:
    rows = [r for r in read("episodes_main.csv") if r["fault"] in FAULTS]
    policies = [p for p in STYLE if any(r["policy"] == p for r in rows)]
    fig, axes = plt.subplots(1, len(FAULTS), figsize=(2 * COL_W_IN, 2.2), sharey=True)
    rng = np.random.default_rng(0)                   # vertical jitter only, so dots don't overlap
    for ax, f in zip(axes, FAULTS):
        for i, p in enumerate(policies):
            xs = [float(r["mttr_censored_s"]) for r in rows if r["policy"] == p and r["fault"] == f]
            y = i + rng.uniform(-0.15, 0.15, len(xs))
            ax.scatter(xs, y, s=8, color=STYLE[p][1], marker=STYLE[p][3], alpha=0.7, linewidths=0)
            ax.plot([np.median(xs)] * 2, [i - 0.32, i + 0.32], color=INK, lw=1.5)
        ax.axvline(CENSOR_S, color=MUTED, lw=0.8, ls=":")
        ax.set_title(f, loc="left", color=INK)
        ax.set_xlim(0, CENSOR_S * 1.05)
        ax.set_xlabel("MTTR (s)")
    axes[0].set_yticks(range(len(policies)), [STYLE[p][0] for p in policies])
    save(fig, "a2_mttr_by_fault")


def fig_training() -> None:
    rows = read("training.csv")
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.2))
    ends = []
    for run, (label, c, ls) in TRAIN_STYLE.items():
        ret = [float(r["return"]) for r in rows if r["run"] == run]
        if len(ret) < ROLL:
            continue
        roll = np.convolve(ret, np.ones(ROLL) / ROLL, mode="valid")
        x = np.arange(ROLL, len(ret) + 1)
        ax.plot(x, roll, color=c, ls=ls)
        ends.append((x[-1], roll[-1], label))
    for y, (x_end, _, label) in zip(spread([e[1] for e in ends], 0.9), ends):
        ax.annotate(label, (x_end, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7,
                    color=INK_2, annotation_clip=False)
    ax.set_xlabel("training episode")
    ax.set_ylabel(f"return ({ROLL}-episode mean)")
    save(fig, "a3_training")


def medians(name: str, policy: str) -> dict[str, float]:
    return {r["fault"]: float(r["mttr_median_censored_s"]) for r in read(name)
            if r["policy"] == policy and r["fault"] in FAULTS}


def dumbbells(pairs: list[tuple[str, dict[str, float], dict[str, float]]], conditions: tuple[str, str],
              name: str) -> None:
    """Per-fault median MTTR in two conditions: hollow marker = first, filled = second."""
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.7))
    for k, (style_key, left, right) in enumerate(pairs):
        label, c, ls, m = STYLE[style_key]
        dx = (k - (len(pairs) - 1) / 2) * 0.32
        for j, f in enumerate(FAULTS):
            ax.plot([j + dx] * 2, [left[f], right[f]], color=c, ls=ls, lw=1.2)
            ax.plot(j + dx, left[f], marker=m, ms=5, mfc=SURFACE, mec=c, ls="none")
            ax.plot(j + dx, right[f], marker=m, ms=5, color=c, ls="none")
        ax.plot([], [], color=c, ls=ls, marker=m, ms=4, label=label)
    ax.plot([], [], marker="o", ms=4, mfc=SURFACE, mec=MUTED, ls="none", label=conditions[0])
    ax.plot([], [], marker="o", ms=4, color=MUTED, ls="none", label=conditions[1])
    ax.set_xticks(range(len(FAULTS)), list(FAULTS))
    ax.set_xlim(-0.6, len(FAULTS) - 0.4)
    ax.set_ylabel("median MTTR (s)")
    ax.legend(frameon=False, fontsize=6.5, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    save(fig, name)


def fig_edge() -> None:
    dumbbells([(p, medians("summary_main.csv", p), medians("summary_edge.csv", p)) for p in ("runbook", "sac_per")],
              ("main", "edge (netem)"), "a4_edge")


def fig_seed1() -> None:
    dumbbells([("runbook", medians("summary_main.csv", "runbook"), medians("summary_seed1.csv", "runbook")),
               ("sac_per", medians("summary_main.csv", "sac_per"), medians("summary_seed1.csv", "sac_per_s1"))],
              ("main (seed 0)", "seed-1 block"), "a5_seed1")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slides", action="store_true", help="write 600-dpi PNGs to paper_a/slides/ instead")
    SLIDES = ap.parse_args().slides
    (SLIDES_OUT if SLIDES else OUT).mkdir(parents=True, exist_ok=True)
    fig_km()
    fig_mttr_by_fault()
    fig_training()
    fig_edge()
    fig_seed1()
