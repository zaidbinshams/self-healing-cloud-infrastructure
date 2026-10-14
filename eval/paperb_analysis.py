"""Paper B analyses: where and how to measure the SLA signal of a self-healing loop. Offline, read-only.

Every number comes from data recorded on the real testbed (data/); nothing is simulated.

  A1 decomposition   per-tick client p99, server p99 and B->A ping p99 (scripts/latency_diag.py runs):
                     CV of each signal and its correlation with network RTT, before/after the runtime fix
  A2 runtime stalls  sub-second stall events before/after cartservice Treatment A (latency_diag --stalls)
  A3 estimator noise per-window bootstrap of nearest-rank quantiles on the raw request capture
                     (scripts/capture_server_requests.py): sampling CV vs expected tail count k = n(1-q),
                     fit CV ~ a*k^-1/2, and the k needed for a target CV (the tail-sample rule)
  A4 false alarms    on fault-free calibration ticks, each signal calibrated by the contract rule
                     L = ceil_10ms(1.5*q95) on the first half and evaluated on the second half: breach rate
                     and consecutive-breach pairs (the runbook's not-H(2) trigger) per hour
  A5 detectability   fault smokes at the final cluster state: peak signal / threshold before the remedy
  A6 transport       Prometheus ConnectTimeouts per tick before/after the keep-alive session
  A7 runbook view    on the M3 runbook validation (ε = 0) and the warm-start runs: per signal (server p95 vs
                     L_SLA; client p99 vs its own threshold from the same calibration rule) the share of
                     fault-free ticks over threshold and of the runbook's not-H(2) trigger (two consecutive),
                     and detection within each fault's first two ticks (before the runbook could act)

Usage:  python -m eval.paperb_analysis [--out data/paperB/analysis.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from env.contract import REPO_ROOT, load_contract
from env.telemetry import _nearest_rank

DATA = REPO_ROOT / "data"
SEED = 2026
BOOTSTRAPS = 400
L_SLA_FACTOR = 1.5                      # contract sla.l_sla_factor (the calibration rule under test)
L_SLA_STEP_MS = 10.0
TICK_S = 20.0

# A1: latency_diag tick runs, in chronological order of the investigation
DIAG_RUNS = [
    ("U50 baseline", "diag/latency-baseline.jsonl"),
    ("U50 repeat (+host metrics)", "diag/latency-host.jsonl"),
    ("U50, recommendation 500m", "diag/latency-rec500.jsonl"),
    ("U40", "diag/latency-u40.jsonl"),
    ("U40, runtime fix", "diag/latency-treatA-u40.jsonl"),
    ("U40, runtime fix + Wi-Fi tuned", "diag/latency-linktuned-u40.jsonl"),
]
STALL_RUNS = [("before Treatment A (U40)", "diag/stalls-u40.json"), ("after Treatment A", "diag/stalls-treatA.json")]
RAW_CAPTURE = "paperB/requests_u30.jsonl"
WINDOWS_S = (5.0, 10.0, 20.0, 40.0, 60.0)
QUANTILES = (0.5, 0.9, 0.95, 0.99)
TARGET_CVS = (0.10, 0.15)
# A4: calibration runs that logged both the server SLA latency and the client p99 per tick
DUAL_SIGNAL_RUNS = [
    ("keep-alive load, U33 (2026-10-09)", "calibration/20261009T153959.jsonl"),
    ("conn-per-request, final limits (2026-10-10 14:26)", "calibration/20261010T142606.jsonl"),
    ("conn-per-request, final (2026-10-10 15:44)", "calibration/20261010T154413.jsonl"),
]
# A5: smokes at the final limits (after a675b86); thresholds from the final calibration run
FINAL_SMOKES = ["F4-20261010T145928", "F4-20261010T150330", "F2-20261010T150758", "F2-20261010T151240"]
FINAL_CALIBRATION = "calibration/20261010T154413.jsonl"
# A6: calibrate runs before / after the Prometheus keep-alive session (cdc39a4)
TRANSPORT_RUNS = [("per-query connections", ["20261010T122211", "20261010T131705", "20261010T142606"]),
                  ("keep-alive session", ["20261010T154413"])]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cv(xs: Sequence[float]) -> float:
    a = np.asarray(xs, float)
    return float(a.std(ddof=1) / a.mean()) if len(a) > 1 and a.mean() else float("nan")


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    return float(np.corrcoef(np.asarray(x, float), np.asarray(y, float))[0, 1]) if len(x) > 2 else float("nan")


def ceil_step(x: float, step: float = L_SLA_STEP_MS) -> float:
    return math.ceil(x / step) * step


# ----------------------------------------------------------------------------- A1, A2

def a1_decomposition() -> list[dict[str, Any]]:
    out = []
    for label, rel in DIAG_RUNS:
        p = DATA / rel
        if not p.exists():
            continue
        rows = [r for r in read_jsonl(p) if not r.get("errors")]
        cli = [r["client"]["p99_ms"] for r in rows]
        srv = [r["server"]["p99_ms"] for r in rows]
        net = [r["net"]["p99_ms"] for r in rows]
        slow = sum(int(r["server"].get("slow", {}).get("500", 0)) for r in rows)
        out.append({"run": label, "ticks": len(rows), "cv_client_p99": cv(cli), "cv_server_p99": cv(srv),
                    "r_client_net": pearson(cli, net), "r_server_net": pearson(srv, net),
                    "median_client_p99_ms": float(np.median(cli)), "median_server_p99_ms": float(np.median(srv)),
                    "median_net_p99_ms": float(np.median(net)), "server_requests_over_500ms": slow})
    return out


def a2_stalls() -> list[dict[str, Any]]:
    out = []
    for label, rel in STALL_RUNS:
        p = DATA / rel
        if p.exists():
            s = json.loads(p.read_text())["summary"]
            out.append({"run": label, **{k: s.get(k) for k in ("requests", "slow", "slow_share", "span_s", "events",
                                                                 "events_per_min", "event_duration_s_median",
                                                                 "events_with_ping_spike")}})
    return out


# ----------------------------------------------------------------------------- A3

def windows(ts: np.ndarray, ms: np.ndarray, width_s: float) -> list[np.ndarray]:
    edges = np.arange(ts.min(), ts.max() - width_s + 1e-9, width_s)
    idx = np.searchsorted(ts, edges)
    end = np.searchsorted(ts, edges + width_s)
    return [ms[a:b] for a, b in zip(idx, end, strict=True) if b - a > 0]


def bootstrap_cv(sample: np.ndarray, q: float, rng: np.random.Generator, n_boot: int = BOOTSTRAPS) -> float:
    """CV of the nearest-rank q-quantile under resampling of one window (pure sampling noise)."""
    n = len(sample)
    draws = np.sort(sample[rng.integers(0, n, size=(n_boot, n))], axis=1)
    est = draws[:, max(0, math.ceil(q * n) - 1)]
    return float(est.std(ddof=1) / est.mean()) if est.mean() else float("nan")


def a3_estimator_noise() -> dict[str, Any]:
    rows = read_jsonl(DATA / RAW_CAPTURE)
    rows.sort(key=lambda r: r["t"])
    ts = np.array([r["t"] for r in rows])
    ms = np.array([r["ms"] for r in rows], float)
    rng = np.random.default_rng(SEED)
    cells = []
    for w in WINDOWS_S:
        ws = windows(ts, ms, w)
        for q in QUANTILES:
            boot = [bootstrap_cv(x, q, rng) for x in ws]
            observed = [_nearest_rank(sorted(x.tolist()), q) for x in ws]
            n_med = float(np.median([len(x) for x in ws]))
            total_cv = cv(observed)
            boot_cv = float(np.median(boot))
            cells.append({"window_s": w, "q": q, "windows": len(ws), "n_median": n_med,
                          "k_tail": n_med * (1 - q), "bootstrap_cv": boot_cv, "observed_cv": total_cv,
                          "sampling_share_of_variance": (boot_cv / total_cv) ** 2 if total_cv else None})
    tail = [c for c in cells if c["q"] >= 0.9 and c["k_tail"] >= 1]
    slope, intercept = np.polyfit(np.log([c["k_tail"] for c in tail]), np.log([c["bootstrap_cv"] for c in tail]), 1)
    a = float(math.exp(intercept))
    k_needed = {f"{t:.2f}": (a / t) ** (1 / -slope) if slope < 0 else None for t in TARGET_CVS}
    return {"requests": len(rows), "span_min": float((ts.max() - ts.min()) / 60), "rate_per_s": len(rows) / float(ts.max() - ts.min()),
            "cells": cells, "fit": {"model": "cv = a * k^b over q >= 0.9", "a": a, "b": float(slope)},
            "k_tail_needed_for_target_cv": k_needed}


# ----------------------------------------------------------------------------- A4, A5

def calibrate_threshold(xs: Sequence[float]) -> float:
    return ceil_step(L_SLA_FACTOR * _nearest_rank(sorted(xs), 0.95))


def false_alarms(xs: Sequence[float], threshold: float) -> dict[str, float]:
    breach = [x > threshold for x in xs]
    pairs = sum(1 for a, b in itertools.pairwise(breach) if a and b)
    hours = len(xs) * TICK_S / 3600
    return {"breach_rate": float(np.mean(breach)), "breach_pairs_per_hour": pairs / hours,
            "breaches_per_hour": sum(breach) / hours}


def a4_false_alarms() -> list[dict[str, Any]]:
    out = []
    for label, rel in DUAL_SIGNAL_RUNS:
        rows = [r for r in read_jsonl(DATA / rel) if not r["stale"]]
        half = len(rows) // 2
        for sig, key in (("server p95", "latency_ms"), ("client p99", "client_p99_ms")):
            xs = [float(r[key]) for r in rows]
            thr = calibrate_threshold(xs[:half])
            out.append({"run": label, "signal": sig, "ticks": len(xs), "cv": cv(xs), "threshold_ms": thr,
                        "threshold_over_median": thr / float(np.median(xs[:half])),
                        **false_alarms(xs[half:], thr)})
    return out


def a5_detectability() -> list[dict[str, Any]]:
    cal = [r for r in read_jsonl(DATA / FINAL_CALIBRATION) if not r["stale"]]
    thr = {"server p95": calibrate_threshold([r["latency_ms"] for r in cal]),
           "client p99": calibrate_threshold([r["client_p99_ms"] for r in cal])}
    smoke = {r["run"].removeprefix("fault_smoke-"): r for r in read_jsonl(DATA / "m3/fault_smoke.jsonl")}
    out = []
    for name in FINAL_SMOKES:
        files = sorted((DATA / "transitions" / f"fault_smoke-{name}").glob("episode_*.jsonl"))
        if not files or name not in smoke:
            continue
        s = smoke[name]
        ticks = read_jsonl(files[0])
        act = s.get("acted_at_tick")
        pre = [t for t in ticks if t["phase"] == "lead_in"]
        fault = [t for t in ticks if t["phase"] == "fault" and (act is None or t["tick"] < act)]
        row: dict[str, Any] = {"smoke": name, "fault": s["fault"], "target": s.get("target"), "severity": s.get("severity"),
                               "fault_ticks_before_remedy": len(fault), "mttr_s": s.get("mttr_s")}
        for sig, get in (("server p95", lambda t: t["raw"]["features"]["latency_ms"]),
                         ("client p99", lambda t: t["raw"]["locust"]["p99_ms"])):
            peak = max(get(t) for t in fault) if fault else None
            base = max(get(t) for t in pre) if pre else None
            row[sig] = {"threshold_ms": thr[sig], "peak_ms": peak, "peak_over_threshold": peak / thr[sig] if peak else None,
                        "lead_in_max_over_threshold": base / thr[sig] if base else None}
        out.append(row)
    return out


# ----------------------------------------------------------------------------- A6

def a6_transport() -> list[dict[str, Any]]:
    out = []
    for label, stamps in TRANSPORT_RUNS:
        timeouts = ticks = stale = 0
        for st in stamps:
            ev = DATA / "logs" / f"calibrate-{st}.events.jsonl"
            cal = DATA / "calibration" / f"{st}.jsonl"
            if not ev.exists() or not cal.exists():
                continue
            rows = read_jsonl(cal)
            ticks += len(rows)
            stale += sum(1 for r in rows if r["stale"])
            timeouts += sum(1 for e in read_jsonl(ev) if e.get("event") == "source_failed"
                            and e.get("component", "").split(".")[-1] in ("cpu", "thr", "mem")
                            and "ConnectTimeout" in str(e.get("error_type")))
        out.append({"transport": label, "runs": stamps, "ticks": ticks, "prom_connect_timeouts": timeouts,
                    "timeouts_per_100_ticks": 100 * timeouts / ticks if ticks else None,
                    "stale_share": stale / ticks if ticks else None})
    return out


# ----------------------------------------------------------------------------- A7

A7_RUNS = ["transitions/runbook_eval", "transitions/warmstart_runbook"]
A7_DETECT_TICKS = 2                     # the runbook's not-H(2): it cannot act before two unhealthy ticks
NOOP_ACTION = 0


def fault_free_ticks(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ticks with no fault active and no action taken earlier in the episode (state unperturbed). Pure."""
    out = []
    for t in sorted(ticks, key=lambda r: r["tick"]):
        if t["fault"] != "NULL" and t["phase"] != "lead_in":
            break
        out.append(t)
        if int(t["a_exec"]) != NOOP_ACTION:
            break                         # the action affects the next tick's state
    return out


