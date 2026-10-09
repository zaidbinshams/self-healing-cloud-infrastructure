"""Proof of concept: per-tick frontend server-side latency over the hotspot (G6 option 1). Read-only.

Question: can the env read every frontend pod's request log for the tick window
(wall(T_k), wall(T_{k+1})], parse `http.resp.took_ms` and compute server p99 **within the 3.0 s
collection deadline**, every tick, with complete data?

Per tick (on the env's TickClock schedule) it fetches the logs of all frontend pods concurrently
(since_seconds = tick + margin, raw bytes, contract k8s timeout), parses the window, and records:
fetch+parse wall time, bytes, server request count vs Locust's count (completeness; container
log rotation truncates reads), server p99 vs Locust client p99.

Uses KUBE_ADMIN only because the agent role lacks `pods/log` today; the API path
(B -> API server -> kubelet on A) and therefore the timing are identical for any credential.

Usage:  source config/cluster.env
        python -m scripts.server_latency_poc --ticks 30 --out data/diag/server_latency_poc.jsonl
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.clock import TickClock
from env.contract import load_contract
from env.telemetry import make_api_client

LOG_MARGIN_S = 3                  # since_seconds = tick_s + margin covers the window plus clock skew
HEALTH_PATH = "/_healthz"


def parse_ts(ts: str) -> float:
    head, frac = ts.rstrip("Z").split(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}").replace(tzinfo=timezone.utc).timestamp()


def window_latencies(text: str, t0: float, t1: float) -> list[float]:
    out = []
    for line in text.splitlines():
        if '"request complete"' not in line:             # cheap prefilter before JSON parsing
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("http.req.path") == HEALTH_PATH:
            continue
        t = parse_ts(rec["timestamp"])
        if t0 < t <= t1:
            out.append(float(rec["http.resp.took_ms"]))
    return out


def p99(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, math.ceil(0.99 * len(s)) - 1)] if s else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticks", type=int, default=30)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    c = load_contract()
    ns, kt, deadline = c.cluster.namespace, c.telemetry.k8s_timeout_s, c.clock.collect_deadline_s
    core = client.CoreV1Api(make_api_client(os.environ["KUBE_ADMIN"]))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    clock = TickClock(c.clock.tick_s, c.clock.late_frac)
    clock.anchor()
    since = int(c.clock.tick_s) + LOG_MARGIN_S
    rows: list[dict[str, Any]] = []

    def fetch(pod: str) -> bytes:
        return core.read_namespaced_pod_log(pod, ns, container="server", since_seconds=since,
                                            _request_timeout=kt, _preload_content=False).data

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        for k in range(args.ticks):
            clock.wait_boundary(k + 1)
            t0w, t1w = clock.wall(k), clock.wall(k + 1)
            start = time.monotonic()
            row: dict[str, Any] = {"tick": k, "error": ""}
            try:
                pods = [p.metadata.name for p in core.list_namespaced_pod(ns, label_selector="app=frontend",
                                                                          _request_timeout=kt).items]
                bodies = list(pool.map(fetch, pods))
                fetched = time.monotonic()
                lat = [x for b in bodies for x in window_latencies(b.decode("utf-8", errors="replace"), t0w, t1w)]
                row.update({"pods": len(pods), "bytes": sum(len(b) for b in bodies), "server_n": len(lat),
                            "server_p99_ms": p99(lat), "fetch_s": fetched - start})
            except ApiException as exc:
                row["error"] = f"ApiException:{exc.status}"
            except urllib3.exceptions.HTTPError as exc:
                row["error"] = type(exc).__name__
            row["total_s"] = time.monotonic() - start
            try:
                r = requests.get(os.environ["LOCUST_URL"] + "/tick", params={"from": f"{t0w:.6f}", "to": f"{t1w:.6f}"},
                                 timeout=c.telemetry.locust_timeout_s)
                r.raise_for_status()
                loc = r.json()
                row.update({"locust_n": loc["n"], "client_p99_ms": loc["p99_ms"]})
            except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
                row["error"] += f" locust:{type(exc).__name__}"
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            rows.append(row)
            print(f"t{k:02d} total {row['total_s']:.3f}s bytes {row.get('bytes', 0):>8} server_n {row.get('server_n', '-'):>5} "
                  f"locust_n {row.get('locust_n', '-'):>5} server_p99 {row.get('server_p99_ms', float('nan')):6.0f} "
                  f"client_p99 {row.get('client_p99_ms', float('nan')):6.0f} {row['error']}", flush=True)

    ok = [r for r in rows if not r["error"].strip()]
    tot = [r["total_s"] for r in rows]
    s95 = sorted(tot)[max(0, round(0.95 * len(tot)) - 1)]
    comp = [r["server_n"] / r["locust_n"] for r in ok if r.get("locust_n")]
    sp = [r["server_p99_ms"] for r in ok if math.isfinite(r["server_p99_ms"])]
    cp = [r["client_p99_ms"] for r in ok]
    print("\n=== summary ===")
    print(f"ticks {len(rows)}, errors {len(rows) - len(ok)}; fetch+parse total: median {statistics.median(tot):.3f} s, "
          f"p95 {s95:.3f} s, max {max(tot):.3f} s (deadline {deadline} s)")
    print(f"completeness server_n/locust_n: min {min(comp):.3f} median {statistics.median(comp):.3f}")
    print(f"server p99: median {statistics.median(sp):.0f} ms, CV {statistics.pstdev(sp) / statistics.fmean(sp):.3f}; "
          f"client p99: median {statistics.median(cp):.0f} ms, CV {statistics.pstdev(cp) / statistics.fmean(cp):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
