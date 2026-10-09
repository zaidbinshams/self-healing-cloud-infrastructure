"""Calibrate the steady baseline and write config/calibration.json (PLAN.md M3 step 3).

1. Refuse unless the live namespace matches golden_live.json and the tracked tree is clean
   (calibration.json records the git SHA the env code was calibrated against, CLAUDE.md §7).
2. U_base search: set Locust's user count, discard warm-up ticks, measure the frontend's CPU
   relative to its limit (cpu_util[frontend], §5.3) over probe ticks, and adjust the user count
   proportionally until it lies in contract `calibration.frontend_util_band`.
3. Steady window at U_base for --minutes: record every tick (data/calibration/<stamp>.jsonl).
4. From non-stale steady ticks:  L_SLA = ceil_10ms(l_sla_factor · q95(tick-P99)),
   RPS_base = median RPS.  Gate G6 (PLAN.md M3): CV(tick-P99) < 0.25 and < 2 % of ticks breach
   the SLA. calibration.json is written ONLY if G6 passes; otherwise exit 1 with diagnostics.

Locust user changes are local to Machine B (POST /swarm); nothing on the cluster is mutated.
Exempt from the calibration.json startup requirement (it creates the file, §7).

Usage:  source config/cluster.env
        python -m scripts.calibrate --minutes 30
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from kubernetes import client

from env.clock import TickClock
from env.contract import (
    CALIBRATION_PATH,
    REPO_ROOT,
    ContractError,
    load_contract,
    load_limits,
)
from env.golden import load_golden, template_hash
from env.recorder import EventLog
from env.telemetry import Collector, ImputeState, impute, make_api_client

G6_MAX_CV = 0.25                 # PLAN.md M3 gate G6
G6_MAX_BREACH_SHARE = 0.02       # PLAN.md M3 gate G6: < 2 % of NULL ticks breach the SLA
L_SLA_ROUND_MS = 10              # CLAUDE.md §5.1: ceil to 10 ms
FRONTEND = "frontend"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout.strip()


def quantile_nearest_rank(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def l_sla_from(p99s: list[float], factor: float) -> float:
    """L_SLA = ceil_10ms(factor * q95(steady tick-P99)) (§5.1). Pure."""
    raw = factor * quantile_nearest_rank(p99s, 0.95)
    return float(math.ceil(raw / L_SLA_ROUND_MS) * L_SLA_ROUND_MS)


def next_users(users: int, util: float, band: tuple[float, float]) -> int:
    """Proportional step toward the band centre (CPU ~ linear in load); always moves by >= 1. Pure."""
    mid = (band[0] + band[1]) / 2
    target = max(1, round(users * mid / util)) if util > 0 else users * 2
    if target == users:
        target = users + (1 if util < band[0] else -1)
    return max(1, target)


def g6(p99s: list[float], fails: list[float], l_sla_ms: float, e_sla: float) -> dict[str, Any]:
    cv = statistics.pstdev(p99s) / statistics.fmean(p99s)
    breach = sum(1 for p, f in zip(p99s, fails, strict=True) if p > l_sla_ms or f > e_sla) / len(p99s)
    return {"cv": cv, "breach_share": breach, "pass": cv < G6_MAX_CV and breach < G6_MAX_BREACH_SHARE}


class Calibrator:
    def __init__(self, contract: Any, events: EventLog) -> None:
        self.c = contract
        self.limits = load_limits(contract)
        self.collector = Collector(contract, os.environ["PROM_URL"], os.environ["LOCUST_URL"],
                                   make_api_client(os.environ["KUBE_AGENT"]), events)
        self.clock = TickClock(contract.clock.tick_s, contract.clock.late_frac)
        self.locust_url = os.environ["LOCUST_URL"]
        self.k = 0

    def swarm(self, users: int) -> None:
        resp = requests.post(f"{self.locust_url.rstrip('/')}/swarm", data={"user_count": users, "spawn_rate": users},
                             timeout=self.c.telemetry.locust_timeout_s)
        resp.raise_for_status()

    def ticks(self, n: int) -> list[dict[str, Any]]:
        """n real ticks on a freshly anchored clock; one record per tick."""
        out, state = [], ImputeState()
        self.clock.anchor()
        for k in range(n):
            self.clock.wait_boundary(k + 1)
            raw = self.collector.collect(self.k, self.clock.wall(k), self.clock.wall(k + 1))
            self.k += 1
            f, state = impute(raw, state, self.c)
            fe = f.services[FRONTEND]
            out.append({"t_wall": self.clock.wall(k + 1), "p99_ms": f.p99_ms, "fail": f.fail_ratio, "rps": f.rps,
                        "n": raw.locust.n, "stale": f.stale,
                        "frontend_util": fe.cpu_cores / (self.limits[FRONTEND].cpu_cores * max(1.0, fe.status_replicas)),
                        "frontend_throttle": fe.throttle})
        return out


def verify_golden(contract: Any) -> list[str]:
    api = make_api_client(os.environ["KUBE_AGENT"])
    deps = {d.metadata.name: d for d in client.AppsV1Api(api).list_namespaced_deployment(
        contract.cluster.namespace, _request_timeout=contract.telemetry.k8s_timeout_s).items}
    problems = []
    for name, g in load_golden(contract).items():
        dep = deps.get(name)
        if dep is None or template_hash(api.sanitize_for_serialization(dep.spec.template)) != g.template_hash \
                or dep.spec.replicas != g.replicas:
            problems.append(name)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, required=True, help="steady window length")
    ap.add_argument("--start-users", type=int, default=40)
    ap.add_argument("--warmup-ticks", type=int, default=2)
    ap.add_argument("--probe-ticks", type=int, default=6)
    ap.add_argument("--max-search", type=int, default=8)
    args = ap.parse_args(argv)
    missing = [v for v in ("KUBE_AGENT", "PROM_URL", "LOCUST_URL") if not os.environ.get(v)]
    if missing:
        ap.error(f"unset {missing} (source config/cluster.env)")
    try:
        contract = load_contract()
    except ContractError as exc:
        print(f"calibrate: {exc}", file=sys.stderr)
        return 2
    if _git("status", "--porcelain", "--untracked-files=no"):
        print("calibrate: refusing: tracked working tree is dirty (calibration records the env git SHA)", file=sys.stderr)
        return 2
    drift = verify_golden(contract)
    if drift:
        print(f"calibrate: refusing: live deployments differ from golden_live.json: {drift}", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_dir = REPO_ROOT / "data" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    events = EventLog(REPO_ROOT / "data" / "logs" / f"calibrate-{stamp}.events.jsonl")
    cal = Calibrator(contract, events)
    band = contract.calibration.frontend_util_band

    users, u_base, search = args.start_users, None, []
    for _ in range(args.max_search):
        cal.swarm(users)
        cal.ticks(args.warmup_ticks)
        probe = [t for t in cal.ticks(args.probe_ticks) if not t["stale"]]
        util = statistics.median(t["frontend_util"] for t in probe) if probe else float("nan")
        search.append({"users": users, "frontend_util": util, "valid_ticks": len(probe)})
        print(f"search: users={users} frontend_util={util:.3f} (band {band[0]:.2f}-{band[1]:.2f})", flush=True)
        if band[0] <= util <= band[1]:
            u_base = users
            break
        if not math.isfinite(util):
            continue
        users = next_users(users, util, band)
    if u_base is None:
        print(f"calibrate: no user count put frontend in the band after {args.max_search} probes: {search}",
              file=sys.stderr)
        return 1

    n_steady = round(args.minutes * 60 / contract.clock.tick_s)
    print(f"steady: {n_steady} ticks at U_base={u_base}", flush=True)
    cal.swarm(u_base)
    cal.ticks(args.warmup_ticks)
    steady = cal.ticks(n_steady)
    ticks_path = out_dir / f"{stamp}.jsonl"
    ticks_path.write_text("".join(json.dumps(t) + "\n" for t in steady))
    valid = [t for t in steady if not t["stale"]]
    p99s, fails = [t["p99_ms"] for t in valid], [t["fail"] for t in valid]
    l_sla = l_sla_from(p99s, contract.sla.l_sla_factor)
    rps_base = statistics.median(t["rps"] for t in valid)
    gate = g6(p99s, fails, l_sla, contract.sla.e_sla)
    summary = {
        "stamp": stamp, "u_base": u_base, "search": search, "steady_ticks": len(steady), "valid_ticks": len(valid),
        "stale_share": 1 - len(valid) / len(steady), "l_sla_ms": l_sla, "rps_base": rps_base,
        "p99_median_ms": statistics.median(p99s), "p99_q95_ms": quantile_nearest_rank(p99s, 0.95),
        "frontend_util_median": statistics.median(t["frontend_util"] for t in valid), "g6": gate,
        "ticks_file": str(ticks_path.relative_to(REPO_ROOT)), "git_sha": _git("rev-parse", "HEAD"),
    }
    (out_dir / f"{stamp}.summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("u_base", "l_sla_ms", "rps_base", "valid_ticks", "stale_share",
                                               "p99_median_ms", "p99_q95_ms", "frontend_util_median", "g6")},
                     indent=1))
    if not gate["pass"]:
        print(f"calibrate: G6 FAILED (CV {gate['cv']:.3f} vs < {G6_MAX_CV}; breach {gate['breach_share']:.2%} vs "
              f"< {G6_MAX_BREACH_SHARE:.0%}); calibration.json NOT written", file=sys.stderr)
        return 1
    CALIBRATION_PATH.write_text(json.dumps({
        "l_sla_ms": l_sla, "rps_base": rps_base, "u_base": u_base,
        "calibrated_at": datetime.now(timezone.utc).isoformat(), "env_git_sha": summary["git_sha"],
    }, indent=2) + "\n")
    print(f"wrote {CALIBRATION_PATH} (commit it with a CONTRACT-CHANGE line)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
