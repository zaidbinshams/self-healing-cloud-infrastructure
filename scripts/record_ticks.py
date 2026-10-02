"""Record real ticks from the cluster as unit-test fixtures (PLAN.md M3 step 1). Read-only.

Runs the M3 telemetry stack exactly as `step()` will: the TickClock schedule, concurrent
collection under the contract deadline, then §5.5 imputation. Each line of the output is
JSON: a header (provenance), then one record per tick with the raw collection (`raw`, the
fixture's ground truth), the imputed features and the stale flag. Lines are flushed every
tick into `<out>.partial`, which is renamed to `<out>` only when all ticks are recorded.

Exempt from the calibration requirement of CLAUDE.md §7 (human-approved 2026-10-02): it runs
before scripts/calibrate.py and records raw, unnormalized telemetry only.

Usage:  source config/cluster.env
        python -m scripts.record_ticks --n 30 --out tests/fixtures/ticks_steady.jsonl
"""

from __future__ import annotations

import argparse
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType

import requests

from env.clock import TickClock
from env.contract import (
    GOLDEN_LIVE_PATH,
    REPO_ROOT,
    ContractError,
    load_contract,
    sha256_file,
)
from env.recorder import EventLog
from env.telemetry import (
    Collector,
    ImputeState,
    dumps,
    features_to_dict,
    impute,
    make_api_client,
    raw_tick_to_dict,
)

COMPONENT = "record_ticks"
_stop = False


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True     # finish the current tick, then exit 0 (CLAUDE.md §6)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout.strip()


def _locust_users(locust_url: str, timeout_s: tuple[float, float], events: EventLog) -> int | None:
    try:
        resp = requests.get(f"{locust_url.rstrip('/')}/stats/requests", timeout=timeout_s)
        resp.raise_for_status()
        return int(resp.json()["user_count"])
    except requests.exceptions.RequestException as exc:
        events.emit("source_failed", component=f"{COMPONENT}.locust_users", error_type=type(exc).__name__,
                    tick=None, error=str(exc)[:300])
    except (ValueError, KeyError, TypeError) as exc:
        events.emit("source_failed", component=f"{COMPONENT}.locust_users",
                    error_type=f"parse:{type(exc).__name__}", tick=None, error=str(exc)[:300])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, required=True, help="number of ticks to record")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run", default=f"record_ticks-{time.strftime('%Y%m%dT%H%M%S')}")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be >= 1")
    missing = [v for v in ("KUBE_AGENT", "PROM_URL", "LOCUST_URL") if not os.environ.get(v)]
    if missing:
        parser.error(f"unset {missing} (source config/cluster.env)")

    try:
        contract = load_contract()
    except ContractError as exc:
        print(f"record_ticks: {exc}", file=sys.stderr)
        return 2

    events = EventLog(REPO_ROOT / "data" / "logs" / f"{args.run}.events.jsonl")
    collector = Collector(contract, os.environ["PROM_URL"], os.environ["LOCUST_URL"],
                          make_api_client(os.environ["KUBE_AGENT"]), events)
    clock = TickClock(contract.clock.tick_s, contract.clock.late_frac)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    header = {
        "kind": "header", "run": args.run, "created_at": time.time(), "n_requested": args.n,
        "contract_sha256": contract.sha256, "golden_live_sha256": sha256_file(GOLDEN_LIVE_PATH),
        "git_sha": _git("rev-parse", "HEAD"), "git_dirty": bool(_git("status", "--porcelain")),
        "managed": list(contract.cluster.managed), "tick_s": contract.clock.tick_s,
        "collect_deadline_s": contract.clock.collect_deadline_s,
        "locust_users": _locust_users(os.environ["LOCUST_URL"], contract.telemetry.locust_timeout_s, events),
    }
    partial = args.out.with_name(args.out.name + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    events.emit("run_start", component=COMPONENT, tick=None, out=str(args.out), n=args.n)

    state = ImputeState()
    stale_ticks: list[int] = []
    collect_s: list[float] = []
    overrun_s: list[float] = []
    recorded = 0
    with partial.open("w", encoding="utf-8") as fh:
        fh.write(dumps(header) + "\n")
        fh.flush()
        clock.anchor()
        for k in range(args.n):
            if _stop:
                break
            boundary = clock.wait_boundary(k + 1)
            raw = collector.collect(k, clock.wall(k), clock.wall(k + 1))
            features, state = impute(raw, state, contract)
            fh.write(dumps({"kind": "tick", "tick": k, "boundary_overrun_s": boundary.overrun_s,
                            "stale": features.stale, "raw": raw_tick_to_dict(raw),
                            "features": features_to_dict(features)}) + "\n")
            fh.flush()
            recorded += 1
            collect_s.append(raw.collect_s)
            overrun_s.append(boundary.overrun_s)
            if features.stale:
                stale_ticks.append(k)
            events.emit("tick", component=COMPONENT, tick=k, stale=features.stale, collect_s=raw.collect_s,
                        imputed=list(features.imputed), failed_sources=list(features.failed_sources))
    collector.close()

    complete = recorded == args.n
    if complete:
        partial.replace(args.out)
    events.emit("run_end", component=COMPONENT, tick=None, recorded=recorded, complete=complete,
                stale_ticks=stale_ticks, counters=dict(collector.counters))
    events.close()

    def p95(xs: list[float]) -> float:
        return sorted(xs)[max(0, round(0.95 * len(xs)) - 1)] if xs else float("nan")

    print(f"recorded {recorded}/{args.n} ticks -> {args.out if complete else partial}")
    print(f"stale ticks: {len(stale_ticks)}/{recorded} {stale_ticks}")
    if collect_s:
        print(f"collect_s: median {statistics.median(collect_s):.3f}  p95 {p95(collect_s):.3f}  "
              f"max {max(collect_s):.3f}  (deadline {contract.clock.collect_deadline_s})")
        print(f"boundary overrun_s: median {statistics.median(overrun_s):.4f}  max {max(overrun_s):.4f}")
    print(f"source failures: {dict(collector.counters) or 'none'}")
    return 0 if complete or _stop else 1    # a signalled early stop exits 0 (CLAUDE.md §6)


if __name__ == "__main__":
    sys.exit(main())
