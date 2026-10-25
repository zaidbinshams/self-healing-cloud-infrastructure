"""Slide-ready figures for the final capstone review, drawn from the committed CSVs only.

Needs Python 3.9+ with matplotlib and numpy:   pip install matplotlib numpy
Run from the repository root:                  python presentation/make_review_figures.py
Output: presentation/figures/RNN_<name>.png (200 dpi, for slides) and .pdf (vector), 16:9-friendly size.

Data: paper_a/figure_data/*.csv (results) and paper_b/figure_data/*.csv (measurement study). Diagrams and
tables (R01-R03, R08, R11, R16, R17) take their facts from CLAUDE.md, config/contract.yaml and
docs/PREREGISTRATION.md; every number in them is cited in presentation/README.md.
Change labels, sizes or colours in the CONFIG block; never change the data.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# ------------------------------------------------------------------------------------------------ CONFIG
ROOT = Path(__file__).resolve().parent.parent
A_DATA = ROOT / "paper_a" / "figure_data"
B_DATA = ROOT / "paper_b" / "figure_data"
OUT = Path(__file__).resolve().parent / "figures"
W, H = 12.0, 5.6                      # inches; fits under a title on a 13.33 x 7.5 in (16:9) slide
CENSOR_S = 360.0
FAULTS = ("F1", "F2", "F3", "F4")
# policy id -> (label, colour, line style, marker). Same colours as the paper figures.
STYLE = {
    "sac_per": ("PER-SAC (warm)", "#2a78d6", "-", "o"),
    "runbook": ("Runbook", "#eb6834", "--", "s"),
    "sac_per_cold": ("PER-SAC (cold)", "#1baf7a", "-.", "^"),
    "k8s_hpa": ("Kubernetes + HPA", "#eda100", ":", "D"),
    "k8s_default": ("Kubernetes", "#e87ba4", (0, (5, 2, 1, 2)), "v"),
}
TRAIN_STYLE = {
    "sac_per_s0": ("PER-SAC (warm, seed 0)", "#2a78d6", "-"),
    "sac_per_s1": ("PER-SAC (warm, seed 1)", "#2a78d6", ":"),
    "sac_per_cold_s0": ("PER-SAC (cold)", "#1baf7a", "-."),
}
CLIENT, SERVER, PING = ("#eb6834", "s"), ("#2a78d6", "o"), ("#1baf7a", "^")      # Paper B signals
SHORT = {"productcatalogservice": "catalog", "cartservice": "cart", "currencyservice": "currency",
         "frontend": "frontend"}
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]       # sequential, light -> dark
STATUS = {"good": ("#0ca30c", "✓"), "partial": ("#fab219", "◐"), "bad": ("#d03b3b", "✗")}
SURFACE, INK, INK_2, MUTED, GRID, FILL = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#f0efec"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 15, "axes.titlesize": 15, "axes.labelsize": 15, "xtick.labelsize": 13, "ytick.labelsize": 13,
    "legend.fontsize": 13, "axes.edgecolor": MUTED, "axes.labelcolor": INK_2, "xtick.color": INK_2,
    "ytick.color": INK_2, "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2.5,
    "pdf.fonttype": 42, "ps.fonttype": 42,
})
# ------------------------------------------------------------------------------------------------ helpers


def read(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(fig: plt.Figure, name: str) -> None:
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=200)
    plt.close(fig)
    print(f"wrote {OUT / name}.png")


def canvas() -> tuple[plt.Figure, plt.Axes]:
    """A blank 100 x 50 drawing area for diagrams and tables."""
    fig, ax = plt.subplots(figsize=(W, H))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 50)
    ax.axis("off")
    fig.subplots_adjust(0.01, 0.01, 0.99, 0.99)
    return fig, ax


def box(ax: plt.Axes, x: float, y: float, w: float, h: float, text: str, *, fc: str = SURFACE, ec: str = MUTED,
        size: float = 12.5, weight: str = "normal", lw: float = 1.2, ha: str = "center") -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=0.8", fc=fc, ec=ec, lw=lw))
    tx = x + w / 2 if ha == "center" else x + 1.0
    ax.text(tx, y + h / 2, text, ha=ha, va="center", fontsize=size, color=INK, weight=weight, linespacing=1.3)


def arrow(ax: plt.Axes, a: tuple[float, float], b: tuple[float, float], label: str = "", *, color: str = INK_2,
          dy: float = 1.3, size: float = 11.5, ls: str = "-") -> None:
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=18, color=color, lw=1.8, ls=ls,
                                 shrinkA=2, shrinkB=2))
    if label:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + dy, label, ha="center", va="bottom", fontsize=size,
                color=INK_2, bbox={"fc": SURFACE, "ec": "none", "pad": 1.5})


def grid_table(ax: plt.Axes, cols: list[tuple[str, float]], rows: list[list[str]], *, top: float = 48,
               row_h: float = 7.6, size: float = 12, head_size: float = 13,
               colors: dict[tuple[int, int], str] | None = None) -> None:
    """Text table on a canvas: cols = [(header, width)], widths summing to <= 98."""
    xs = np.cumsum([1.0] + [w for _, w in cols])
    ax.add_patch(plt.Rectangle((1, top - 5.2), xs[-1] - 1, 5.2, fc=FILL, ec="none"))
    for j, (head, _) in enumerate(cols):
        ax.text(xs[j] + 0.8, top - 2.6, head, ha="left", va="center", fontsize=head_size, weight="bold", color=INK)
    y = top - 5.2
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            c = (colors or {}).get((i, j), INK)
            ax.text(xs[j] + 0.8, y - row_h / 2, cell, ha="left", va="center", fontsize=size, color=c, linespacing=1.25)
        y -= row_h
        ax.plot([1, xs[-1]], [y, y], color=GRID, lw=1.0)


def summary(name: str) -> list[dict[str, str]]:
    return read(A_DATA, name)


# ------------------------------------------------------------------------------------------------ diagrams


def r01_architecture() -> None:
    fig, ax = canvas()
    box(ax, 1, 1, 38, 47, "", fc="#f6f8fc", ec="#9ec5f4", lw=1.5)
    box(ax, 61, 1, 38, 47, "", fc="#fcf8f5", ec="#f3c1a8", lw=1.5)
    ax.text(20, 45.3, "Machine B — controller (WSL2, CPU only)", ha="center", fontsize=13.5, weight="bold")
    ax.text(80, 45.3, "Machine A — cluster (K3s, single node)", ha="center", fontsize=13.5, weight="bold")
    left = [(36.5, "Locust load generator\n30 users · /tick window endpoint"),
            (28.25, "Telemetry collector\n7 sources in parallel · 3 s deadline"),
            (20, "Policy: PER-SAC or runbook\none decision every 20 s"),
            (11.75, "Action executor\nagent credential (least privilege)"),
            (3.5, "Fault injector + reset\ncontroller credential")]
    right = [(36.5, "Online Boutique · 11 deployments\nmanaged: frontend, cart, currency, catalog"),
             (28.25, "Prometheus · cAdvisor every 5 s\n+ frontend request logs"),
             (20, "Kubernetes API\n(+ metrics-server for HPA)"),
             (11.75, "Chaos Mesh\nCPU stress (StressChaos)")]
    for y, t in left:
        box(ax, 3, y, 34, 6.2, t)
    for y, t in right:
        box(ax, 63, y, 34, 6.2, t)
    for y0, y1 in ((28.25, 26.2), (20, 17.95)):
        arrow(ax, (20, y0), (20, y1))
    ax.add_patch(plt.Rectangle((44, 1), 12, 42.8, fc="none", ec=MUTED, ls=(0, (4, 3)), lw=1.4))
    ax.text(50, 46.6, "Wi-Fi hotspot\n(edge link)", ha="center", va="center", fontsize=12.5, color=INK_2)
    arrow(ax, (37.6, 39.6), (62.4, 39.6), "HTTP load", dy=0.6)
    arrow(ax, (62.4, 31.35), (37.6, 31.35), "metrics · logs · status", dy=0.6)
    arrow(ax, (37.6, 14.85), (62.4, 23.1), "restart · scale · restore", dy=-5.2)
    arrow(ax, (37.6, 6.6), (62.4, 14.85), "inject F1–F4", dy=-5.6, ls="--")
    save(fig, "R01_architecture")


def r02_tick() -> None:
    fig, ax = plt.subplots(figsize=(W, H))
    lanes = ["Measurement window", "Collect + decide", "Learn (training only)", "Action takes effect"]
    y = {n: len(lanes) - 1 - i for i, n in enumerate(lanes)}
    ax.barh(y[lanes[0]], 20, left=0, height=0.5, color="#9ec5f4")
    ax.barh(y[lanes[0]], 20, left=20, height=0.5, color="#cde2fb")
    ax.text(10, y[lanes[0]], "tick k: (T_k, T_k+1]", ha="center", va="center", fontsize=13)
    ax.text(30, y[lanes[0]], "tick k+1", ha="center", va="center", fontsize=13, color=INK_2)
    ax.barh(y[lanes[1]], 3, left=20, height=0.5, color="none", edgecolor=MUTED, ls="--", lw=1.4)
    ax.barh(y[lanes[1]], 0.33, left=20, height=0.5, color="#2a78d6")
    ax.annotate("median 0.33 s, p95 0.89 s\n(hard deadline 3 s)", (20.3, y[lanes[1]]), xytext=(25, y[lanes[1]]),
                va="center", fontsize=12.5, color=INK_2, arrowprops={"arrowstyle": "-", "color": MUTED})
    ax.barh(y[lanes[2]], 1.0, left=20.4, height=0.5, color="#6da7ec")
    ax.annotate("≤ 4 gradient steps, ≤ 1 s", (21.4, y[lanes[2]]), xytext=(25, y[lanes[2]]), va="center",
                fontsize=12.5, color=INK_2, arrowprops={"arrowstyle": "-", "color": MUTED})
    ax.barh(y[lanes[3]], 19.6, left=20.4, height=0.5, color=FILL, edgecolor=MUTED)
    ax.text(30.2, y[lanes[3]], "rollout runs asynchronously; one action in flight", ha="center", va="center",
            fontsize=12.5, color=INK_2)
    for t in (0, 20, 40):
        ax.axvline(t, color=MUTED, lw=1.0, zorder=0)
    ax.set_yticks(list(y.values()), list(y.keys()))
    ax.set_xlim(-0.5, 40.5)
    ax.set_xticks([0, 5, 10, 15, 20, 25, 30, 35, 40])
    ax.set_xlabel("time (s); boundaries T_k every 20 s on Machine B's clock")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R02_control_loop")


def r03_faults() -> None:
    fig, ax = canvas()
    cols = [("Fault", 15), ("Injected how", 33), ("What Kubernetes does", 25), ("Correct action", 25)]
    rows = [["F1  bad deploy", "EXTRA_LATENCY 300 ms–1 s on catalog\n(configuration rollout)",
             "nothing: pods healthy,\nbad template is the spec", "RESTORE catalog"],
            ["F2  hot pod", "CPU stress (1–2 workers) inside one\ncart or currency pod (Chaos Mesh)",
             "nothing: pod alive", "RESTART that service"],
            ["F3  scaled to 0", "cart or currency set to 0 replicas", "nothing: 0 is the\ndesired state",
             "RESTORE (or SCALE_UP)\nthat service"],
            ["F4  surge", "load × 2.5 or × 3 (Locust users)", "HPA adds frontend pods;\ndefault does nothing",
             "SCALE_UP frontend"],
            ["NULL", "no fault (tests false alarms)", "—", "do nothing (NOOP)"]]
    grid_table(ax, cols, rows, row_h=8.4, size=12.5)
    save(fig, "R03_faults_and_cures")


# ------------------------------------------------------------------------------------------------ results


def _recovered(policy: str) -> int:
    return sum(int(r["recovered"]) for r in summary("summary_main.csv") if r["policy"] == policy and r["fault"] in FAULTS)


def r04_recovered() -> None:
    order = ["runbook", "sac_per", "sac_per_cold", "k8s_hpa", "k8s_default"]
    vals = [_recovered(p) for p in order]
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    y = np.arange(len(order))[::-1]
    ax.barh(y, vals, height=0.6, color=[STYLE[p][1] for p in order])
    for yi, v in zip(y, vals):
        ax.text(v + 0.6, yi, f"{v} / 40", va="center", fontsize=15, color=INK)
    ax.set_yticks(y, [STYLE[p][0] for p in order])
    ax.set_xlim(0, 44)
    ax.set_xlabel("fault episodes recovered (of 40: 10 each of F1–F4)")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R04_faults_recovered")


def r05_matrix() -> None:
    eps = [r for r in summary("episodes_main.csv") if r["fault"] in FAULTS]
    cols = [("F1", None), ("F2", "cartservice"), ("F2", "currencyservice"), ("F3", None), ("F4", None)]
    heads = ["F1 bad deploy", "F2 hot pod\ncart", "F2 hot pod\ncurrency", "F3 scaled to 0", "F4 surge"]
    order = ["runbook", "sac_per", "sac_per_cold", "k8s_hpa", "k8s_default"]
    fig, ax = plt.subplots(figsize=(W, H))
    for i, p in enumerate(order):
        for j, (f, tgt) in enumerate(cols):
            sel = [r for r in eps if r["policy"] == p and r["fault"] == f and (tgt is None or r["target"] == tgt)]
            k, n = sum(r["recovered"] == "True" for r in sel), len(sel)
            c = BLUE_RAMP[min(len(BLUE_RAMP) - 1, round(k / n * (len(BLUE_RAMP) - 1)))]
            ax.add_patch(plt.Rectangle((j + 0.03, -i - 0.97), 0.94, 0.94, fc=c, ec=SURFACE, lw=2))
            ax.text(j + 0.5, -i - 0.5, f"{k}/{n}", ha="center", va="center", fontsize=16,
                    color="#ffffff" if k / n >= 0.5 else INK)
    ax.set_xlim(0, len(cols))
    ax.set_ylim(-len(order), 0)
    ax.set_xticks(np.arange(len(cols)) + 0.5, heads)
    ax.xaxis.tick_top()
    ax.set_yticks(-np.arange(len(order)) - 0.5, [STYLE[p][0] for p in order])
    ax.tick_params(length=0)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    save(fig, "R05_recovery_by_fault")


def r06_mttr() -> None:
    rows = summary("summary_main.csv")
    order = ["runbook", "sac_per", "k8s_hpa", "sac_per_cold", "k8s_default"]
    fig, ax = plt.subplots(figsize=(W, H))
    ys = {f: len(FAULTS) - 1 - i for i, f in enumerate(FAULTS)}
    for k, p in enumerate(order):
        off = (k - (len(order) - 1) / 2) * 0.14
        for f in FAULTS:
            r = next(x for x in rows if x["policy"] == p and x["fault"] == f)
            ax.plot(float(r["mttr_median_censored_s"]), ys[f] + off, marker=STYLE[p][3], ms=11, ls="none",
                    color=STYLE[p][1], mec=SURFACE, mew=1.2)
        ax.plot([], [], marker=STYLE[p][3], ms=10, ls="none", color=STYLE[p][1], label=STYLE[p][0])
    ax.axvline(CENSOR_S, color=MUTED, ls=":", lw=1.5)
    ax.text(CENSOR_S - 4, len(FAULTS) - 0.45, "not recovered = 360 s", ha="right", va="center", fontsize=12,
            color=INK_2)
    ax.set_yticks(list(ys.values()), ["F1 bad deploy", "F2 hot pod", "F3 scaled to 0", "F4 surge"])
    ax.set_xlim(0, 380)
    ax.set_ylim(-0.6, len(FAULTS) - 0.3)
    ax.set_xlabel("median MTTR (s); not-recovered episodes count as 360 s; 10 episodes per cell")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=5, frameon=False, fontsize=12.5,
              handletextpad=0.2, columnspacing=1.2)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R06_mttr_by_fault")


def r07_km() -> None:
    rows = summary("km_curves.csv")
    fig, ax = plt.subplots(figsize=(W, H))
    ends = []
    for p, (label, c, ls, _) in STYLE.items():
        pts = [(float(r["t_s"]), float(r["p_not_recovered"])) for r in rows if r["policy"] == p]
        t = [0.0, *[x for x, _ in pts], CENSOR_S]
        s = [1.0, *[y for _, y in pts]]
        s.append(s[-1])
        ax.step(t, s, where="post", color=c, ls=ls, lw=3)
        ends.append((s[-1], f"{label}: {round(s[-1] * 40)}/40 not recovered", c))
    for y, label, c in _spread(ends, 0.075):
        ax.plot([CENSOR_S + 3, CENSOR_S + 14], [y, y], color=c, lw=3, clip_on=False)
        ax.annotate(label, (CENSOR_S + 16, y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=13,
                    color=INK_2, annotation_clip=False)
    ax.set_xlim(0, CENSOR_S)
    ax.set_ylim(-0.04, 1.04)
    ax.set_xlabel("time since fault injection (s)")
    ax.set_ylabel("share of faults not yet recovered")
    fig.subplots_adjust(left=0.08, right=0.66, bottom=0.13, top=0.97)
    save(fig, "R07_time_to_recovery")


def _spread(ends: list[tuple[float, str, str]], gap: float) -> list[tuple[float, str, str]]:
    ends = sorted(ends)
    out = []
    last = -float("inf")
    for y, label, c in ends:
        y2 = max(y, last + gap)
        out.append((y2, label, c))
        last = y2
    return out


def _cell(seqs: list[str]) -> str:
    n = len(seqs)
    acts = [[a.strip() for a in s.split(">")] if s != "none" else [] for s in seqs]
    per_ep = Counter(a for ep in acts for a in set(ep))
    lines = []
    for a, k in per_ep.most_common(2):
        kind, _, tgt = a.partition(" ")
        lines.append(f"{kind} {SHORT.get(tgt, tgt)} ({k}/{n})")
    none = sum(1 for ep in acts if not ep)
    if none:
        lines.append(f"no action ({none}/{n})")
    mean = sum(len(ep) for ep in acts) / n
    if mean > 2.5:
        lines.append(f"≈{mean:.0f} actions per episode")
    return "\n".join(lines[:3])


def r08_actions() -> None:
    rows = summary("actions_main.csv")
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for r in rows:
        groups[(r["policy"], r["fault"], r["target"])] += [r["executed_actions"]] * int(r["episodes"])
    keys = [("F1", "productcatalogservice", "RESTORE catalog"), ("F2", "cartservice", "RESTART cart"),
            ("F2", "currencyservice", "RESTART currency"), ("F3", "cartservice", "RESTORE cart"),
            ("F3", "currencyservice", "RESTORE currency"), ("F4", "frontend", "SCALE_UP frontend"),
            ("NULL", "", "no action")]
    fig, ax = canvas()
    cols = [("Fault · target", 17), ("Correct action", 19), ("Runbook", 20), ("PER-SAC (warm)", 20),
            ("PER-SAC (cold)", 22)]
    table = []
    for f, tgt, cure in keys:
        name = f"{f} · {SHORT[tgt]}" if tgt else "NULL (no fault)"
        table.append([name, cure] + [_cell(groups[(p, f, tgt)]) for p in ("runbook", "sac_per", "sac_per_cold")])
    grid_table(ax, cols, table, row_h=6.1, size=11, head_size=12.5)
    save(fig, "R08_actions_learned")


def r09_training() -> None:
    rows = summary("training.csv")
    fig, ax = plt.subplots(figsize=(W, H))
    ends = []
    for run, (label, c, ls) in TRAIN_STYLE.items():
        ret = np.array([float(r["return"]) for r in rows if r["run"] == run])
        roll = np.convolve(ret, np.ones(10) / 10, mode="valid")
        x = np.arange(10, len(ret) + 1)
        ax.plot(x, roll, color=c, ls=ls, lw=3)
        ends.append((roll[-1], label, c, x[-1]))
    for (y, label, c), (_, _, _, xe) in zip(_spread([(e[0], e[1], e[2]) for e in ends], 1.0),
                                          sorted(ends, key=lambda e: e[0])):
        ax.annotate(label, (xe, y), xytext=(8, 0), textcoords="offset points", va="center", fontsize=13,
                    color=INK_2, annotation_clip=False)
    ax.set_xlabel("training episode (live cluster)")
    ax.set_ylabel("episode return, 10-episode mean\n(higher = less SLA violation)")
    fig.subplots_adjust(left=0.11, right=0.76, bottom=0.13, top=0.97)
    save(fig, "R09_warm_vs_cold_training")


def r10_fault_free() -> None:
    order = ["runbook", "sac_per", "sac_per_cold", "k8s_hpa", "k8s_default"]
    rows = summary("summary_main.csv")
    vals = [int(float(next(r for r in rows if r["policy"] == p and r["fault"] == "NULL")["null_with_action"]))
            for p in order]
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    y = np.arange(len(order))[::-1]
    ax.barh(y, vals, height=0.6, color=[STYLE[p][1] for p in order])
    for yi, v in zip(y, vals):
        ax.text(v + 0.15, yi, f"{v} / 10", va="center", fontsize=15, color=INK)
    ax.set_yticks(y, [STYLE[p][0] for p in order])
    ax.set_xlim(0, 11.5)
    ax.set_xlabel("fault-free (NULL) evaluation episodes in which the policy took any action")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R10_false_alarms")


def r11_hypotheses() -> None:
    fig, ax = canvas()
    cols = [("", 4), ("Hypothesis", 34), ("Result", 20), ("Evidence", 40)]
    rows = [["H1", "Faster recovery than Kubernetes and HPA", "partly", "F1–F3 vs both, F4 vs Kubernetes: yes\nF4 vs HPA: HPA faster (δ −0.58, p .031)"],
            ["H2", "As fast as the runbook (margin 20 s);\nlower SLA penalty", "partly", "MTTR CI [−20.0, +0.9] s: non-inferior\nSLA penalty: tie (p .66)"],
            ["H3", "Runbook warm start helps", "supported", "early return p 6.5e-9; MTTR p 4.4e-5\n(cold start 100 episodes)"],
            ["H4", "Safe: no false remediation, no violations", "supported", "0/10 NULL episodes, 0/30 in training,\n0 safety violations"],
            ["H5", "Holds under a degraded link (netem)", "not established", "CI upper bound 20.15 s vs margin 20 s\n(n = 16 per policy)"]]
    status = {"partly": "partial", "supported": "good", "not established": "bad"}
    colors = {}
    for i, r in enumerate(rows):
        key = status[r[2]]
        c, icon = STATUS[key]
        colors[(i, 2)] = "#8a5a00" if key == "partial" else c       # amber icon/label text needs a darker ink
        r[2] = f"{icon}  {r[2]}"
    grid_table(ax, cols, rows, row_h=8.4, size=12.5, colors=colors)
    save(fig, "R11_hypotheses")


def _medians(name: str, policy: str) -> dict[str, float]:
    return {r["fault"]: float(r["mttr_median_censored_s"]) for r in summary(name)
            if r["policy"] == policy and r["fault"] in FAULTS}


def r12_robustness() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(W, H), sharey=True)
    panels = [(axes[0], "Degraded link (+150 ms, 1 % loss)", "summary_edge.csv", ("runbook", "sac_per"),
               ("main", "netem")),
              (axes[1], "Second training seed", "summary_seed1.csv", ("runbook", "sac_per_s1"),
               ("main (seed 0)", "seed-1 block"))]
    for ax, title, name, (rb, agent), conds in panels:
        for k, (pol, second) in enumerate((("runbook", rb), ("sac_per", agent))):
            label, c, ls, m = STYLE[pol]
            left, right = _medians("summary_main.csv", pol), _medians(name, second)
            dx = (k - 0.5) * 0.3
            for j, f in enumerate(FAULTS):
                ax.plot([j + dx] * 2, [left[f], right[f]], color=c, ls=ls, lw=2)
                ax.plot(j + dx, left[f], marker=m, ms=10, mfc=SURFACE, mec=c, mew=2, ls="none")
                ax.plot(j + dx, right[f], marker=m, ms=10, color=c, ls="none")
        ax.set_title(title, loc="left", color=INK)
        ax.set_xticks(range(len(FAULTS)), list(FAULTS))
        ax.set_xlim(-0.6, len(FAULTS) - 0.4)
        ax.text(0.98, 0.97, f"hollow = {conds[0]}\nfilled = {conds[1]}", transform=ax.transAxes, ha="right",
                va="top", fontsize=12, color=INK_2)
    axes[0].set_ylabel("median MTTR (s)")
    for p in ("runbook", "sac_per"):
        label, c, ls, m = STYLE[p]
        axes[0].plot([], [], color=c, ls=ls, marker=m, ms=9, label=label)
    fig.legend(loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, "R12_robustness")


def r13_latency() -> None:
    ctx = {r["block"]: r for r in read(A_DATA, "context.csv")}
    fig, ax = plt.subplots(figsize=(W, H * 0.7))
    names = [("main", "main evaluation"), ("edge", "degraded link (netem)")]
    for i, (key, label) in enumerate(names):
        y = len(names) - 1 - i
        med, p95 = float(ctx[key]["decision_latency_median_s"]), float(ctx[key]["decision_latency_p95_s"])
        ax.plot([med, p95], [y, y], color="#9ec5f4", lw=6, solid_capstyle="round")
        ax.plot(med, y, "o", ms=13, color="#2a78d6")
        ax.plot(p95, y, "o", ms=13, mfc=SURFACE, mec="#2a78d6", mew=2.5)
        ax.text(med - 0.08, y + 0.22, f"median {med:.2f} s", ha="left", fontsize=12.5, color=INK_2)
        ax.text(p95, y - 0.3, f"p95 {p95:.2f} s", ha="center", va="top", fontsize=12.5, color=INK_2)
    ax.axvline(4.0, color=MUTED, ls="--", lw=1.5)
    ax.text(4.05, len(names) - 0.55, "pre-registered target:\np95 ≤ 4 s", fontsize=12, color=INK_2, va="top")
    ax.set_yticks([1, 0], [n for _, n in names])
    ax.set_xlim(0, 6)
    ax.set_ylim(-0.7, 1.6)
    ax.set_xlabel("decision latency: window close → action dispatched (s); tick = 20 s")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R13_decision_latency")


def r14_paperb_timeseries() -> None:
    rows = read(B_DATA, "fig_b2_timeseries_treatmentA.csv")
    t = [int(r["tick"]) for r in rows]
    fig, ax = plt.subplots(figsize=(W, H))
    for (c, m), name, col in ((CLIENT, "client-side p99 (Locust, across Wi-Fi)", "client_p99_ms"),
                              (PING, "ping p99 (network only)", "ping_p99_ms"),
                              (SERVER, "server-side p99 (frontend logs)", "server_p99_ms")):
        ys = [float(r[col]) for r in rows]
        ax.plot(t, ys, color=c, marker=m, ms=6)
        ax.annotate(name, (t[-1], ys[-1]), xytext=(10, 0), textcoords="offset points", va="center", fontsize=13,
                    color=INK_2, annotation_clip=False)
    ax.set_xlabel("tick (20 s each), one 10-minute run after the runtime fix")
    ax.set_ylabel("latency (ms)")
    fig.subplots_adjust(left=0.08, right=0.68, bottom=0.13, top=0.97)
    save(fig, "R14_client_vs_server_latency")


def r15_paperb_detect() -> None:
    rows = read(B_DATA, "fig_b4_detectability.csv")
    smokes: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:
        smokes.setdefault(r["smoke"], {})[r["signal"]] = r
    labels, cl, sv = [], [], []
    for s in smokes.values():
        r = s["server p95"]
        sev = float(r["severity"])
        labels.append(f"F4 surge ×{sev}" if r["fault"] == "F4" else f"F2 {SHORT[r['target']]}, {sev:g} worker")
        cl.append(float(s["client p99"]["peak_over_threshold"]))
        sv.append(float(r["peak_over_threshold"]))
    first = next(iter(smokes.values()))
    y = np.arange(len(labels))[::-1]
    fig, ax = plt.subplots(figsize=(W, H * 0.85))
    ax.axvspan(1.0, 3.1, color="#eef4fc", zorder=0)
    ax.plot(cl, y, ls="none", marker=CLIENT[1], ms=13, color=CLIENT[0],
            label=f"client-side p99 (threshold {float(first['client p99']['threshold_ms']):.0f} ms)")
    ax.plot(sv, y, ls="none", marker=SERVER[1], ms=13, color=SERVER[0],
            label=f"server-side p95 (threshold {float(first['server p95']['threshold_ms']):.0f} ms)")
    for xs in (cl, sv):
        for xi, yi in zip(xs, y):
            ax.text(xi, yi + 0.22, f"{xi:.2f}×", ha="center", fontsize=12, color=INK_2)
    ax.axvline(1.0, color=INK_2, lw=1.5)
    ax.text(1.05, len(labels) - 0.45, "detected →", fontsize=13, color=INK_2)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, 3.1)
    ax.set_ylim(-0.6, len(labels) - 0.2)
    ax.set_xlabel("fault peak ÷ the signal's calibrated threshold (same calibration rule for both)")
    ax.legend(loc="lower right", frameon=False)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    save(fig, "R15_what_each_signal_detects")


def r16_plan() -> None:
    fig, ax = canvas()
    cols = [("Planned (PLAN.md)", 40), ("Delivered", 42), ("", 16)]
    rows = [["M1–M2  cluster, credentials, telemetry, faults", "done (1–2 Oct)", "good"],
            ["M3  real-cluster environment, runbook, gates", "done; G5/G6 missed once (one Wi-Fi outage), reviewed", "good"],
            ["M4  masked SAC (Tianshou)", "in-house PyTorch SAC (approved 10 Oct)", "good"],
            ["M4  PER: 3 seeds;  uniform ablation: 3 seeds", "PER: 2 seeds;  uniform ablation: not run", "partial"],
            ["M4  cold-start ablation (mandatory)", "done, 100 episodes (Amendment 7)", "good"],
            ["M5  pre-registered evaluation, statistics", "5 policies × 50 + edge 2 × 20 + seed-1 2 × 50", "good"],
            ["not planned", "edge study, seed-1 replication, Paper B", "good"]]
    label = {"good": "met", "partial": "partly met"}
    colors = {}
    for i, r in enumerate(rows):
        c, icon = STATUS[r[2]]
        colors[(i, 2)] = c if r[2] == "good" else "#8a5a00"
        r[2] = f"{icon} {label[r[2]]}" if i < len(rows) - 1 else "+ extra"
    grid_table(ax, cols, rows, row_h=6.0, size=12.5, colors=colors)
    save(fig, "R16_plan_vs_delivered")


def r17_timeline() -> None:
    # (date, day, text, stem height): + above the line, - below; 13 Oct sits higher so it clears 9 Oct
    events = [("1 Oct", 1, "M1 cluster baseline", 0.55), ("2 Oct", 2, "M2 telemetry and faults", -0.55),
              ("9 Oct", 9, "SLA signal → server-side p95", 0.55), ("10 Oct", 10, "pre-registration", -0.55),
              ("13 Oct", 13, "schedule frozen; runbook data", 1.35), ("20 Oct", 20, "seed-0 training done", -0.55),
              ("24 Oct", 24, "main + edge evaluation done", 0.55), ("25 Oct", 25, "seed-1 block done", -0.55)]
    fig, ax = plt.subplots(figsize=(W, H * 0.8))
    ax.plot([0, 26], [0, 0], color=MUTED, lw=2)
    for d, x, text, h in events:
        up = h > 0
        ax.plot(x, 0, "o", ms=11, color="#2a78d6")
        ax.plot([x, x], [0, h], color=MUTED, lw=1)
        ax.text(x, h + (0.07 if up else -0.07), f"{d}\n{text}" if up else f"{text}\n{d}", ha="center",
                va="bottom" if up else "top", fontsize=12, color=INK)
    ax.axvspan(12.5, 25.5, color="#eef4fc", zorder=0)
    ax.text(19, -1.6, "on the live cluster: runbook data, training, evaluation", ha="center", fontsize=12, color=INK_2)
    ax.set_xlim(-0.5, 27)
    ax.set_ylim(-1.8, 2.3)
    ax.axis("off")
    fig.tight_layout()
    save(fig, "R17_timeline_oct_2026")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in (r01_architecture, r02_tick, r03_faults, r04_recovered, r05_matrix, r06_mttr, r07_km, r08_actions,
               r09_training, r10_fault_free, r11_hypotheses, r12_robustness, r13_latency, r14_paperb_timeseries,
               r15_paperb_detect, r16_plan, r17_timeline):
        fn()
