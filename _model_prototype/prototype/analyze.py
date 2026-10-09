"""Metrics, statistics and the 7 figures for the SYNTHETIC prototype results."""
from __future__ import annotations

import argparse
import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import mannwhitneyu  # noqa: E402

from .contract import ACTIONS  # noqa: E402

TAGS = ["k8s_default", "k8s_hpa", "runbook", "sac_uniform_s0", "sac_per_s0"]
LABEL = {"k8s_default": "K8s default", "k8s_hpa": "K8s + HPA", "runbook": "Runbook",
         "sac_uniform_s0": "SAC uniform s0", "sac_per_s0": "SAC PER s0"}
COLOR = {"k8s_default": "#52514e", "k8s_hpa": "#1baf7a", "runbook": "#eb6834",
         "sac_uniform_s0": "#4a3aa7", "sac_per_s0": "#2a78d6"}
FAULTS = ["F1", "F2", "F3", "F4"]
CENSOR = 360.0
WATERMARK = "SYNTHETIC toy-simulator data — illustrative prototype output, NOT cluster measurements"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SHORT = {"productcatalogservice": "pc", "cartservice": "cart", "currencyservice": "cur",
         "frontend": "fe"}


def useful(fault: str, target: str | None) -> set[str]:
    t = SHORT.get(target or "", "")
    return {"F1": {"RESTORE pc"}, "F2": {f"RESTART {t}", f"SCALE_UP {t}"},
            "F3": {f"SCALE_UP {t}", f"RESTORE {t}"}, "F4": {"SCALE_UP fe", "SCALE_DOWN fe"},
            "NULL": set()}[fault]


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def finish(fig, path, title):
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.01, 0.005, WATERMARK, fontsize=7.5, color="#b0342f", ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)


def end_labels(ax, items, x, gap):
    """items: [(y, text)]; nudges labels apart so line-end labels never overlap."""
    items = sorted(items)
    ys = [y for y, _ in items]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    for (y0, txt), y in zip(items, ys):
        ax.text(x, y, txt, color=INK2, fontsize=8, va="center")


def mttr_c(e):
    return e["mttr_s"] if e["recovered"] else CENSOR


def km_curve(times, events):
    order = np.argsort(times)
    t, ev = np.asarray(times)[order], np.asarray(events)[order]
    n, s, xs, ys = len(t), 1.0, [0.0], [1.0]
    for u in np.unique(t[ev == 1]):
        at_risk = np.sum(t >= u)
        d = np.sum((t == u) & (ev == 1))
        s *= 1 - d / at_risk
        xs.append(float(u)); ys.append(s)
    xs.append(CENSOR); ys.append(s)
    return xs, ys, n


