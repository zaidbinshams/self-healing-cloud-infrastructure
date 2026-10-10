"""Capture raw per-request frontend latencies for Paper B's estimator analysis. Read-only on the cluster.

Polls every frontend pod's request log, de-duplicates by `http.req.id`, and writes one JSON line per
completed non-health request: {"t": <A log timestamp, unix s>, "path": ..., "ms": took_ms, "status": ...}.
Run it only in a dedicated window (no fault episodes, no other heavy log readers) at U_base, so the
data is a clean steady state. Locust must be running; this script never changes the load.

Usage:  source config/cluster.env
        python -m scripts.capture_server_requests --minutes 15 --out data/paperB/requests_u33.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.contract import load_contract
from env.telemetry import (
    SERVER_CONTAINER,
    SERVER_DEPLOYMENT,
    SERVER_HEALTH_PATH,
    _log_ts,
    make_api_client,
)

POLL_S = 10.0                 # poll period; each read overlaps the previous one by LOOKBACK_PAD_S
LOOKBACK_PAD_S = 15


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    c = load_contract()
    ns, timeout = c.cluster.namespace, c.telemetry.k8s_timeout_s
    core = client.CoreV1Api(make_api_client(os.environ["KUBE_AGENT"]))
    seen: set[str] = set()
    errors = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + args.minutes * 60
    with args.out.open("w", encoding="utf-8") as fh:
        while time.monotonic() < end:
            t0 = time.monotonic()
            try:
                pods = [p.metadata.name for p in core.list_namespaced_pod(
                    ns, label_selector=f"app={SERVER_DEPLOYMENT}", _request_timeout=timeout).items]
                for pod in pods:
                    text = core.read_namespaced_pod_log(
                        pod, ns, container=SERVER_CONTAINER, since_seconds=int(POLL_S + LOOKBACK_PAD_S),
                        _request_timeout=timeout, _preload_content=False).data.decode("utf-8", errors="replace")
                    for line in text.splitlines():
                        if '"request complete"' not in line:
                            continue
                        try:
                            rec = json.loads(line)
                            rid = rec["http.req.id"]
                            if rid in seen or rec.get("http.req.path") == SERVER_HEALTH_PATH:
                                continue
                            seen.add(rid)
                            fh.write(json.dumps({"t": _log_ts(rec["timestamp"]), "path": rec["http.req.path"],
                                                 "ms": float(rec["http.resp.took_ms"]),
                                                 "status": rec.get("http.resp.status")}) + "\n")
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue        # partial line at a read boundary
                fh.flush()
            except ApiException as exc:
                errors += 1
                print(f"capture: ApiException {exc.status} (#{errors})", file=sys.stderr)
            except urllib3.exceptions.HTTPError as exc:
                errors += 1
                print(f"capture: {type(exc).__name__} (#{errors})", file=sys.stderr)
            time.sleep(max(0.0, POLL_S - (time.monotonic() - t0)))
    print(f"captured {len(seen)} requests to {args.out} ({errors} poll errors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