def a7_runbook_view() -> dict[str, Any]:
    cal = [r for r in read_jsonl(DATA / FINAL_CALIBRATION) if not r["stale"]]
    thr = {"server p95": calibrate_threshold([r["latency_ms"] for r in cal]),
           "client p99": calibrate_threshold([r["client_p99_ms"] for r in cal])}
    get = {"server p95": lambda t: t["raw"]["features"]["latency_ms"], "client p99": lambda t: t["raw"]["locust"]["p99_ms"]}
    free: list[list[dict[str, Any]]] = []
    detect: dict[str, dict[str, list[bool]]] = {s: {} for s in thr}
    detect_h: dict[str, dict[str, list[bool]]] = {s: {} for s in thr}     # full health: latency OR failures
    e_sla = load_contract().sla.e_sla
    for rel in A7_RUNS:
        for f in sorted((DATA / rel).glob("episode_*.jsonl")):
            ticks = [t for t in read_jsonl(f) if not t["stale"] and t.get("source") != "probe"]
            if not ticks:
                continue
            free.append(fault_free_ticks(ticks))
            fault = ticks[0]["fault"]
            first = sorted((t for t in ticks if t["phase"] == "fault"), key=lambda r: r["tick"])[:A7_DETECT_TICKS]
            if fault != "NULL" and len(first) == A7_DETECT_TICKS:
                for sig, g in get.items():
                    detect[sig].setdefault(fault, []).append(any(g(t) > thr[sig] for t in first))
                    detect_h[sig].setdefault(fault, []).append(
                        any(g(t) > thr[sig] or t["raw"]["features"]["fail_ratio"] > e_sla for t in first))
    out: dict[str, Any] = {"thresholds_ms": thr, "fault_free": {}, "detected_within_2_ticks": {},
                           "detected_within_2_ticks_full_health": {}}
    n_ticks = sum(len(ep) for ep in free)
    for sig, g in get.items():
        over = sum(1 for ep in free for t in ep if g(t) > thr[sig])
        pairs = sum(1 for ep in free for a, b in itertools.pairwise(ep) if g(a) > thr[sig] and g(b) > thr[sig])
        out["fault_free"][sig] = {"ticks": n_ticks, "over_threshold": over, "share": over / n_ticks if n_ticks else None,
                                  "consecutive_pairs": pairs,
                                  "pairs_per_hour": pairs / (n_ticks * TICK_S / 3600) if n_ticks else None}
        out["detected_within_2_ticks"][sig] = {f: {"detected": sum(v), "episodes": len(v)}
                                               for f, v in sorted(detect[sig].items())}
        out["detected_within_2_ticks_full_health"][sig] = {f: {"detected": sum(v), "episodes": len(v)}
                                                           for f, v in sorted(detect_h[sig].items())}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DATA / "paperB" / "analysis.json")
    args = ap.parse_args(argv)
    result = {"A1_decomposition": a1_decomposition(), "A2_stalls": a2_stalls(),
              "A3_estimator_noise": a3_estimator_noise(), "A4_false_alarms": a4_false_alarms(),
              "A5_detectability": a5_detectability(), "A6_transport": a6_transport(),
              "A7_runbook_view": a7_runbook_view()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
