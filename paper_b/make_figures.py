"""Draw the Paper B figures from paper_b/figure_data/*.csv only (no raw data, no project install needed).

Needs Python 3.9+ with matplotlib:   pip install matplotlib
Run from the repository root or from paper_b/:  python paper_b/make_figures.py   (or: python make_figures.py)
Output: paper_b/latex/figures/*.pdf (vector, for LaTeX) and *.png (preview), overwriting the shipped ones.
        With --slides: paper_b/slides/*.png at 600 dpi for presentations (the LaTeX figures are not touched).

    b1_decomposition      CV of client vs server p99 and their correlation with ping RTT, per run   <- fig_b1_decomposition.csv
    b2_timeseries_treatA  client p99, ping p99 and server p99 per tick, one run after the fix        <- fig_b2_timeseries_treatmentA.csv
    b3_estimator_noise    sampling CV of the window quantile vs expected tail samples, with the fit  <- fig_b3_estimator_noise.csv, fig_b3_fit.csv
    b4_detectability      fault peak / calibrated threshold, client p99 vs server p95, per smoke      <- fig_b4_detectability.csv

Change labels, sizes or colours in the CONFIG block below; never change the data.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

# ------------------------------------------------------------------------------------------------ CONFIG
HERE = Path(__file__).resolve().parent
DATA = HERE / "figure_data"
OUT = HERE / "latex" / "figures"
SLIDES_OUT = HERE / "slides"
COL_W_IN = 3.4                    # IEEE one-column width (3.5 in) minus a margin; full width = 2 * COL_W_IN
# One colour and marker per signal, the same in every figure: client = orange square, server = blue circle.
CLIENT = ("client p99 (Locust)", "#eb6834", "s")
SERVER = ("server p99 (frontend logs)", "#2a78d6", "o")
PING = ("ping p99 (B→A)", "#1baf7a", "^")
QUANTILE_STYLE = {0.90: ("#1baf7a", "^"), 0.95: ("#2a78d6", "o"), 0.99: ("#eb6834", "s")}   # Fig. b3
SHORT = {"cartservice": "cart", "currencyservice": "currency"}                              # Fig. b4 row labels
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
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
    with (DATA / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(fig: plt.Figure, name: str) -> None:
    if not fig.get_layout_engine():
        fig.tight_layout()
    if SLIDES:
        fig.savefig(SLIDES_OUT / f"{name}.png", dpi=600)
        print(f"wrote {SLIDES_OUT / name}.png")
    else:
        for ext in ("pdf", "png"):
            fig.savefig(OUT / f"{name}.{ext}", dpi=300)
        print(f"wrote {OUT / name}.pdf")
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ figures


def fig_decomposition() -> None:
    rows = read("fig_b1_decomposition.csv")
    y = list(range(len(rows)))[::-1]                     # first CSV row at the top
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(2 * COL_W_IN, 2.3), sharey=True)
    panels = ((ax1, "cv_client_p99", "cv_server_p99", "Tick-to-tick variability", "CV of tick p99"),
              (ax2, "r_client_vs_ping_p99", "r_server_vs_ping_p99", "Coupling to network RTT", "Pearson r with ping p99"))
    for ax, kc, ks, title, xlab in panels:
        ax.scatter([float(r[kc]) for r in rows], y, color=CLIENT[1], marker=CLIENT[2], s=22, zorder=3, label=CLIENT[0])
        ax.scatter([float(r[ks]) for r in rows], y, color=SERVER[1], marker=SERVER[2], s=22, zorder=3, label=SERVER[0])
        ax.set_title(title, loc="left", color=INK)
        ax.set_xlabel(xlab)
    ax1.set_yticks(y, [r["run"] for r in rows])
    ax2.axvline(0, color=MUTED, lw=0.8)
    h, lab = ax1.get_legend_handles_labels()
    fig.legend(h, lab, loc="upper center", ncol=2, frameon=False, fontsize=7, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    save(fig, "b1_decomposition")


def fig_timeseries() -> None:
    rows = read("fig_b2_timeseries_treatmentA.csv")
    t = [int(r["tick"]) for r in rows]
    series = [(CLIENT, "client p99", "client_p99_ms"), (PING, PING[0], "ping_p99_ms"), (SERVER, "server p99", "server_p99_ms")]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.1))
    for (_, c, m), name, col in series:
        ys = [float(r[col]) for r in rows]
        ax.plot(t, ys, color=c, marker=m, ms=3)
        ax.annotate(name, (t[-1], ys[-1]), xytext=(4, 0), textcoords="offset points", va="center", fontsize=7, color=INK_2)
    ax.set_xlabel("tick (20 s)")
    ax.set_ylabel("latency (ms)")
    ax.set_xlim(t[0], t[-1] + 7)
    save(fig, "b2_timeseries_treatA")


def fig_estimator() -> None:
    cells = read("fig_b3_estimator_noise.csv")
    fit = read("fig_b3_fit.csv")[0]
    a, b = float(fit["a"]), float(fit["b"])
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.3))
    for q, (c, m) in QUANTILE_STYLE.items():
        pts = sorted((float(x["expected_tail_samples_k"]), float(x["bootstrap_sampling_cv"]))
                     for x in cells if abs(float(x["quantile"]) - q) < 1e-9)
        ks, cvs = [k for k, _ in pts], [v for _, v in pts]
        ax.plot(ks, cvs, color=c, marker=m, ms=4, lw=1.0)
        ax.annotate(f"p{round(q * 100)}", (ks[0], cvs[0]), xytext=(-4, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=7, color=INK_2)
    kk = [1.5, 300.0]
    ax.plot(kk, [a * k ** b for k in kk], color=MUTED, lw=0.9, ls="--")
    ax.annotate(f"fit: CV = {a:.2f}·k^{b:.2f}", (60, a * 60 ** b), xytext=(0, 6), textcoords="offset points",
                fontsize=7, color=INK_2)
    ax.axhline(0.10, color=GRID, lw=1.2, zorder=0)       # the 10 % sampling-CV reference line
    ax.set_xscale("log")
    ax.set_yscale("log")
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        axis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_yticks([0.03, 0.05, 0.1, 0.2])
    ax.set_xlabel("expected tail samples per window, k = n(1−q)")
    ax.set_ylabel("sampling CV (bootstrap)")
    save(fig, "b3_estimator_noise")


def fig_detectability() -> None:
    rows = read("fig_b4_detectability.csv")
    smokes: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:                                       # one row per (smoke, signal); keep the CSV order
        smokes.setdefault(r["smoke"], {})[r["signal"]] = r
    labels, client, server = [], [], []
    for s in smokes.values():
        r = s["server p95"]
        sev = float(r["severity"])
        labels.append(f"F4 surge ×{sev}" if r["fault"] == "F4" else f"F2 {SHORT[r['target']]}, {sev:g} worker")
        client.append(float(s["client p99"]["peak_over_threshold"]))
        server.append(float(r["peak_over_threshold"]))
    first = next(iter(smokes.values()))
    y = list(range(len(labels)))[::-1]
    fig, ax = plt.subplots(figsize=(COL_W_IN, 2.2))
    ax.scatter(client, y, color=CLIENT[1], marker=CLIENT[2], s=24, zorder=3,
               label=f"client p99 (threshold {float(first['client p99']['threshold_ms']):.0f} ms)")
    ax.scatter(server, y, color=SERVER[1], marker=SERVER[2], s=24, zorder=3,
               label=f"server p95 (threshold {float(first['server p95']['threshold_ms']):.0f} ms)")
    ax.axvline(1.0, color=INK_2, lw=0.9)
    ax.annotate("threshold", (1.0, max(y) + 0.45), xytext=(3, 0), textcoords="offset points", fontsize=7, color=INK_2)
    ax.set_yticks(y, labels)
    ax.set_xlabel("fault peak / calibrated threshold")
    ax.set_ylim(-0.6, max(y) + 0.8)
    fig.legend(loc="upper center", ncol=1, frameon=False, fontsize=7, bbox_to_anchor=(0.55, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.80))
    save(fig, "b4_detectability")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slides", action="store_true", help="write 600-dpi PNGs to paper_b/slides/ instead")
    SLIDES = ap.parse_args().slides
    (SLIDES_OUT if SLIDES else OUT).mkdir(parents=True, exist_ok=True)
    fig_decomposition()
    fig_timeseries()
    fig_estimator()
    fig_detectability()
