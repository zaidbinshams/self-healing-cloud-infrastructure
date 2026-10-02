"""Probe Online Boutique gRPC services directly from Machine B during load (M3, gate G6). Read-only.

Opens `kubectl port-forward` (admin kubeconfig) to each target service and calls one method every
--period seconds on an absolute schedule, recording (B wall start, latency, status) per call.
Requests are sent as raw protobuf bytes, so no generated stubs are needed: Empty and
HealthCheckRequest{service: ""} serialize to b"", and GetCartRequest{user_id: "g6-probe"} is
field 1 (string) = b"\n\x08g6-probe" — a read of a dedicated, always-empty probe cart.

Targets (G6 stall attribution, 2026-10-02):
  currency      currencyservice  GetSupportedCurrencies   — suspect handler (Node.js)
  currency_hc   currencyservice  grpc.health.v1 Check      — same event loop, near-zero work
  shipping_hc   shippingservice  grpc.health.v1 Check      — control: same port-forward path, Go,
                                                              not on the page-render path
  cart          cartservice      GetCart                   — the one dependency every page render
                                                              calls but POST /cart does not (.NET+redis)
  redis         redis-cart       PING (raw RESP)           — cartservice's backing store, bypassing .NET

It also polls the kubelet's cAdvisor `container_threads` for cartservice every second (keeping
cAdvisor's sample timestamps). With --stalls-json, thread-count rises are tested against the
stall windows: the share of rising sample intervals that overlap a stall, divided by the share
of all sample intervals that do (enrichment > 1 means rises cluster at stalls, as a starved .NET
ThreadPool injecting workers would produce).

With --stalls-json (output of `latency_diag --stalls` run over the same period) it reports, per
target, latency inside vs outside the frontend stall events and how many stalls had a probe spike.

Usage:  source config/cluster.env
        python -m scripts.latency_diag --stalls --duration 300 --out data/diag/stalls.json &
        python -m scripts.grpc_probe --duration 300 --out data/diag/grpc.json --stalls-json data/diag/stalls.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.telemetry import make_api_client, pod_deployment

PERIOD_S = 0.05
CALL_DEADLINE_S = 2.0
SPIKE_MS = 200.0               # a probe this slow counts as a spike
EVENT_PAD_S = 0.2
PORT_FORWARD_READY_S = 15.0
THREAD_POLL_S = 1.0            # cAdvisor refreshes every housekeeping interval (5 s on A); poll faster
THREAD_DEPLOYMENT = "cartservice"
THREAD_PAD_S = 5.0             # a rise may be sampled up to one housekeeping interval after it happened
REDIS_PING = b"*1\r\n$4\r\nPING\r\n"
REDIS_PONG = b"+PONG\r\n"
THREADS_RE = re.compile(r'^container_threads\{([^}]*)\} ([0-9.eE+-]+) (\d+)$')
LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class Target:
    name: str
    service: str
    port: int
    local_port: int
    method: str
    request: bytes = b""
    kind: str = "grpc"             # "grpc" or "redis"


PROBE_CART = b"\n\x08g6-probe"      # GetCartRequest{user_id: "g6-probe"}

TARGETS = (
    Target("currency", "currencyservice", 7000, 17000, "/hipstershop.CurrencyService/GetSupportedCurrencies"),
    Target("currency_hc", "currencyservice", 7000, 17000, "/grpc.health.v1.Health/Check"),
    Target("shipping_hc", "shippingservice", 50051, 15051, "/grpc.health.v1.Health/Check"),
    Target("cart", "cartservice", 7070, 17070, "/hipstershop.CartService/GetCart", PROBE_CART),
    Target("redis", "redis-cart", 6379, 16379, "PING", kind="redis"),
)


def port_forward(kubeconfig: str, ns: str, service: str, port: int, local_port: int) -> subprocess.Popen[str]:
    proc = subprocess.Popen(["kubectl", "--kubeconfig", kubeconfig, "-n", ns, "port-forward", f"svc/{service}",
                             f"{local_port}:{port}", "--address", "127.0.0.1"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + PORT_FORWARD_READY_S
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if "Forwarding from" in line:
            threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()   # drain
            return proc
        if proc.poll() is not None:
            break
    proc.terminate()
    raise RuntimeError(f"port-forward to {service}:{port} did not become ready")


class RedisPing:
    """PING over one persistent RESP connection; reconnects after an error."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.sock: socket.socket | None = None

    def __call__(self, _request: bytes, timeout: float) -> None:
        if self.sock is None:
            self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.sock.sendall(REDIS_PING)
        buf = b""
        while not buf.endswith(b"\r\n"):
            chunk = self.sock.recv(64)
            if not chunk:
                raise ConnectionError("redis closed the connection")
            buf += chunk
        if buf != REDIS_PONG:
            raise ValueError(f"unexpected reply {buf!r}")

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None


