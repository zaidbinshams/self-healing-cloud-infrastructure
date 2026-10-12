"""Build the fixed evaluation schedule (docs/PREREGISTRATION.md §3; CLAUDE.md §4.4: committed, immutable).

`per_fault` episodes of each of F1–F4 plus `per_fault` NULL, with targets/severities balanced within each
fault (the combinations are cycled, the remainder assigned at random), lead-in L uniform in
`episode.lead_in_ticks`, and the 50 entries shuffled. Everything is drawn from one seeded RNG, so the
file is reproducible from (contract, seed, per_fault). Policies are interleaved at run time over this
order; the edge subset is the first `edge_per_fault` entries of each fault type, in schedule order.

Usage:  python -m eval.make_schedule --seed 2026 --per-fault 10 --out eval/schedules/eval_v1.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import time
from pathlib import Path
from typing import Any

from env.contract import Contract, load_contract
from env.injector import F1_TARGET, F4_TARGET

FAULTS = ("F1", "F2", "F3", "F4", "NULL")


def combos(c: Contract, fault: str) -> list[tuple[str | None, Any]]:
    e = c.episode
    if fault == "F1":
        return [(F1_TARGET, s) for s in e.f1_latency]
    if fault == "F2":
        return list(itertools.product(e.f2_targets, e.f2_workers))
    if fault == "F3":
        return [(t, None) for t in e.f3_targets]
    if fault == "F4":
        return [(F4_TARGET, m) for m in e.f4_multiplier]
    return [(None, None)]


def balanced(rng: random.Random, options: list[tuple[str | None, Any]], n: int) -> list[tuple[str | None, Any]]:
    """n draws covering every option floor(n/k) times; the n mod k extra options are chosen at random."""
    k = len(options)
    out = options * (n // k) + rng.sample(options, n % k)
    rng.shuffle(out)
    return out


def build_schedule(c: Contract, seed: int, per_fault: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    lo, hi = c.episode.lead_in_ticks
    entries = []
    for fault in FAULTS:
        for target, severity in balanced(rng, combos(c, fault), per_fault):
            entries.append({"fault": fault, "target": target, "severity": severity, "lead_in": rng.randint(lo, hi)})
    rng.shuffle(entries)
    return [{"index": i, **e} for i, e in enumerate(entries)]


def edge_subset(schedule: list[dict[str, Any]], edge_per_fault: int) -> list[int]:
    """Indices of the first `edge_per_fault` entries of each fault type (pre-registered, deterministic)."""
    seen: dict[str, int] = {}
    out = []
    for e in schedule:
        if seen.get(e["fault"], 0) < edge_per_fault:
            seen[e["fault"]] = seen.get(e["fault"], 0) + 1
            out.append(e["index"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--per-fault", type=int, required=True)
    ap.add_argument("--edge-per-fault", type=int, default=4)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists():
        print(f"make_schedule: {args.out} exists; schedules are immutable (CLAUDE.md §4.4)")
        return 1
    c = load_contract()
    sched = build_schedule(c, args.seed, args.per_fault)
    doc = {"seed": args.seed, "per_fault": args.per_fault, "contract_sha256": c.sha256,
           "generated_at": time.time(), "edge_subset": edge_subset(sched, args.edge_per_fault), "entries": sched}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {args.out}: {len(sched)} entries, edge subset {len(doc['edge_subset'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
