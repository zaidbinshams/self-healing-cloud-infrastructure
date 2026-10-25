"""Pre-registered analysis of the evaluation and training runs (docs/PREREGISTRATION.md §1, §4–6).

Input rows: eval/run_eval.py episodes.jsonl (one row per attempt) and agents/train.py episodes.jsonl.
Only the final valid attempt of each (entry, policy) counts; exclusions are counted per policy.

    H1  sac_per vs k8s_default, k8s_hpa: MTTR per fault type (Mann–Whitney, Holm per fault family)
    H2  sac_per vs runbook: MTTR non-inferiority (CI upper bound < 20 s) and SLA penalty
    H3  sac_per vs sac_per_cold: training return over episodes 1–100 and evaluation MTTR
    H4  sac_per: 0 safety violations; NULL episodes with an action (eval) and FRR over training NULL episodes
    H5  edge subset: sac_per − runbook MTTR gap does not widen beyond the margin
Plus pooled Kaplan–Meier / log-rank over F1–F4 and per-policy summaries.
    seed1  (Amendment 8, replication) H2 rule within the seed-1 block, H4, runbook-anchor drift, seed 1 vs 0

Usage:  python -m eval.analyze --main data/eval/main [--edge data/eval/edge] \\
            [--train sac_per=data/m4/sac_per_s0 --train sac_per_cold=data/m4/sac_per_cold_s0] \\
            [--seed1 sac_per_s1=data/eval/seed1=data/m4/sac_per_s1] --out data/eval/analysis.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from eval import stats

FAULTS = ("F1", "F2", "F3", "F4")
SEED = 2026
EARLY_EPISODES = 100                    # H3: return over training episodes 1–100


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def final_valid(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Last valid attempt per (index, policy); excluded = attempts that were invalid, per policy."""
    best: dict[tuple[int, str], dict[str, Any]] = {}
    excluded: dict[str, int] = {}
    for r in rows:
        if r.get("interrupted"):
            continue
        if not r["valid"]:
            excluded[r["policy"]] = excluded.get(r["policy"], 0) + 1
            continue
        best[(int(r["index"]), r["policy"])] = r
    return list(best.values()), excluded


def by(rows: list[dict[str, Any]], policy: str, fault: str | None = None) -> list[dict[str, Any]]:
    return [r for r in rows if r["policy"] == policy and (fault is None or r["fault"] == fault)]


def mttr(rows: list[dict[str, Any]]) -> list[float]:
    return [float(r["mttr_censored_s"]) for r in rows]


def summary(rows: list[dict[str, Any]], policies: list[str]) -> list[dict[str, Any]]:
    out = []
    for p in policies:
        for f in (*FAULTS, "NULL"):
            rs = by(rows, p, f)
            if not rs:
                continue
            out.append({"policy": p, "fault": f, "n": len(rs),
                        "recovered": sum(bool(r["recovered"]) for r in rs) if f != "NULL" else None,
                        "mttr_median_s": float(np.median(mttr(rs))) if f != "NULL" else None,
                        "sla_penalty_mean": float(np.mean([r["sla_penalty"] for r in rs])),
                        "actions_mean": float(np.mean([r["actions"] for r in rs])),
                        "false_remediations": sum(bool(r["false_remediation"]) for r in rs) if f == "NULL" else None,
                        "mask_violations": sum(int(r["mask_violations"]) for r in rs),
                        "exec_errors": sum(int(r["exec_errors"]) for r in rs)})
    return out


def as_dicts(cs: list[stats.Comparison]) -> list[dict[str, Any]]:
    return [vars(c) | {"non_inferior": stats.non_inferior(c)} for c in cs]


def h1(rows: list[dict[str, Any]], policies: list[str]) -> dict[str, Any]:
    out = {}
    for f in FAULTS:
        pairs = [("sac_per", b) for b in ("k8s_default", "k8s_hpa") if b in policies and by(rows, b, f)]
        if by(rows, "sac_per", f) and pairs:
            g = {p: mttr(by(rows, p, f)) for p in {"sac_per", *(b for _, b in pairs)}}
            out[f] = as_dicts(stats.compare(g, pairs, SEED))
    return out


