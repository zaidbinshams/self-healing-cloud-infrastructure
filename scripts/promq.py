"""Run the contract PromQL queries (CLAUDE.md §5.4) at B's current wall time. Read-only.

Usage:  source config/cluster.env
        python -m scripts.promq --all
        python -m scripts.promq --query thr --deployment currencyservice

Exit code 0 iff every requested (query, deployment) has a finite value.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

from env.telemetry import build_queries
from env.telemetry import fetch_prom as run_query
from scripts._kube import load_contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--query", choices=["cpu", "thr", "mem"])
    parser.add_argument("--deployment", help="only report this managed deployment")
    parser.add_argument("--show", action="store_true", help="print the PromQL text")
    args = parser.parse_args(argv)

    contract = load_contract()
    managed: list[str] = contract["cluster"]["managed"]
    if args.deployment and args.deployment not in managed:
        parser.error(f"--deployment must be one of {managed}")
    targets = [args.deployment] if args.deployment else managed
    prom_url = os.environ.get("PROM_URL")
    if not prom_url:
        parser.error("PROM_URL unset (source config/cluster.env)")
    timeout_s = tuple(contract["telemetry"]["prom_timeout_s"])
    queries = build_queries(contract["telemetry"]["rate_window"])
    names = ["cpu", "thr", "mem"] if args.all else [args.query]

    eval_time_s = time.time()   # B is the clock authority (CLAUDE.md §2)
    bad = 0
    for name in names:
        if args.show:
            print(f"# Q_{name}\n{queries[name]}")
        res = run_query(prom_url, name, queries[name], eval_time_s, timeout_s, managed)
        if not res.ok:
            print(f"{name:4s} ERROR {res.error}")
            bad += len(targets)
            continue
        for dep in targets:
            val = res.values.get(dep)
            if val is None:
                print(f"{name:4s} {dep:22s} MISSING")
                bad += 1
            elif not math.isfinite(val):
                print(f"{name:4s} {dep:22s} {val} (non-finite)")
                bad += 1
            else:
                print(f"{name:4s} {dep:22s} {val:.6g}")
    print(f"evaluated at time={eval_time_s:.3f} (B wall clock)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
