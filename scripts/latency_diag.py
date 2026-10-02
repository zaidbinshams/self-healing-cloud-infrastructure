"""Diagnose tick-P99 variation under steady load (M3, gate G6). Read-only.

On the same 20 s tick windows as the env, collects:
  client     Locust /tick (n, p50, p99) — what the agent sees
  server     frontend `http.resp.took_ms` per request from its JSON logs (A-side latency)
  network    `ping -D -i 0.2 $A_IP` RTT p50/p99/max and loss (B -> A over WSL NAT + hotspot)
  B CPU      Locust process CPU % (one gevent loop = at most one core)
  A CPU      node CPU from metrics-server; CFS throttle ratio of every boutique deployment
  A host     kubelet cAdvisor (unfiltered): CPU of non-Kubernetes processes on A (root minus
             kubepods cgroup), node CPU pressure (PSI "some"), and per-service PSI waiting/stalled
Writes one JSON line per tick and prints correlations at the end.

--stalls mode (sub-second view, no tick windows): for --duration seconds, collects every frontend
request (deduplicated by request id), pings A every 0.2 s, and polls the kubelet's cAdvisor CFS
counters per service. Slow requests (> 500 ms) are grouped into stall events by start time; each
event is checked against (a) ping RTT to A during it — a host-wide freeze delays pings too, a
container-level stall does not — and (b) which services' throttled-period counters advanced in
the cAdvisor samples bracketing it. Writes one JSON document.

Uses the admin kubeconfig (pod logs, node metrics); never mutates anything.
Usage:  source config/cluster.env
        python -m scripts.latency_diag --ticks 30 --out data/diag/latency.jsonl
        python -m scripts.latency_diag --stalls --duration 180 --out data/diag/stalls.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from env.clock import TickClock
from env.contract import load_contract
from env.telemetry import _dep, make_api_client, pod_deployment

PING_INTERVAL_S = 0.2
PING_RE = re.compile(r"^\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+) ms")
LOG_LOOKBACK_PAD_S = 15
PATH_ID_RE = re.compile(r"^/product/[^/]+$")
HEALTH_PATH = "/_healthz"
SLOW_MS = (500, 1000)
STALL_GAP_S = 1.0          # slow requests starting within this gap belong to one stall event
PING_SPIKE_MS = 150.0      # a ping this slow during a stall points at the host, not a container
LOG_POLL_S = 10.0          # frontend log poll period (logs fetched with LOG_LOOKBACK_PAD_S overlap)
CADVISOR_POLL_S = 2.5      # kubelet cAdvisor poll period (its housekeeping interval on A is 5 s)
EVENT_PAD_S = 0.2          # ping window padding around a stall event
_SEL = '{namespace="boutique",container="server"}'


def thr_all(rate_window: str) -> str:
    """Contract Q_thr without the managed-only filter: every boutique deployment."""
    num = _dep(f"rate(container_cpu_cfs_throttled_periods_total{_SEL}[{rate_window}])")
    den = _dep(f"rate(container_cpu_cfs_periods_total{_SEL}[{rate_window}])")
    return f"sum by (deployment) ({num}) / sum by (deployment) ({den})"


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def pearson(a: list[float], b: list[float]) -> float:
    pairs = [(x, y) for x, y in zip(a, b, strict=True) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 3:
        return float("nan")
    xs, ys = zip(*pairs, strict=True)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in pairs) / (len(pairs) * sx * sy)


def cv(xs: list[float]) -> float:
    xs = [x for x in xs if math.isfinite(x)]
    return statistics.pstdev(xs) / statistics.fmean(xs) if len(xs) > 1 and statistics.fmean(xs) else float("nan")


class Pinger:
    """Background `ping -D`; keeps (t_wall, seq, rtt_ms)."""

    def __init__(self, host: str) -> None:
        self.samples: list[tuple[float, int, float]] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(["ping", "-D", "-n", "-i", str(PING_INTERVAL_S), host],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            m = PING_RE.match(line)
            if m:
                with self._lock:
                    self.samples.append((float(m.group(1)), int(m.group(2)), float(m.group(3))))

    def window(self, t0: float, t1: float) -> dict[str, float]:
        with self._lock:
            w = [s for s in self.samples if t0 < s[0] <= t1]
        rtts = [s[2] for s in w]
        expected = (t1 - t0) / PING_INTERVAL_S
        return {"n": len(w), "loss": max(0.0, 1 - len(w) / expected), "p50_ms": pct(rtts, 0.5),
                "p99_ms": pct(rtts, 0.99), "max_ms": max(rtts) if rtts else float("nan")}

    def stop(self) -> None:
        self.proc.terminate()


def proc_cpu_s(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")    # utime + stime


CADVISOR_RE = re.compile(r'^(container_cpu_usage_seconds_total|container_pressure_cpu_waiting_seconds_total|'
                         r'container_pressure_cpu_stalled_seconds_total)\{([^}]*)\} ([0-9.eE+-]+)')
LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
SHORT = {"container_cpu_usage_seconds_total": "cpu", "container_pressure_cpu_waiting_seconds_total": "wait",
         "container_pressure_cpu_stalled_seconds_total": "stall"}


def cadvisor_counters(core: client.CoreV1Api, node: str, ns: str, timeout: tuple[float, float]) -> dict[str, float]:
    """Cumulative CPU/PSI seconds: node root, kubepods, and per boutique deployment (container server)."""
    resp = core.connect_get_node_proxy_with_path(node, "metrics/cadvisor", _request_timeout=timeout,
                                                 _preload_content=False)
    out: dict[str, float] = defaultdict(float)
    for line in resp.data.decode("utf-8", errors="replace").splitlines():
        m = CADVISOR_RE.match(line)
        if not m:
            continue
        labels = dict(LABEL_RE.findall(m.group(2)))
        key, value = SHORT[m.group(1)], float(m.group(3))
        if labels.get("id") == "/":
            out[f"node/{key}"] += value
        elif labels.get("id") == "/kubepods.slice":
            out[f"kubepods/{key}"] += value
        elif labels.get("namespace") == ns and labels.get("container") == "server":
            dep = pod_deployment(labels.get("pod", ""))
            if dep:
                out[f"{dep}/{key}"] += value
    return out


def parse_ts(ts: str) -> float:
    head, frac = ts.rstrip("Z").split(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}").replace(tzinfo=timezone.utc).timestamp()


def server_latencies(core: client.CoreV1Api, ns: str, t0: float, t1: float, timeout: tuple[float, float],
                     tick_s: float) -> dict[str, Any]:
    pods = core.list_namespaced_pod(ns, label_selector="app=frontend", _request_timeout=timeout).items
    by_path: dict[str, list[float]] = defaultdict(list)
    for pod in pods:
        # _preload_content=False: the client otherwise returns repr(bytes) ("b'...'") for log bodies.
        resp = core.read_namespaced_pod_log(pod.metadata.name, ns, container="server",
                                            since_seconds=int(tick_s + LOG_LOOKBACK_PAD_S),
                                            _request_timeout=timeout, _preload_content=False)
        text = resp.data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("message") != "request complete" or rec.get("http.req.path") == HEALTH_PATH:
                continue
            t = parse_ts(rec["timestamp"])
            if t0 < t <= t1:
                path = PATH_ID_RE.sub("/product/[id]", rec["http.req.path"])
                by_path[f"{rec['http.req.method']} {path}"].append(float(rec["http.resp.took_ms"]))
    allv = [v for vs in by_path.values() for v in vs]
    return {"n": len(allv), "p50_ms": pct(allv, 0.5), "p99_ms": pct(allv, 0.99),
            "slow": {str(s): sum(v > s for v in allv) for s in SLOW_MS},
            "paths": {p: {"n": len(v), "p50_ms": pct(v, 0.5), "p99_ms": pct(v, 0.99), "max_ms": max(v),
                          f"over_{SLOW_MS[0]}": sum(x > SLOW_MS[0] for x in v)} for p, v in sorted(by_path.items())}}


THR_RE = re.compile(r'^(container_cpu_cfs_throttled_periods_total|container_cpu_cfs_periods_total)\{([^}]*)\} '
                    r'([0-9.eE+-]+) (\d+)$')


def cfs_samples(core: client.CoreV1Api, node: str, ns: str, timeout: tuple[float, float]) -> dict[str, tuple[float, float, float]]:
    """deployment -> (cAdvisor sample time s, throttled periods, periods) for boutique `server` containers."""
    resp = core.connect_get_node_proxy_with_path(node, "metrics/cadvisor", _request_timeout=timeout,
                                                 _preload_content=False)
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for line in resp.data.decode("utf-8", errors="replace").splitlines():
        m = THR_RE.match(line)
        if not m:
            continue
        labels = dict(LABEL_RE.findall(m.group(2)))
        dep = pod_deployment(labels.get("pod", "")) if labels.get("namespace") == ns and labels.get("container") == "server" else None
        if not dep:
            continue
        a = acc[dep]
        a[0] = max(a[0], int(m.group(4)) / 1000)
        a[1 if m.group(1).endswith("throttled_periods_total") else 2] += float(m.group(3))
    return {d: (a[0], a[1], a[2]) for d, a in acc.items()}


def run_stalls(args: argparse.Namespace) -> int:
    env = os.environ
    c = load_contract()
    ns, kt = c.cluster.namespace, c.telemetry.k8s_timeout_s
    core = client.CoreV1Api(make_api_client(env["KUBE_ADMIN"]))
    node = core.list_node(_request_timeout=kt).items[0].metadata.name
    pod = core.list_namespaced_pod(ns, label_selector="app=frontend", _request_timeout=kt).items[0].metadata.name
    pinger = Pinger(env["A_IP"])
    reqs: dict[str, tuple[float, str, float]] = {}
    cfs: list[tuple[float, dict[str, tuple[float, float, float]]]] = []
    errors: list[str] = []
    end = time.monotonic() + args.duration
    next_log = time.monotonic()
    while time.monotonic() < end:
        try:
            cfs.append((time.time(), cfs_samples(core, node, ns, kt)))
            if time.monotonic() >= next_log:
                next_log += LOG_POLL_S
                text = core.read_namespaced_pod_log(pod, ns, container="server", since_seconds=int(LOG_POLL_S + LOG_LOOKBACK_PAD_S),
                                                    _request_timeout=kt, _preload_content=False).data.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("message") == "request complete" and r.get("http.req.path") != HEALTH_PATH:
                        path = PATH_ID_RE.sub("/product/[id]", r["http.req.path"])
                        reqs[r["http.req.id"]] = (parse_ts(r["timestamp"]), f"{r['http.req.method']} {path}",
                                                  float(r["http.resp.took_ms"]))
        except (ApiException, urllib3.exceptions.HTTPError, KeyError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
        time.sleep(CADVISOR_POLL_S)
    pinger.stop()

    done = sorted(reqs.values())
    slow = sorted((t - ms / 1000, t, ms, path) for t, path, ms in done if ms > SLOW_MS[0])
    events: list[dict[str, Any]] = []
    for start, finish, ms, path in slow:
        if events and start - events[-1]["start"] < STALL_GAP_S:
            ev = events[-1]
            ev["end"] = max(ev["end"], finish)
            ev["n"] += 1
            ev["paths"][path] = ev["paths"].get(path, 0) + 1
        else:
            events.append({"start": start, "end": finish, "n": 1, "paths": {path: 1}})
    pings = sorted(pinger.samples)
    deps = sorted({d for _, snap in cfs for d in snap})

    def throttle_between(t0: float, t1: float) -> dict[str, float]:
        """Throttled share of CFS periods per service between the samples bracketing [t0, t1]."""
        out = {}
        for d in deps:
            series = sorted({snap[d] for _, snap in cfs if d in snap})          # (sample_t, thr, periods)
            before = [x for x in series if x[0] <= t0]
            after = [x for x in series if x[0] >= t1]
            if before and after and after[0][2] > before[-1][2]:
                out[d] = (after[0][1] - before[-1][1]) / (after[0][2] - before[-1][2])
        return out

    lo = pings[0][0] if pings else float("inf")
    hi = pings[-1][0] if pings else float("-inf")
    for ev in events:
        w = [p[2] for p in pings if ev["start"] - EVENT_PAD_S <= p[0] <= ev["end"] + EVENT_PAD_S]
        ev["ping_n"], ev["ping_max_ms"] = len(w), (max(w) if w else None)
        ev["throttle"] = throttle_between(ev["start"], ev["end"])
    covered = [ev for ev in events if lo < ev["start"] and ev["end"] < hi]
    quiet = [p[2] for p in pings
             if not any(e["start"] - EVENT_PAD_S <= p[0] <= e["end"] + EVENT_PAD_S for e in events)]
    span = (done[-1][0] - done[0][0]) if len(done) > 1 else float("nan")
    whole = {}
    for d in deps:            # first vs last cAdvisor sample (sample times lag B's poll times)
        series = sorted({snap[d] for _, snap in cfs if d in snap})
        if len(series) > 1 and series[-1][2] > series[0][2]:
            whole[d] = (series[-1][1] - series[0][1]) / (series[-1][2] - series[0][2])
    summary = {
        "requests": len(done), "slow": len(slow), "slow_share": len(slow) / len(done) if done else None,
        "span_s": span, "events": len(events), "events_per_min": len(events) / span * 60 if math.isfinite(span) and span > 0 else None,
        "event_duration_s_median": statistics.median([e["end"] - e["start"] for e in events]) if events else None,
        "events_with_ping_spike": sum(1 for e in covered if (e["ping_max_ms"] or 0) > PING_SPIKE_MS),
        "events_with_ping_coverage": len(covered),
        "quiet_ping_spike_share": sum(x > PING_SPIKE_MS for x in quiet) / len(quiet) if quiet else None,
        "throttle_whole_run": whole,
        "throttle_in_stalls_mean": {d: statistics.fmean(e["throttle"][d] for e in events if d in e["throttle"])
                                    for d in deps if any(d in e["throttle"] for e in events)},
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "events": events}, indent=1, default=str))

    print(f"{summary['requests']} requests over {span:.0f} s; {summary['slow']} > {SLOW_MS[0]} ms "
          f"({(summary['slow_share'] or 0):.2%}); {len(events)} stall events "
          f"({(summary['events_per_min'] or 0):.1f}/min, median {summary['event_duration_s_median'] or 0:.2f} s)")
    for ev in events:
        top = sorted(ev["throttle"].items(), key=lambda kv: -kv[1])[:3]
        print(f"  stall +{ev['start'] - done[0][0]:6.1f}s dur {ev['end'] - ev['start']:4.2f}s reqs {ev['n']:3d} "
              f"ping max {ev['ping_max_ms'] if ev['ping_max_ms'] is not None else float('nan'):6.1f} ms | "
              + " ".join(f"{d} {v:.2f}" for d, v in top))
    print(f"host-freeze test: {summary['events_with_ping_spike']}/{summary['events_with_ping_coverage']} stalls had a ping > "
          f"{PING_SPIKE_MS:.0f} ms (outside stalls: {(summary['quiet_ping_spike_share'] or 0):.1%} of pings)")
    print("throttled share of CFS periods — whole run vs bracketing stalls (mean):")
    for d in sorted(whole, key=lambda d: -summary["throttle_in_stalls_mean"].get(d, 0)):
        print(f"  {d:24s} {whole[d]:.3f}  vs  {summary['throttle_in_stalls_mean'].get(d, float('nan')):.3f}")
    print(f"errors: {len(errors)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticks", type=int, default=30)
    ap.add_argument("--stalls", action="store_true", help="sub-second stall-event mode (see module docstring)")
    ap.add_argument("--duration", type=float, default=180.0, help="--stalls: seconds to observe")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.stalls:
        return run_stalls(args)
    env = os.environ
    c = load_contract()
    ns, tick_s = c.cluster.namespace, c.clock.tick_s
    kt, pt, lt = c.telemetry.k8s_timeout_s, c.telemetry.prom_timeout_s, c.telemetry.locust_timeout_s
    api = make_api_client(env["KUBE_ADMIN"])
    core, custom = client.CoreV1Api(api), client.CustomObjectsApi(api)
    locust_pid = int((Path(__file__).resolve().parent.parent / "data" / "locust.pid").read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)

    pinger = Pinger(env["A_IP"])
    clock = TickClock(tick_s, c.clock.late_frac)
    clock.anchor()
    cpu_prev = proc_cpu_s(locust_pid)
    node = core.list_node(_request_timeout=kt).items[0].metadata.name
    cad_prev = cadvisor_counters(core, node, ns, kt)
    rows: list[dict[str, Any]] = []
    with args.out.open("w") as fh:
        for k in range(args.ticks):
            clock.wait_boundary(k + 1)
            t0, t1 = clock.wall(k), clock.wall(k + 1)
            row: dict[str, Any] = {"tick": k, "t0": t0, "t1": t1, "errors": []}
            cpu_now = proc_cpu_s(locust_pid)
            row["locust_cpu_frac"] = (cpu_now - cpu_prev) / (t1 - t0)
            cpu_prev = cpu_now
            try:
                r = requests.get(env["LOCUST_URL"] + "/tick", params={"from": f"{t0:.6f}", "to": f"{t1:.6f}"}, timeout=lt)
                r.raise_for_status()
                row["client"] = r.json()
            except (requests.exceptions.RequestException, ValueError) as exc:
                row["errors"].append(f"locust:{type(exc).__name__}")
            row["net"] = pinger.window(t0, t1)
            try:
                cad = cadvisor_counters(core, node, ns, kt)
                rate = {k: (cad[k] - cad_prev.get(k, cad[k])) / (t1 - t0) for k in cad}
                cad_prev = cad
                row["host"] = {"node_cpu": rate.get("node/cpu", float("nan")),
                               "kube_cpu": rate.get("kubepods/cpu", float("nan")),
                               "other_cpu": rate.get("node/cpu", float("nan")) - rate.get("kubepods/cpu", float("nan")),
                               "node_psi_wait": rate.get("node/wait", float("nan")),
                               "psi_wait": {k.split("/")[0]: v for k, v in rate.items()
                                            if k.endswith("/wait") and not k.startswith(("node/", "kubepods/"))},
                               "psi_stall": {k.split("/")[0]: v for k, v in rate.items()
                                             if k.endswith("/stall") and not k.startswith(("node/", "kubepods/"))}}
            except (ApiException, urllib3.exceptions.HTTPError, ValueError) as exc:
                row["errors"].append(f"cadvisor:{type(exc).__name__}")
            try:
                row["server"] = server_latencies(core, ns, t0, t1, kt, tick_s)
                nodes = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes", _request_timeout=kt)
                row["node_cpu_cores"] = sum(float(parse_quantity(i["usage"]["cpu"])) for i in nodes["items"])
            except (ApiException, urllib3.exceptions.HTTPError, KeyError, ValueError) as exc:
                row["errors"].append(f"kube:{type(exc).__name__}")
            try:
                r = requests.get(env["PROM_URL"] + "/api/v1/query", params={"query": thr_all(c.telemetry.rate_window), "time": f"{t1:.3f}"},
                                 timeout=pt)
                r.raise_for_status()
                row["throttle"] = {x["metric"].get("deployment", "?"): float(x["value"][1])
                                   for x in r.json()["data"]["result"]}
            except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
                row["errors"].append(f"prom:{type(exc).__name__}")
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            rows.append(row)
            cl, sv, nt = row.get("client", {}), row.get("server", {}), row["net"]
            print(f"t{k:02d} client p99 {cl.get('p99_ms', float('nan')):6.0f} n {cl.get('n', 0):5d} | "
                  f"server p99 {sv.get('p99_ms', float('nan')):6.0f} >500ms {sv.get('slow', {}).get('500', '-')!s:>3} | "
                  f"rtt p50 {nt['p50_ms']:5.1f} max {nt['max_ms']:6.1f} loss {nt['loss']:.2f} | "
                  f"locust cpu {row['locust_cpu_frac']:.2f} | A other cpu {row.get('host', {}).get('other_cpu', float('nan')):5.2f} "
                  f"node psi {row.get('host', {}).get('node_psi_wait', float('nan')):.2f} "
                  f"{' '.join(row['errors'])}", flush=True)
    pinger.stop()

    def col(f):
        return [f(r) for r in rows]
    client_p99 = col(lambda r: r.get("client", {}).get("p99_ms", float("nan")))
    server_p99 = col(lambda r: r.get("server", {}).get("p99_ms", float("nan")))
    print("\n=== summary over", len(rows), "ticks ===")
    print(f"client p99: median {statistics.median(client_p99):.0f}  CV {cv(client_p99):.2f}")
    print(f"server p99: median {statistics.median(server_p99):.0f}  CV {cv(server_p99):.2f}")
    for name, xs in [("server p99", server_p99), ("rtt max", col(lambda r: r["net"]["max_ms"])),
                     ("rtt p99", col(lambda r: r["net"]["p99_ms"])), ("locust cpu", col(lambda r: r["locust_cpu_frac"])),
                     ("node cpu", col(lambda r: r.get("node_cpu_cores", float("nan")))),
                     ("server >500ms count", col(lambda r: float(r.get("server", {}).get("slow", {}).get("500", "nan")))),
                     ("A non-k8s cpu", col(lambda r: r.get("host", {}).get("other_cpu", float("nan")))),
                     ("A node cpu (cAdvisor)", col(lambda r: r.get("host", {}).get("node_cpu", float("nan")))),
                     ("A node psi wait", col(lambda r: r.get("host", {}).get("node_psi_wait", float("nan"))))]:
        print(f"corr(client p99, {name:20s}) = {pearson(client_p99, xs):+.2f}")
    paths: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    slow: dict[str, int] = defaultdict(int)
    for r in rows:
        for p, s in r.get("server", {}).get("paths", {}).items():
            paths[p].append(s["p99_ms"])
            counts[p] += s["n"]
            slow[p] += s[f"over_{SLOW_MS[0]}"]
    total = sum(counts.values()) or 1
    print("\nserver latency by path (share of requests; median of per-tick p99; requests > 500 ms):")
    for p in sorted(paths, key=lambda p: -statistics.median(paths[p])):
        print(f"  {p:28s} {counts[p] / total:6.1%}  p99~{statistics.median(paths[p]):6.0f} ms  slow {slow[p]}")
    thr: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for d, v in r.get("throttle", {}).items():
            if math.isfinite(v):
                thr[d].append(v)
    deps = sorted({d for r in rows for d in r.get("host", {}).get("psi_wait", {})})
    print("\nper-service CPU pressure (PSI waiting, fraction of time): median / max / corr with client p99:")
    for d in deps:
        w = col(lambda r, d=d: r.get("host", {}).get("psi_wait", {}).get(d, float("nan")))
        fw = [x for x in w if math.isfinite(x)]
        if fw:
            print(f"  {d:24s} {statistics.median(fw):.3f} / {max(fw):.3f} / {pearson(client_p99, w):+.2f}")
    print("\nthrottle (median / max) for all deployments:")
    for d in sorted(thr, key=lambda d: -max(thr[d])):
        print(f"  {d:24s} {statistics.median(thr[d]):.3f} / {max(thr[d]):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
