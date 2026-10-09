"""Compute M3 validation gates G1–G7 from run logs and print PASS/FAIL (PLAN.md M3 "Validation Gates").

Inputs (all written by real runs, read-only here):
  <run dir>/episodes.jsonl                 agents/run_policy.py per-episode summaries
  data/transitions/<run>/episode_*.jsonl   per-tick transitions (CLAUDE.md §6)
  data/logs/<run>.events.jsonl             structured events (resets, hard resets)
  data/calibration/<latest>.summary.json   G6 CV (scripts/calibrate.py)
  data/m3/fault_smoke.jsonl                G7 (scripts/fault_smoke.py)

Usage:  python -m scripts.gates --run data/m3/runbook_eval [--only G1,G2,G5]
Exit 0 iff every selected gate passes.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from env.contract import REPO_ROOT, Contract, load_calibration, load_contract

# Gate thresholds — PLAN.md M3 table (verbatim).
G1_JITTER_P95_S = 1.0
G1_DECISION_P95_S = 4.0
G2_MAX_STALE_SHARE = 0.01
G3_MIN_RECOVERY = 0.95
G4_MAX_NULL_ACTIONS = 0
G5_MIN_CLEAN_RESETS = 0.98
G6_MAX_CV = 0.25
G6_MAX_NULL_BREACH = 0.02


@dataclass(frozen=True)
class Gate:
    name: str
    ok: bool
    detail: str


def p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, round(0.95 * len(s)) - 1)] if s else float("nan")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# ----------------------------------------------------------------------------- pure gate functions

def g1(transitions: list[list[dict[str, Any]]], tick_s: float) -> Gate:
    jitter = [abs((b["t_wall"] - a["t_wall"]) - tick_s) for ep in transitions for a, b in itertools.pairwise(ep)
              if b["tick"] == a["tick"] + 1]
    lat = [t["decision_latency_s"] for ep in transitions for t in ep]
    j, d = p95(jitter), p95(lat)
    return Gate("G1 Clock", j < G1_JITTER_P95_S and d <= G1_DECISION_P95_S,
                f"p95 tick jitter {j:.3f} s (< {G1_JITTER_P95_S}); p95 decision latency {d:.2f} s (<= {G1_DECISION_P95_S})")


def g2(transitions: list[list[dict[str, Any]]]) -> Gate:
    ticks = [t for ep in transitions for t in ep]
    share = sum(1 for t in ticks if t["stale"]) / len(ticks) if ticks else float("nan")
    return Gate("G2 Telemetry", share < G2_MAX_STALE_SHARE, f"stale {share:.2%} of {len(ticks)} ticks (< 1%)")


def g3(episodes: list[dict[str, Any]]) -> Gate:
    faults = [e for e in episodes if e["fault"] in ("F1", "F2", "F3", "F4") and not e.get("interrupted")]
    rec = sum(1 for e in faults if e.get("recovered")) / len(faults) if faults else float("nan")
    by = {f: f"{sum(1 for e in faults if e['fault'] == f and e.get('recovered'))}/{sum(1 for e in faults if e['fault'] == f)}"
          for f in ("F1", "F2", "F3", "F4")}
    return Gate("G3 Faults", rec >= G3_MIN_RECOVERY, f"recovered {rec:.1%} of {len(faults)} fault episodes {by} (>= 95%)")


def g4(episodes: list[dict[str, Any]], transitions: list[list[dict[str, Any]]]) -> Gate:
    null_eps = {e["episode"] for e in episodes if e["fault"] == "NULL"}
    acts = sum(1 for ep in transitions for t in ep if t["episode"] in null_eps and t["a_exec"] != 0)
    return Gate("G4 Safety", acts <= G4_MAX_NULL_ACTIONS, f"{acts} actions in {len(null_eps)} NULL episodes (FRR must be 0)")


def g5(events: list[dict[str, Any]]) -> Gate:
    resets = sum(1 for e in events if e["event"] == "reset_done")
    hard = sum(1 for e in events if e["event"] == "reset_hard")
    clean = (resets - hard) / resets if resets else float("nan")
    return Gate("G5 Reset", clean >= G5_MIN_CLEAN_RESETS, f"{resets - hard}/{resets} resets without hard reset (>= 98%)")


def g6(cal_summary: dict[str, Any] | None, episodes: list[dict[str, Any]],
       transitions: list[list[dict[str, Any]]], l_sla_ms: float, e_sla: float) -> Gate:
    cv = (cal_summary or {}).get("g6", {}).get("cv", float("nan"))
    null_eps = {e["episode"] for e in episodes if e["fault"] == "NULL"}
    ticks = [t["raw"]["features"] for ep in transitions for t in ep if t["episode"] in null_eps]
    breach = (sum(1 for f in ticks if f["latency_ms"] > l_sla_ms or f["fail_ratio"] > e_sla) / len(ticks)
              if ticks else float("nan"))
    return Gate("G6 Signal", cv < G6_MAX_CV and breach < G6_MAX_NULL_BREACH,
                f"steady tick SLA-latency CV {cv:.3f} (< 0.25, calibration); NULL-tick SLA breaches {breach:.2%} "
                f"of {len(ticks)} (< 2%)")


def g7(smoke: list[dict[str, Any]], contract: Contract) -> Gate:
    f4 = [r for r in smoke if r["fault"] == "F4" and "g7_f4_bottleneck_ok" in r]
    f2 = [r for r in smoke if r["fault"] == "F2" and "g7_f2_throttle_ok" in r]
    f4_ok = bool(f4) and all(r["g7_f4_bottleneck_ok"] for r in f4)
    seen = {int(r["severity"]) for r in f2}
    missing = sorted(set(contract.episode.f2_workers) - seen)
    f2_ok = not missing and all(r["g7_f2_throttle_ok"] for r in f2)
    return Gate("G7 Calibration", f4_ok and f2_ok,
                f"F4 bottleneck=frontend in {sum(r['g7_f4_bottleneck_ok'] for r in f4)}/{len(f4)} smokes; "
                f"F2 throttle >= θ in {sum(r['g7_f2_throttle_ok'] for r in f2)}/{len(f2)}"
                + (f"; F2 severities not yet smoke-tested: {missing}" if missing else ""))


# ----------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True, help="run_policy --out directory")
    ap.add_argument("--only", help="comma-separated subset, e.g. G1,G2,G5")
    args = ap.parse_args(argv)
    contract = load_contract()
    cal = load_calibration()
    run = args.run.name
    episodes = load_jsonl(args.run / "episodes.jsonl")
    tdir = REPO_ROOT / "data" / "transitions" / run
    transitions = [load_jsonl(p) for p in sorted(tdir.glob("episode_*.jsonl"))]
    events = load_jsonl(REPO_ROOT / "data" / "logs" / f"{run}.events.jsonl")
    sums = sorted((REPO_ROOT / "data" / "calibration").glob("*.summary.json"))
    cal_summary = json.loads(sums[-1].read_text()) if sums else None
    smoke = load_jsonl(REPO_ROOT / "data" / "m3" / "fault_smoke.jsonl")

    gates: dict[str, Callable[[], Gate]] = {
        "G1": lambda: g1(transitions, contract.clock.tick_s),
        "G2": lambda: g2(transitions),
        "G3": lambda: g3(episodes),
        "G4": lambda: g4(episodes, transitions),
        "G5": lambda: g5(events),
        "G6": lambda: g6(cal_summary, episodes, transitions, cal.l_sla_ms, contract.sla.e_sla),
        "G7": lambda: g7(smoke, contract),
    }
    selected = args.only.split(",") if args.only else list(gates)
    print(f"run {run}: {len(episodes)} episodes, {sum(len(t) for t in transitions)} transitions, "
          f"{statistics.median([len(t) for t in transitions]) if transitions else 0} median ticks/episode")
    results = [gates[g]() for g in selected]
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name}: {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
