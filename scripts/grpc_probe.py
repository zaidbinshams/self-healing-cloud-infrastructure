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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc

PERIOD_S = 0.05
CALL_DEADLINE_S = 2.0
SPIKE_MS = 200.0               # a probe this slow counts as a spike
EVENT_PAD_S = 0.2
PORT_FORWARD_READY_S = 15.0


@dataclass(frozen=True)
class Target:
    name: str
    service: str
    port: int
    local_port: int
    method: str
    request: bytes = b""


PROBE_CART = b"\n\x08g6-probe"      # GetCartRequest{user_id: "g6-probe"}

TARGETS = (
    Target("currency", "currencyservice", 7000, 17000, "/hipstershop.CurrencyService/GetSupportedCurrencies"),
    Target("currency_hc", "currencyservice", 7000, 17000, "/grpc.health.v1.Health/Check"),
    Target("shipping_hc", "shippingservice", 50051, 15051, "/grpc.health.v1.Health/Check"),
    Target("cart", "cartservice", 7070, 17070, "/hipstershop.CartService/GetCart", PROBE_CART),
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


def probe(target: Target, duration_s: float, out: list[tuple[float, float, str]]) -> None:
    channel = grpc.insecure_channel(f"127.0.0.1:{target.local_port}")
    call = channel.unary_unary(target.method, request_serializer=None, response_deserializer=None)
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
        out.append((start_wall, (time.monotonic() - start) * 1000, status))
        k += 1
    channel.close()


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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"report": report, "samples": samples}, default=str))

    print(f"{'target':12s} {'n':>5s} {'err':>4s} {'p50':>6s} {'p99':>6s} {'max':>7s} {'>200ms':>7s}"
          + ("  | in-stall p50/max   out p50/p99   stalls w/ spike   spikes in stalls" if stalls else ""))
    for name, r in report.items():
        line = (f"{name:12s} {r['n']:5d} {r['errors']:4d} {r['p50_ms']:6.1f} {r['p99_ms']:6.1f} {r['max_ms']:7.1f} "
                f"{r['spike_share']:7.2%}")
        if stalls:
            line += (f"  | {r['inside_p50_ms']:6.1f}/{r['inside_max_ms']:7.1f}  {r['outside_p50_ms']:5.1f}/{r['outside_p99_ms']:6.1f}"
                     f"   {r['stalls_with_spike']:3d}/{r['stalls_covered']:<3d}          {r['spikes_inside_stalls']}/{r['spikes_total']}")
        print(line)
    if stalls:
        s = stalls["summary"]
        print(f"frontend stalls in window: {s['events']} ({s['events_per_min']:.1f}/min), slow share {s['slow_share']:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