def h2(rows: list[dict[str, Any]], tag: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in (*FAULTS, "pooled"):
        a = by(rows, "sac_per", None if f == "pooled" else f)
        b = by(rows, "runbook", None if f == "pooled" else f)
        a, b = [r for r in a if r["fault"] != "NULL"], [r for r in b if r["fault"] != "NULL"]
        if a and b:
            mt = stats.compare({"sac_per": mttr(a), "runbook": mttr(b)}, [("sac_per", "runbook")], SEED)
            pen = stats.compare({"sac_per": [r["sla_penalty"] for r in a], "runbook": [r["sla_penalty"] for r in b]},
                                [("sac_per", "runbook")], SEED + 1)
            out[f] = {"mttr": as_dicts(mt)[0], "sla_penalty": as_dicts(pen)[0]}
    return {"tag": tag, **out}


def h3(rows: list[dict[str, Any]], train: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if {"sac_per", "sac_per_cold"} <= set(train):
        early = {k: [r["return"] for r in v if not r.get("interrupted")][:EARLY_EPISODES] for k, v in train.items()}
        out["train_return_1_100"] = as_dicts(stats.compare(early, [("sac_per", "sac_per_cold")], SEED))[0]
    a = [r for r in by(rows, "sac_per") if r["fault"] != "NULL"]
    b = [r for r in by(rows, "sac_per_cold") if r["fault"] != "NULL"]
    if a and b:
        out["eval_mttr"] = as_dicts(stats.compare({"sac_per": mttr(a), "sac_per_cold": mttr(b)},
                                                  [("sac_per", "sac_per_cold")], SEED))[0]
    return out


def h4(rows: list[dict[str, Any]], train: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    ev = by(rows, "sac_per")
    nulls = [r for r in ev if r["fault"] == "NULL"]
    tr = [r for r in train.get("sac_per", []) if r["fault"] == "NULL" and not r.get("interrupted")]
    return {"eval_mask_violations": sum(int(r["mask_violations"]) for r in ev),
            "eval_exec_errors": sum(int(r["exec_errors"]) for r in ev),
            "eval_null_episodes": len(nulls), "eval_null_with_action": sum(bool(r["false_remediation"]) for r in nulls),
            "train_null_episodes": len(tr), "train_frr": (sum(r["actions"] > 0 for r in tr) / len(tr)) if tr else None}


def km_pooled(rows: list[dict[str, Any]], policies: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"curves": {}, "logrank_vs_sac_per": {}}
    faulted = {p: [r for r in by(rows, p) if r["fault"] != "NULL"] for p in policies}
    for p, rs in faulted.items():
        if rs:
            t, s = stats.kaplan_meier(mttr(rs), [bool(r["event_observed"]) for r in rs])
            out["curves"][p] = {"t": t.tolist(), "survival": s.tolist(), "n": len(rs)}
    a = faulted.get("sac_per", [])
    for p, rs in faulted.items():
        if p != "sac_per" and a and rs:
            out["logrank_vs_sac_per"][p] = stats.logrank(mttr(a), [bool(r["event_observed"]) for r in a],
                                                         mttr(rs), [bool(r["event_observed"]) for r in rs])
    return out


def replication(seed_rows: list[dict[str, Any]], main_rows: list[dict[str, Any]], agent: str,
                train: list[dict[str, Any]]) -> dict[str, Any]:
    """Amendment 8: a second training seed (agent) against a same-block runbook anchor.

    H2's rule within the block, H4 for the agent, anchor-vs-main runbook drift (descriptive), and the
    agent against main-evaluation sac_per (seed 0 vs seed 1)."""
    rows, excl = final_valid(seed_rows)
    main, _ = final_valid(main_rows)
    as_sac = [r | {"policy": "sac_per"} if r["policy"] == agent else r for r in rows]
    drift, seeds = {}, {}
    for f in (*FAULTS, "pooled"):
        sel = None if f == "pooled" else f
        anchor = [r for r in by(rows, "runbook", sel) if r["fault"] != "NULL"]
        ref = [r for r in by(main, "runbook", sel) if r["fault"] != "NULL"]
        s1 = [r for r in by(rows, agent, sel) if r["fault"] != "NULL"]
        s0 = [r for r in by(main, "sac_per", sel) if r["fault"] != "NULL"]
        if anchor and ref:
            drift[f] = as_dicts(stats.compare({"anchor": mttr(anchor), "main": mttr(ref)}, [("anchor", "main")], SEED))[0]
        if s1 and s0:
            seeds[f] = as_dicts(stats.compare({agent: mttr(s1), "sac_per": mttr(s0)}, [(agent, "sac_per")], SEED))[0]
    return {"agent": agent, "excluded_attempts": excl, "summary": summary(rows, sorted({r["policy"] for r in rows})),
            "H2": h2(as_sac, "seed1"), "H4": h4(as_sac, {"sac_per": train}), "anchor_drift": drift,
            "seed1_vs_seed0": seeds}


def analyze(main: list[dict[str, Any]], edge: list[dict[str, Any]], train: dict[str, list[dict[str, Any]]]
            ) -> dict[str, Any]:
    rows, excl = final_valid(main)
    policies = sorted({r["policy"] for r in rows})
    out: dict[str, Any] = {"policies": policies, "excluded_attempts": excl, "summary": summary(rows, policies),
                           "H1": h1(rows, policies), "H2": h2(rows, "main"), "H3": h3(rows, train),
                           "H4": h4(rows, train), "km": km_pooled(rows, policies)}
    if edge:
        erows, eexcl = final_valid(edge)
        out["H5_edge"] = {"excluded_attempts": eexcl, "summary": summary(erows, sorted({r["policy"] for r in erows})),
                          "gap": h2(erows, "edge")}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main", type=Path, required=True, help="run_eval --out dir of the main evaluation")
    ap.add_argument("--edge", type=Path, default=None)
    ap.add_argument("--train", action="append", default=[], help="name=train out dir (episodes.jsonl)")
    ap.add_argument("--seed1", default=None, help="agent=eval dir=train dir (Amendment 8 replication block)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    train = {k: read_rows(Path(v) / "episodes.jsonl") for k, v in (t.split("=", 1) for t in args.train)}
    main_rows = read_rows(args.main / "episodes.jsonl")
    res = analyze(main_rows, read_rows(args.edge / "episodes.jsonl") if args.edge else [], train)
    if args.seed1:
        agent, ev, tr = args.seed1.split("=", 2)
        res["seed1"] = replication(read_rows(Path(ev) / "episodes.jsonl"), main_rows, agent,
                                   read_rows(Path(tr) / "episodes.jsonl"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, default=float))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
