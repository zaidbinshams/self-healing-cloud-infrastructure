"""Paper A figure and table data as CSV (paper_a/figure_data/), from the analysis JSON and recorded runs.

Derived values only (per-episode metric rows, summaries, test results, curves); raw transitions stay in data/.

    summary_<block>.csv     per policy × fault: n, recovered, median MTTR (censored, and over recovered only), SLA
                            penalty, actions, NULL episodes with an action
    tests.csv               every pre-registered comparison (H1–H5) and the seed-1 replication, one row each
    km_curves.csv           pooled Kaplan–Meier steps per policy (main evaluation)
    episodes_<block>.csv    final valid attempt per (entry, policy)
    actions_<block>.csv     executed action sequence per policy × fault/target (counts)
    training.csv            per training episode: run, return, recovered, MTTR, actions, stale ticks
    context_<block>.csv     per evaluation block: decision latency and lead-in latency (server p95, client p99)

Usage:  python -m eval.papera_export --analysis data/eval/analysis.json --out paper_a/figure_data
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from eval.analyze import FAULTS, final_valid, read_rows

BLOCKS = {"main": "data/eval/eval_v1_main", "edge": "data/eval/eval_v1_edge", "seed1": "data/eval/eval_v1_seed1"}
TRAIN = {"sac_per_s0": "data/m4/sac_per_s0", "sac_per_s1": "data/m4/sac_per_s1",
         "sac_per_cold_s0": "data/m4/sac_per_cold_s0"}
ACTION_NAMES = ("NOOP", "RESTART frontend", "RESTART cartservice", "RESTART currencyservice",
                "RESTART productcatalogservice", "SCALE_UP frontend", "SCALE_DOWN frontend", "SCALE_UP cartservice",
                "SCALE_UP currencyservice", "RESTORE productcatalogservice", "RESTORE cartservice",
                "RESTORE currencyservice")
EPISODE_COLS = ("index", "policy", "fault", "target", "severity", "recovered", "mttr_censored_s", "sla_penalty",
                "actions", "false_remediation", "stale_ticks", "decision_latency_p95_s", "attempt")


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def sig4(x: Any) -> Any:
    return float(f"{float(x):.4g}") if isinstance(x, float | np.floating) else x      # 4 significant digits


def summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in sorted({r["policy"] for r in rows}):
        for f in (*FAULTS, "NULL"):
            rs = [r for r in rows if r["policy"] == p and r["fault"] == f]
            if not rs:
                continue
            rec = [r["mttr_censored_s"] for r in rs if r["recovered"]]
            out.append({"policy": p, "fault": f, "n": len(rs),
                        "recovered": sum(bool(r["recovered"]) for r in rs) if f != "NULL" else "",
                        "mttr_median_censored_s": sig4(np.median([r["mttr_censored_s"] for r in rs])) if f != "NULL" else "",
                        "mttr_median_recovered_s": sig4(np.median(rec)) if rec else "",
                        "sla_penalty_mean": sig4(np.mean([r["sla_penalty"] for r in rs])),
                        "actions_mean": sig4(np.mean([r["actions"] for r in rs])),
                        "null_with_action": sum(bool(r["false_remediation"]) for r in rs) if f == "NULL" else ""})
        faulted = [r for r in rows if r["policy"] == p and r["fault"] != "NULL"]
        rec = [r["mttr_censored_s"] for r in faulted if r["recovered"]]
        out.append({"policy": p, "fault": "F1-F4", "n": len(faulted), "recovered": len(rec),
                    "mttr_median_censored_s": sig4(np.median([r["mttr_censored_s"] for r in faulted])),
                    "mttr_median_recovered_s": sig4(np.median(rec)) if rec else "",
                    "sla_penalty_mean": sig4(np.mean([r["sla_penalty"] for r in faulted])),
                    "actions_mean": sig4(np.mean([r["actions"] for r in faulted])), "null_with_action": ""})
    return out


def tests(a: dict[str, Any]) -> list[dict[str, Any]]:
    out = []

    def add(hyp: str, scope: str, metric: str, c: dict[str, Any]) -> None:
        out.append({"hypothesis": hyp, "scope": scope, "metric": metric} | {k: sig4(v) for k, v in c.items()})

    for f, cs in a["H1"].items():
        for c in cs:
            add("H1", f, "mttr", c)
    for tag, block in (("H2", a["H2"]), ("H5", a.get("H5_edge", {}).get("gap", {})),
                       ("seed1-H2", a.get("seed1", {}).get("H2", {}))):
        for f, v in block.items():
            if f != "tag":
                add(tag, f, "mttr", v["mttr"])
                add(tag, f, "sla_penalty", v["sla_penalty"])
    for k, c in a["H3"].items():
        add("H3", k, "return" if k.startswith("train") else "mttr", c)
    for tag, key in (("seed1-drift", "anchor_drift"), ("seed1-vs-seed0", "seed1_vs_seed0")):
        for f, c in a.get("seed1", {}).get(key, {}).items():
            add(tag, f, "mttr", c)
    return out


def actions(block_dir: Path, rows: list[dict[str, Any]], transitions: Path) -> list[dict[str, Any]]:
    seqs: dict[tuple[str, str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        ts = read_rows(transitions / f"episode_{r['episode']}.jsonl")
        seq = " > ".join(ACTION_NAMES[t["a_exec"]] for t in ts if t["a_exec"] != 0) or "none"
        seqs[(r["policy"], r["fault"], r["target"] or "")][seq] += 1
    return [{"policy": p, "fault": f, "target": t, "executed_actions": s, "episodes": n}
            for (p, f, t), c in sorted(seqs.items()) for s, n in c.most_common()]


def context(block: str, transitions: Path) -> dict[str, Any]:
    dl, server, client = [], [], []
    for path in transitions.glob("episode_*.jsonl"):
        for t in read_rows(path):
            dl.append(t["decision_latency_s"])
            if t["phase"] == "lead_in" and not t["stale"]:
                if t["raw"]["server"].get("ok"):
                    server.append(t["raw"]["server"]["sla_ms"])
                if t["raw"]["locust"].get("ok"):
                    client.append(t["raw"]["locust"]["p99_ms"])
    d = np.asarray(dl, float)
    return {"block": block, "ticks": d.size, "decision_latency_median_s": sig4(np.median(d)),
            "decision_latency_p95_s": sig4(np.percentile(d, 95)), "decision_latency_max_s": sig4(d.max()),
            "lead_in_ticks": len(server), "lead_in_server_p95_median_ms": sig4(np.median(server)),
            "lead_in_client_p99_median_ms": sig4(np.median(client)),
            "lead_in_client_p99_p95_ms": sig4(np.percentile(client, 95))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    a = json.loads(args.analysis.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    ctx = []
    for block, d in BLOCKS.items():
        rows, _ = final_valid(read_rows(Path(d) / "episodes.jsonl"))
        rows.sort(key=lambda r: (r["index"], r["policy"]))
        transitions = Path("data/transitions") / Path(d).name
        write(args.out / f"summary_{block}.csv", summary(rows))
        write(args.out / f"episodes_{block}.csv", [{k: sig4(r.get(k)) for k in EPISODE_COLS} for r in rows])
        write(args.out / f"actions_{block}.csv", actions(Path(d), rows, transitions))
        ctx.append(context(block, transitions))
    write(args.out / "context.csv", ctx)
    write(args.out / "tests.csv", tests(a))
    write(args.out / "km_curves.csv", [{"policy": p, "t_s": sig4(t), "p_not_recovered": sig4(s), "n": c["n"]}
                                       for p, c in a["km"]["curves"].items()
                                       for t, s in zip(c["t"], c["survival"], strict=True)])
    write(args.out / "training.csv", [{"run": run, "episode": r["episode"], "fault": r["fault"],
                                       "target": r["target"] or "", "severity": r["severity"],
                                       "return": sig4(r["return"]), "recovered": r["recovered"],
                                       "mttr_s": sig4(r["mttr_s"]) if r["mttr_s"] is not None else "",
                                       "actions": r["actions"], "stale_ticks": r["stale_ticks"]}
                                      for run, d in TRAIN.items() for r in read_rows(Path(d) / "episodes.jsonl")
                                      if not r.get("interrupted") and not r.get("probe")])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
