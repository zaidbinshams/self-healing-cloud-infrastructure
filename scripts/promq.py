"""Run the contract PromQL queries (CLAUDE.md §5.4) at B's current wall time. Read-only.

Usage:  source config/cluster.env
        python -m scripts.promq --all
        python -m scripts.promq --query thr --deployment currencyservice

Exit code 0 iff every requested (query, deployment) has a finite value.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field

import requests

from scripts._kube import load_contract

_POD_TO_DEPLOYMENT = r'"deployment", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$"'
_SEL = '{namespace="boutique",container="server"}'


def _dep(expr: str) -> str:
    return f"label_replace({expr}, {_POD_TO_DEPLOYMENT})"


def build_queries(rate_window: str) -> dict[str, str]:
    """Q_cpu, Q_thr, Q_mem verbatim from CLAUDE.md §5.4 (rate window from the contract)."""
    rate = lambda metric: f"rate({metric}{_SEL}[{rate_window}])"
    by_dep = lambda expr: f"sum by (deployment) ({_dep(expr)})"
    return {
        "cpu": by_dep(rate("container_cpu_usage_seconds_total")),
        "thr": f"{by_dep(rate('container_cpu_cfs_throttled_periods_total'))}\n"
               f"      / {by_dep(rate('container_cpu_cfs_periods_total'))}",
        "mem": by_dep(f"container_memory_working_set_bytes{_SEL}"),
    }


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    query: str
    eval_time_s: float
    values: dict[str, float] = field(default_factory=dict)   # deployment -> value (may be NaN)
    error: str = ""


def run_query(prom_url: str, name: str, expr: str, eval_time_s: float,
              timeout_s: tuple[float, float], managed: list[str]) -> QueryResult:
    try:
        resp = requests.get(f"{prom_url.rstrip('/')}/api/v1/query",
                            params={"query": expr, "time": f"{eval_time_s:.3f}"}, timeout=timeout_s)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            return QueryResult(False, name, eval_time_s, error=f"status={body.get('status')} {body.get('error', '')}")
        values = {row["metric"].get("deployment", ""): float(row["value"][1])
                  for row in body["data"]["result"]}
    except requests.exceptions.RequestException as exc:
        return QueryResult(False, name, eval_time_s, error=f"{type(exc).__name__}: {exc}")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:   # JSONDecodeError is a ValueError
        return QueryResult(False, name, eval_time_s, error=f"parse {type(exc).__name__}: {exc}")
    return QueryResult(True, name, eval_time_s, {d: v for d, v in values.items() if d in managed})


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