def boot_median_ci(x, rng, B=5000):
    x = np.asarray(x, float)
    meds = np.median(rng.choice(x, (B, len(x)), replace=True), axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default="results/eval")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    figdir = os.path.join(a.out, "figures")
    os.makedirs(figdir, exist_ok=True)
    data = {t: json.load(open(os.path.join(a.eval, f"{t}_episodes.json"))) for t in TAGS}
    rng = np.random.default_rng(0)

    # ------------------------------------------------------------ summary.csv
    rows = []
    for t in TAGS:
        for f in FAULTS + ["NULL", "ALL_FAULTS"]:
            eps = [e for e in data[t] if (e["fault"] == f if f != "ALL_FAULTS" else e["fault"] != "NULL")]
            acts = [x for e in eps for x in e["actions"]]
            wasted = sum(1 for e in eps for x in e["actions"]
                         if x["phase"] == "lead_in" or x["action"] not in useful(e["fault"], e["target"]))
            m = [mttr_c(e) for e in eps]
            rows.append({
                "tag": t, "fault": f, "n": len(eps),
                "recovery_rate": round(np.mean([e["recovered"] for e in eps]), 3) if f != "NULL" else "",
                "mttr_median_s": round(float(np.median(m)), 1) if f != "NULL" else "",
                "mttr_q1_s": round(float(np.percentile(m, 25)), 1) if f != "NULL" else "",
                "mttr_q3_s": round(float(np.percentile(m, 75)), 1) if f != "NULL" else "",
                "sum_v_mean": round(float(np.mean([e["sum_v"] for e in eps])), 3),
                "return_mean": round(float(np.mean([e["return"] for e in eps])), 3),
                "actions_per_episode": round(len(acts) / len(eps), 2),
                "wasted_action_rate": round(wasted / len(acts), 3) if acts else 0.0,
                "frr": round(np.mean([e["n_actions"] > 0 for e in eps]), 3) if f == "NULL" else "",
                "schedule_sha256": data[t][0]["schedule_sha256"][:16],
                "data_origin": "SYNTHETIC_TOY_SIMULATOR"})
    with open(os.path.join(a.out, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    # --------------------------------------------------------------- stats.md
    lines = [f"# Statistics — {WATERMARK}", "",
             "MTTR censored at 360 s (non-recovered episodes enter as 360 s). n = 6 per fault type.", "",
             "## Mann–Whitney U (two-sided) on MTTR, per fault type", "",
             "| Comparison | Fault | median A (s) | median B (s) | U | p |", "|---|---|---|---|---|---|"]
    for A, B in (("sac_per_s0", "runbook"), ("sac_per_s0", "sac_uniform_s0"),
                 ("sac_per_s0", "k8s_hpa"), ("runbook", "k8s_hpa")):
        for f in FAULTS + ["ALL"]:
            xa = [mttr_c(e) for e in data[A] if e["fault"] != "NULL" and (f == "ALL" or e["fault"] == f)]
            xb = [mttr_c(e) for e in data[B] if e["fault"] != "NULL" and (f == "ALL" or e["fault"] == f)]
            if np.allclose(xa, xa[0]) and np.allclose(xb, xa[0]):
                U, p = float(len(xa) * len(xb) / 2), 1.0
            else:
                U, p = mannwhitneyu(xa, xb, alternative="two-sided")
            lines.append(f"| {LABEL[A]} vs {LABEL[B]} | {f} | {np.median(xa):.0f} | {np.median(xb):.0f} | "
                         f"{float(U):.1f} | {float(p):.3f} |")
    lines += ["", "## Bootstrap 95% CI of median MTTR over the 24 fault episodes (5,000 resamples)", "",
              "| Policy | median (s) | 95% CI (s) |", "|---|---|---|"]
    for t in TAGS:
        m = [mttr_c(e) for e in data[t] if e["fault"] != "NULL"]
        lo, hi = boot_median_ci(m, rng)
        lines.append(f"| {LABEL[t]} | {np.median(m):.0f} | [{lo:.0f}, {hi:.0f}] |")
    with open(os.path.join(a.out, "stats.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    # ---------------------------------------------------------------- figures
    W = 0.15
    # 1. MTTR boxes
    fig, ax = plt.subplots(figsize=(10, 4.6))
    for i, t in enumerate(TAGS):
        pos = [j + (i - 2) * W for j in range(4)]
        vals = [[mttr_c(e) for e in data[t] if e["fault"] == f] for f in FAULTS]
        bp = ax.boxplot(vals, positions=pos, widths=W * 0.8, patch_artist=True, showfliers=False,
                        medianprops={"color": INK, "lw": 1.2},
                        whiskerprops={"color": COLOR[t]}, capprops={"color": COLOR[t]},
                        flierprops={"marker": "o", "ms": 3, "mfc": COLOR[t], "mec": COLOR[t]})
        for b in bp["boxes"]:
            b.set(facecolor=COLOR[t], edgecolor=COLOR[t], alpha=0.35)
        for p, v in zip(pos, vals):   # every episode as a dot, so constant groups stay visible
            ax.scatter(p + rng.uniform(-W * 0.25, W * 0.25, len(v)), v, s=14, color=COLOR[t],
                       edgecolors="#fcfcfb", linewidths=0.6, zorder=3)
    ax.axhline(CENSOR, color=INK2, lw=0.8, ls="--")
    ax.text(3.45, CENSOR - 8, "censored (not recovered)", fontsize=7, color=INK2, ha="right", va="top")
    ax.set_xticks(range(4), ["F1 bad deploy", "F2 hot pod", "F3 scaled to 0", "F4 surge"])
    ax.set_ylabel("MTTR (s)", color=INK2); style(ax)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=COLOR[t]) for t in TAGS],
              labels=[LABEL[t] for t in TAGS], ncol=5, fontsize=8, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, 1.12))
    finish(fig, os.path.join(figdir, "fig1_mttr_box.png"), "MTTR per fault type and policy")

    # 2. Kaplan–Meier
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    km_lab = []
    for t in TAGS:
        eps = [e for e in data[t] if e["fault"] != "NULL"]
        xs, ys, _ = km_curve([mttr_c(e) for e in eps], [int(e["recovered"]) for e in eps])
        ax.step(xs, ys, where="post", color=COLOR[t], lw=2)
        km_lab.append((ys[-1], LABEL[t]))
    end_labels(ax, km_lab, CENSOR + 4, 0.05)
    ax.set_xlim(0, CENSOR + 80); ax.set_ylim(-0.03, 1.03)
    ax.set_xlabel("seconds since injection", color=INK2); ax.set_ylabel("fraction not yet recovered", color=INK2)
    style(ax)
    finish(fig, os.path.join(figdir, "fig2_kaplan_meier.png"), "Time to recovery (Kaplan–Meier, 24 fault episodes)")

    # 3. Recovery rate
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for i, t in enumerate(TAGS):
        vals = [np.mean([e["recovered"] for e in data[t] if e["fault"] == f]) for f in FAULTS]
        pos = [j + (i - 2) * W for j in range(4)]
        ax.bar(pos, vals, width=W - 0.02, color=COLOR[t], label=LABEL[t])
    ax.set_xticks(range(4), ["F1", "F2", "F3", "F4"]); ax.set_ylim(0, 1.08)
    ax.set_ylabel("recovered within 18 ticks", color=INK2); style(ax)
    ax.legend(ncol=5, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.12))
    finish(fig, os.path.join(figdir, "fig3_recovery_rate.png"), "Recovery rate per fault type")

    # 4. Σv per episode
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for i, t in enumerate(TAGS):
        v = [e["sum_v"] for e in data[t] if e["fault"] != "NULL"]
        jit = rng.uniform(-0.18, 0.18, len(v))
        ax.scatter(np.full(len(v), i) + jit, v, s=16, color=COLOR[t], alpha=0.85, linewidths=0)
        ax.hlines(np.median(v), i - 0.3, i + 0.3, color=INK, lw=1.5)
        ax.text(i + 0.32, np.median(v), f"{np.median(v):.2f}", fontsize=7.5, color=INK2, va="center")
    ax.set_xticks(range(5), [LABEL[t] for t in TAGS]); ax.set_ylabel("Σ v over the episode", color=INK2)
    style(ax)
    finish(fig, os.path.join(figdir, "fig4_sla_penalty.png"), "Cumulative SLA penalty per fault episode (bar = median)")

    # 5. learning curves
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    lc0, lc1 = [], []
    for t in ("sac_uniform_s0", "sac_per_s0"):
        with open(os.path.join(a.runs, t, "train_episodes.csv")) as fh:
            tr = list(csv.DictReader(fh))
        ep = np.array([int(r["episode"]) for r in tr])
        ret = np.array([float(r["return"]) for r in tr])
        rec = np.array([np.nan if r["fault"] == "NULL" else float(r["recovered"]) for r in tr])
        k = 25
        rr = [np.mean(ret[max(0, i - k + 1):i + 1]) for i in range(len(ret))]
        rc = [np.nanmean(rec[max(0, i - k + 1):i + 1]) for i in range(len(rec))]
        axs[0].plot(ep, rr, color=COLOR[t], lw=2)
        axs[1].plot(ep, rc, color=COLOR[t], lw=2)
        lc0.append((rr[-1], LABEL[t])); lc1.append((rc[-1], LABEL[t]))
    end_labels(axs[0], lc0, 304, 1.2)
    end_labels(axs[1], lc1, 304, 0.05)
    axs[0].set_title("rolling return (25 ep)", fontsize=9, color=INK2, loc="left")
    axs[1].set_title("rolling recovery rate (25 ep, fault episodes)", fontsize=9, color=INK2, loc="left")
    for ax in axs:
        ax.set_xlabel("training episode", color=INK2); ax.set_xlim(0, 360); style(ax)
    finish(fig, os.path.join(figdir, "fig5_learning_curves.png"),
           "Learning curves, uniform vs PER (seed 0 only; stochastic policy)")

    # 6. fault × action heat map
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.0), sharey=True)
    rowsF = [("F1", None), ("F2", "cartservice"), ("F2", "currencyservice"), ("F3", "cartservice"),
             ("F3", "currencyservice"), ("F4", None), ("NULL", None)]
    rlab = ["F1 pc", "F2 cart", "F2 cur", "F3 cart", "F3 cur", "F4", "NULL"]
    alab = [x[2] for x in ACTIONS[1:]]
    for ax, t in zip(axs, ("runbook", "sac_per_s0")):
        M = np.zeros((len(rowsF), len(alab)))
        for e in data[t]:
            r = next(i for i, (f, tg) in enumerate(rowsF) if f == e["fault"] and (tg is None or tg == e["target"]))
            for x in e["actions"]:
                M[r, alab.index(x["action"])] += 1
        n_eps = np.array([sum(1 for e in data[t] if e["fault"] == f and (tg is None or tg == e["target"]))
                          for f, tg in rowsF])[:, None]
        M = M / n_eps
        ax.imshow(M, cmap="Blues", vmin=0, vmax=2, aspect="auto")
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                if M[i, j] > 0:
                    ax.text(j, i, f"{M[i, j]:.1f}", ha="center", va="center", fontsize=7,
                            color="#fcfcfb" if M[i, j] > 1.0 else INK)
        for i, (f, tg) in enumerate(rowsF):
            for j, lab in enumerate(alab):
                if lab in useful(f, tg) and f != "F4" or (f == "F4" and lab == "SCALE_UP fe"):
                    ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec="#eb6834", lw=1.5))
        ax.set_xticks(range(len(alab)), alab, rotation=45, ha="right", fontsize=7.5)
        ax.set_yticks(range(len(rlab)), rlab, fontsize=8)
        ax.set_title(f"{LABEL[t]} — actions per episode", fontsize=9, color=INK2, loc="left")
        ax.tick_params(colors=INK2)
    fig.text(0.99, 0.94, "orange box = curative action in §5.2 outcome grid", fontsize=7.5, color=INK2, ha="right")
    finish(fig, os.path.join(figdir, "fig6_action_heatmap.png"), "Fault × action frequency (greedy evaluation)")

    # 7. FRR + wasted-action rate
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8))
    by = {(r["tag"], r["fault"]): r for r in rows}
    for ax, key, ttl in ((axs[0], "frr", "false remediation rate (NULL episodes)"),
                         (axs[1], "wasted_action_rate", "wasted-action rate (all episodes)")):
        vals = []
        for t in TAGS:
            if key == "frr":
                vals.append(float(by[(t, "NULL")]["frr"]))
            else:
                acts = [x for e in data[t] for x in e["actions"]]
                wst = sum(1 for e in data[t] for x in e["actions"]
                          if x["phase"] == "lead_in" or x["action"] not in useful(e["fault"], e["target"]))
                vals.append(wst / len(acts) if acts else 0.0)
        ax.bar(range(5), vals, color=[COLOR[t] for t in TAGS], width=0.6)
        for i, v in enumerate(vals):
            ax.text(i, v + 0.01, f"{v:.2f}", ha="center", fontsize=8, color=INK2)
        ax.set_xticks(range(5), [LABEL[t] for t in TAGS], fontsize=7.5, rotation=20)
        ax.set_ylim(0, max(0.2, max(vals) * 1.25)); ax.set_title(ttl, fontsize=9, color=INK2, loc="left")
        style(ax)
    finish(fig, os.path.join(figdir, "fig7_frr_wasted.png"), "Operational overhead")
    print("analysis written to", a.out)


if __name__ == "__main__":
    main()