def probe(target: Target, duration_s: float, out: list[tuple[float, float, str]]) -> None:
    if target.kind == "redis":
        redis = RedisPing(target.local_port)
        call, closer = redis, redis.close
    else:
        channel = grpc.insecure_channel(f"127.0.0.1:{target.local_port}")
        call = channel.unary_unary(target.method, request_serializer=None, response_deserializer=None)
        closer = channel.close
    t0 = time.monotonic()
    k = 0
    while True:
        next_t = t0 + k * PERIOD_S
        if next_t - t0 > duration_s:
            break
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        start_wall, start = time.time(), time.monotonic()
        try:
            call(target.request, timeout=CALL_DEADLINE_S)
            status = "OK"
        except grpc.RpcError as exc:
            status = exc.code().name
        except (OSError, ValueError) as exc:          # redis: socket errors / bad reply
            status = type(exc).__name__
            if isinstance(call, RedisPing):
                call.close()
        out.append((start_wall, (time.monotonic() - start) * 1000, status))
        k += 1
    closer()


def monitor_threads(kubeconfig: str, ns: str, duration_s: float, out: list[tuple[float, float]],
                    errors: list[str]) -> None:
    """(cAdvisor sample time s, thread count) for THREAD_DEPLOYMENT's `server` container."""
    core = client.CoreV1Api(make_api_client(kubeconfig))
    timeout = (2.0, 5.0)
    node = core.list_node(_request_timeout=timeout).items[0].metadata.name
    seen: set[float] = set()
    end = time.monotonic() + duration_s
    while time.monotonic() < end:
        try:
            body = core.connect_get_node_proxy_with_path(node, "metrics/cadvisor", _request_timeout=timeout,
                                                         _preload_content=False).data.decode("utf-8", errors="replace")
            for line in body.splitlines():
                m = THREADS_RE.match(line)
                if not m:
                    continue
                labels = dict(LABEL_RE.findall(m.group(1)))
                if (labels.get("namespace") == ns and labels.get("container") == "server"
                        and pod_deployment(labels.get("pod", "")) == THREAD_DEPLOYMENT):
                    ts = int(m.group(3)) / 1000
                    if ts not in seen:
                        seen.add(ts)
                        out.append((ts, float(m.group(2))))
        except (ApiException, urllib3.exceptions.HTTPError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
        time.sleep(THREAD_POLL_S)
    out.sort()


def analyse_threads(samples: list[tuple[float, float]], events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [c for _, c in samples]
    r: dict[str, Any] = {"samples": len(samples), "min": min(counts, default=None),
                         "median": statistics.median(counts) if counts else None, "max": max(counts, default=None)}
    if len(samples) < 2:
        return r
    intervals = [(samples[i - 1][0], samples[i][0], samples[i][1] - samples[i - 1][1]) for i in range(1, len(samples))]

    def overlaps(a: float, b: float) -> bool:      # sample interval (a, b] vs stall [start, end + pad]
        return any(a < e["end"] + THREAD_PAD_S and b >= e["start"] for e in events)
    rises = [iv for iv in intervals if iv[2] > 0]
    r.update({"rises": len(rises), "falls": sum(1 for iv in intervals if iv[2] < 0), "intervals": len(intervals),
              "median_interval_s": statistics.median(b - a for a, b, _ in intervals)})
    if events:
        base = sum(overlaps(a, b) for a, b, _ in intervals) / len(intervals)
        hit = sum(overlaps(a, b) for a, b, _ in rises) / len(rises) if rises else float("nan")
        lo, hi = samples[0][0], samples[-1][0]
        covered = [e for e in events if lo < e["start"] and e["end"] < hi]
        r.update({"rise_share_in_stalls": hit, "all_share_in_stalls": base,
                  "enrichment": hit / base if base else float("nan"),
                  "stalls_with_rise": sum(1 for e in covered
                                          if any(a < e["end"] + THREAD_PAD_S and b >= e["start"] for a, b, _ in rises)),
                  "stalls_covered": len(covered)})
    return r


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def analyse(samples: dict[str, list[tuple[float, float, str]]], stalls: dict[str, Any] | None) -> dict[str, Any]:
    report: dict[str, Any] = {}
    events = (stalls or {}).get("events", [])
    for name, rows in samples.items():
        lat = [r[1] for r in rows]
        r: dict[str, Any] = {"n": len(rows), "errors": sum(1 for x in rows if x[2] != "OK"),
                             "p50_ms": pct(lat, 0.5), "p99_ms": pct(lat, 0.99), "max_ms": max(lat, default=float("nan")),
                             "spike_share": sum(x > SPIKE_MS for x in lat) / len(lat) if lat else float("nan")}
        if events:
            def in_event(t: float) -> bool:
                return any(e["start"] - EVENT_PAD_S <= t <= e["end"] + EVENT_PAD_S for e in events)
            inside = [x[1] for x in rows if in_event(x[0])]
            outside = [x[1] for x in rows if not in_event(x[0])]
            lo, hi = (rows[0][0], rows[-1][0]) if rows else (0.0, 0.0)
            covered = [e for e in events if lo < e["start"] and e["end"] < hi]
            hit = [e for e in covered if any(e["start"] - EVENT_PAD_S <= x[0] <= e["end"] + EVENT_PAD_S and x[1] > SPIKE_MS
                                             for x in rows)]
            spikes = [x for x in rows if x[1] > SPIKE_MS]
            r.update({"inside_p50_ms": pct(inside, 0.5), "inside_max_ms": max(inside, default=float("nan")),
                      "outside_p50_ms": pct(outside, 0.5), "outside_p99_ms": pct(outside, 0.99),
                      "stalls_covered": len(covered), "stalls_with_spike": len(hit),
                      "spikes_inside_stalls": sum(1 for x in spikes if in_event(x[0])), "spikes_total": len(spikes)})
        report[name] = r
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stalls-json", type=Path, help="latency_diag --stalls output from the same period")
    args = ap.parse_args(argv)
    kubeconfig, ns = os.environ["KUBE_ADMIN"], os.environ.get("NS", "boutique")

    forwards = {}
    try:
        for t in TARGETS:
            if t.local_port not in forwards:
                forwards[t.local_port] = port_forward(kubeconfig, ns, t.service, t.port, t.local_port)
        samples: dict[str, list[tuple[float, float, str]]] = {t.name: [] for t in TARGETS}
        threads = [threading.Thread(target=probe, args=(t, args.duration, samples[t.name])) for t in TARGETS]
        thread_samples: list[tuple[float, float]] = []
        monitor_errors: list[str] = []
        threads.append(threading.Thread(target=monitor_threads,
                                        args=(kubeconfig, ns, args.duration, thread_samples, monitor_errors)))
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        for proc in forwards.values():
            proc.terminate()

    stalls = None
    if args.stalls_json:
        deadline = time.monotonic() + 120            # the concurrent --stalls run may still be finishing
        while not args.stalls_json.exists() and time.monotonic() < deadline:
            time.sleep(2)
        stalls = json.loads(args.stalls_json.read_text()) if args.stalls_json.exists() else None
    report = analyse(samples, stalls)
    threads_report = analyse_threads(thread_samples, (stalls or {}).get("events", []))
    threads_report["errors"] = monitor_errors
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"report": report, "threads": threads_report, "samples": samples,
                                    "thread_samples": thread_samples}, default=str))

    print(f"{'target':12s} {'n':>5s} {'err':>4s} {'p50':>6s} {'p99':>6s} {'max':>7s} {'>200ms':>7s}"
          + ("  | in-stall p50/max   out p50/p99   stalls w/ spike   spikes in stalls" if stalls else ""))
    for name, r in report.items():
        line = (f"{name:12s} {r['n']:5d} {r['errors']:4d} {r['p50_ms']:6.1f} {r['p99_ms']:6.1f} {r['max_ms']:7.1f} "
                f"{r['spike_share']:7.2%}")
        if stalls:
            line += (f"  | {r['inside_p50_ms']:6.1f}/{r['inside_max_ms']:7.1f}  {r['outside_p50_ms']:5.1f}/{r['outside_p99_ms']:6.1f}"
                     f"   {r['stalls_with_spike']:3d}/{r['stalls_covered']:<3d}          {r['spikes_inside_stalls']}/{r['spikes_total']}")
        print(line)
    tr = threads_report
    print(f"\n{THREAD_DEPLOYMENT} threads: min {tr['min']} median {tr['median']} max {tr['max']} "
          f"({tr['samples']} cAdvisor samples, median interval {tr.get('median_interval_s', float('nan')):.1f} s); "
          f"rises {tr.get('rises')} falls {tr.get('falls')}; monitor errors {len(monitor_errors)}")
    if stalls and "enrichment" in tr:
        print(f"thread rises overlapping a stall: {tr['rise_share_in_stalls']:.0%} vs all intervals {tr['all_share_in_stalls']:.0%} "
              f"-> enrichment {tr['enrichment']:.2f}; stalls with a rise {tr['stalls_with_rise']}/{tr['stalls_covered']}")
    if stalls:
        s = stalls["summary"]
        print(f"frontend stalls in window: {s['events']} ({s['events_per_min']:.1f}/min), slow share {s['slow_share']:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
