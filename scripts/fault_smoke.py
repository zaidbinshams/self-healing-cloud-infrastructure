"""Fault smoke test: inject one fault, apply its known-correct remedy, verify cure + recovery (PLAN.md M3 step 4).

Mutates the cluster (one real episode through BoutiqueEnv). The plan is fixed by the CLI; the
oracle remedy comes from the expected-outcome grid of CLAUDE.md §5.2 (documentation only, never
agent input). Before acting, the fault is observed for `runbook.debounce_ticks` ticks, which gives:
  * detectability: did tick SLA latency or the failure ratio exceed the SLA (L_SLA / e_sla) before any
    remedy? A fault the SLA cannot see cannot be learned (G6 discussion, 2026-10-09);
  * G7 evidence: F4 — is frontend the most throttled managed service and >= θ?
                 F2 — does the target's throttle reach >= θ?
Writes one JSON result to --out (appended) and exits 0 iff cured, recovered and detectable.

Usage:  source config/cluster.env
        python -m scripts.fault_smoke --fault F1 --severity 600ms
        python -m scripts.fault_smoke --fault F2 --target currencyservice --workers 2
        python -m scripts.fault_smoke --fault F3 --target cartservice
        python -m scripts.fault_smoke --fault F4 --multiplier 3.0 --check-bottleneck frontend
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from env.boutique_env import EnvironmentDegraded, make_env
from env.contract import REPO_ROOT, ContractError
from env.injector import F1_TARGET, F4_TARGET, FaultPlan
from env.k8s_actions import CATALOG, RESTART, RESTORE, SCALE
from env.telemetry import FEATURES_PER_SERVICE, SERVICE_OFFSET

F_THROTTLE = 1


def oracle_action(plan: FaultPlan) -> int:
    """Known-correct remedy (§5.2 grid 'C' cells). Pure."""
    def find(kind: str, target: str, delta: int = 0) -> int:
        return next(a.id for a in CATALOG if a.kind == kind and a.target == target and (kind != SCALE or a.delta == delta))
    if plan.fault == "F1":
        return find(RESTORE, F1_TARGET)
    if plan.fault == "F2":
        return find(RESTART, str(plan.target))
    if plan.fault == "F3":
        return find(RESTORE, str(plan.target))
    if plan.fault == "F4":
        return find(SCALE, F4_TARGET, +1)
    raise ValueError(f"no oracle for {plan.fault}")


def throttle_by_service(obs_vec: Any, managed: tuple[str, ...]) -> dict[str, float]:
    return {d: float(obs_vec[SERVICE_OFFSET + FEATURES_PER_SERVICE * i + F_THROTTLE]) for i, d in enumerate(managed)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fault", choices=["F1", "F2", "F3", "F4"], required=True)
    ap.add_argument("--target")
    ap.add_argument("--severity", help="F1 latency, e.g. 600ms")
    ap.add_argument("--workers", type=int, help="F2 stress workers")
    ap.add_argument("--multiplier", type=float, help="F4 users multiplier")
    ap.add_argument("--check-bottleneck", help="F4: the service expected to be the most throttled")
    ap.add_argument("--lead-in", type=int, default=2)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "m3" / "fault_smoke.jsonl")
    args = ap.parse_args(argv)

    if args.fault == "F1":
        plan = FaultPlan("F1", F1_TARGET, args.severity or "600ms", args.lead_in)
    elif args.fault == "F2":
        if not args.target or not args.workers:
            ap.error("F2 needs --target and --workers")
        plan = FaultPlan("F2", args.target, args.workers, args.lead_in)
    elif args.fault == "F3":
        if not args.target:
            ap.error("F3 needs --target")
        plan = FaultPlan("F3", args.target, None, args.lead_in)
    else:
        if not args.multiplier:
            ap.error("F4 needs --multiplier")
        plan = FaultPlan("F4", F4_TARGET, args.multiplier, args.lead_in)

    run = f"fault_smoke-{args.fault}-{time.strftime('%Y%m%dT%H%M%S')}"
    try:
        env = make_env(run, seed=0)
    except (ContractError, RuntimeError) as exc:
        print(f"fault_smoke: {exc}", file=sys.stderr)
        return 2
    c, cal = env.c, env.cal
    theta = c.runbook.throttle_threshold
    remedy = oracle_action(plan)
    result: dict[str, Any] = {"run": run, "fault": plan.fault, "target": plan.target, "severity": plan.severity,
                              "remedy": CATALOG[remedy].name, "l_sla_ms": cal.l_sla_ms}
    try:
        obs, _info = env.reset(options={"plan": plan})
        fault_ticks: list[dict[str, Any]] = []
        acted_at, terminated, truncated, last = None, False, False, {}
        while not (terminated or truncated):
            ep = env.ep
            ready = acted_at is None and len(fault_ticks) >= c.runbook.debounce_ticks
            still_bad = ep.features is not None and ep.features.latency_ms > cal.l_sla_ms
            if obs["mask"][remedy] and (ready or (plan.fault == "F4" and acted_at is not None and still_bad)):
                a = remedy                                   # F4 keeps scaling while still above the SLA
            else:
                a = 0
            obs, _r, terminated, truncated, last = env.step(a)
            if acted_at is None and a == remedy and last["a_exec"] == remedy:
                acted_at = last["tick"]
            # evidence from every post-injection tick before the remedy (incl. the injection tick)
            if acted_at is None and env.ep.k_inject is not None and env.ep.features is not None:
                f = env.ep.features
                fault_ticks.append({"tick": last["tick"], "latency_ms": f.latency_ms, "fail": f.fail_ratio,
                                    "throttle": throttle_by_service(obs["obs"], c.cluster.managed)})
    except EnvironmentDegraded as exc:
        print(f"fault_smoke: environment degraded: {exc}", file=sys.stderr)
        env.close()
        return 3
    env.close()

    max_lat = max((t["latency_ms"] for t in fault_ticks), default=0.0)
    max_fail = max((t["fail"] for t in fault_ticks), default=0.0)
    detectable = max_lat > cal.l_sla_ms or max_fail > c.sla.e_sla
    if last.get("inject_failed"):
        result["inject_failed"] = True
        print("fault_smoke: injection FAILED (see the run's events log); episode ended as invalid", file=sys.stderr)
    result.update({"acted_at_tick": acted_at, "fault_ticks_observed": fault_ticks, "max_latency_before_remedy_ms": max_lat,
                   "max_fail_before_remedy": max_fail, "detectable": detectable, "cured": last.get("cured"),
                   "recovered": last.get("recovered"), "mttr_s": last.get("mttr_s"),
                   "terminated": terminated, "truncated": truncated})
    if plan.fault == "F4" and fault_ticks:
        peak = {d: max(t["throttle"][d] for t in fault_ticks) for d in c.cluster.managed}
        expected = args.check_bottleneck or F4_TARGET
        result["f4_peak_throttle"] = peak
        result["g7_f4_bottleneck_ok"] = max(peak, key=peak.get) == expected and peak[expected] >= theta
    if plan.fault == "F2" and fault_ticks:
        peak = max(t["throttle"][str(plan.target)] for t in fault_ticks)
        result["f2_peak_target_throttle"] = peak
        result["g7_f2_throttle_ok"] = peak >= theta
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "fault_ticks_observed"}, indent=1))
    ok = bool(result["cured"] and result["recovered"] and detectable)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
