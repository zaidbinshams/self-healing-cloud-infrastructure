"""Interim training progress report (for status meetings). Read-only over data/; nothing is simulated.

From agents/train.py logs (data/m4/<run>/episodes.jsonl, probes.jsonl), and from the runbook validation
(data/m3/runbook_eval/episodes.jsonl) as the reference point, writes into --out:
    progress.md   per-run summary, rolling learning signals, greedy-probe table, runbook reference
    progress.png  return (rolling mean), fault-recovery rate (rolling), alpha, actions in NULL episodes
Interim numbers are training diagnostics, not pre-registered evaluation results; label them as such.

Usage:  python -m scripts.training_report [--run sac_per_s0 --run sac_per_cold_s0] [--out data/m4/report]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from env.contract import REPO_ROOT
from eval.plots import INK_2, STYLE

ROLL = 10


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def rolling(xs: list[float], n: int = ROLL) -> np.ndarray:
    return np.convolve(xs, np.ones(n) / n, mode="valid") if len(xs) >= n else np.array([])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and rates over finished (non-interrupted) episodes. Pure."""
    done = [r for r in rows if not r.get("interrupted")]
    faults = [r for r in done if r["fault"] != "NULL"]
    nulls = [r for r in done if r["fault"] == "NULL"]
    mttr = [r["mttr_s"] for r in faults if r.get("recovered") and r.get("mttr_s") is not None]
    return {"episodes": len(done), "fault_episodes": len(faults),
            "recovered": sum(bool(r.get("recovered")) for r in faults),
            "recovery_rate": (sum(bool(r.get("recovered")) for r in faults) / len(faults)) if faults else None,
            "mttr_median_s": statistics.median(mttr) if mttr else None,
            "null_episodes": len(nulls), "null_with_action": sum(r.get("actions", 0) > 0 for r in nulls),
            "mean_return": statistics.mean(r["return"] for r in done) if done else None}


def probe_table(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for at in sorted({int(p["after_train_episodes"]) for p in probes}):
        out.append({"after_train_episodes": at, **summarize([p for p in probes if int(p["after_train_episodes"]) == at])})
    return out


def fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "–"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def figure(runs: dict[str, list[dict[str, Any]]], out: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(6.8, 4.4))
    panels = [("return", "return (10-ep mean)"), ("recovered", "fault recovery rate (10 fault eps)"),
              ("alpha", "temperature α"), ("null_actions", "actions per NULL episode")]
    for ax, (key, title) in zip(axes.ravel(), panels, strict=True):
        for name, rows in runs.items():
            done = [r for r in rows if not r.get("interrupted")]
            label, c, ls, _ = STYLE.get(name.replace("_s0", ""), (name, INK_2, "-", "o"))
            if key == "return":
                y = rolling([r["return"] for r in done]); x = np.arange(ROLL, len(done) + 1)
            elif key == "recovered":
                f = [float(bool(r.get("recovered"))) for r in done if r["fault"] != "NULL"]
                y = rolling(f); x = np.arange(ROLL, len(f) + 1)
            elif key == "alpha":
                y = np.array([r["alpha"] for r in done]); x = np.arange(1, len(done) + 1)
            else:
                nl = [r for r in done if r["fault"] == "NULL"]
                y = np.array([r.get("actions", 0) for r in nl], float); x = np.arange(1, len(nl) + 1)
            if len(y):
                ax.plot(x, y, color=c, ls=ls, lw=1.3, label=label, marker="o" if key == "null_actions" else None, ms=3)
        ax.set_title(title, loc="left", fontsize=8, color=INK_2)
        ax.tick_params(labelsize=7)
    axes[0, 0].legend(frameon=False, fontsize=7)
    axes[1, 0].set_yscale("log")
    axes[1, 0].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.3g}"))
    axes[1, 0].yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    for ax in axes[1]:
        ax.set_xlabel("episode (NULL panel: NULL episode index)", fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "progress.png", dpi=200)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", default=None)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "m4" / "report")
    args = ap.parse_args(argv)
    names = args.run or ["sac_per_s0", "sac_per_cold_s0"]
    runs = {n: read(REPO_ROOT / "data" / "m4" / n / "episodes.jsonl") for n in names}
    runs = {n: r for n, r in runs.items() if r}
    if not runs:
        print("training_report: no training episodes yet", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    ref = summarize(read(REPO_ROOT / "data" / "m3" / "runbook_eval" / "episodes.jsonl"))
    lines = ["# Training progress (interim diagnostics, not evaluation results)", "",
             "| run | episodes | fault recovered | recovery rate | median MTTR (s) | NULL eps with action | mean return | α (last) |",
             "|---|---|---|---|---|---|---|---|"]
    for n, rows in runs.items():
        s = summarize(rows)
        lines.append(f"| {n} | {s['episodes']} | {s['recovered']}/{s['fault_episodes']} | {fmt(s['recovery_rate'])} | "
                     f"{fmt(s['mttr_median_s'], 0)} | {s['null_with_action']}/{s['null_episodes']} | "
                     f"{fmt(s['mean_return'])} | {fmt(rows[-1].get('alpha'), 4)} |")
    lines.append(f"| runbook validation (ε = 0, reference) | {ref['episodes']} | {ref['recovered']}/{ref['fault_episodes']} | "
                 f"{fmt(ref['recovery_rate'])} | {fmt(ref['mttr_median_s'], 0)} | {ref['null_with_action']}/{ref['null_episodes']} | "
                 f"{fmt(ref['mean_return'])} | – |")
    for n in runs:
        pt = probe_table(read(REPO_ROOT / "data" / "m4" / n / "probes.jsonl"))
        if pt:
            lines += ["", f"## Greedy probes: {n}", "",
                      "| after N training episodes | probe episodes | fault recovered | median MTTR (s) | NULL with action |",
                      "|---|---|---|---|---|"]
            lines += [f"| {p['after_train_episodes']} | {p['episodes']} | {p['recovered']}/{p['fault_episodes']} | "
                      f"{fmt(p['mttr_median_s'], 0)} | {p['null_with_action']}/{p['null_episodes']} |" for p in pt]
    lines += ["", "Figure: progress.png (return, recovery rate, α, actions in NULL episodes)."]
    (args.out / "progress.md").write_text("\n".join(lines) + "\n")
    figure(runs, args.out)
    print("\n".join(lines))
    print(f"\nwrote {args.out / 'progress.md'} and {args.out / 'progress.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
