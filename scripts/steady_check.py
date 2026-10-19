"""Session-start steady check against the frozen calibration (docs/PREREGISTRATION.md, Amendment 3).

Records N steady ticks with scripts/record_ticks.py (read-only telemetry; Locust must be running at U_base)
and applies the rule fixed on 13 Oct 2026, before any data was seen:

    median tick SLA latency within ±30 % of the calibration median
    95th percentile of tick SLA latency ≤ 1.5 × the calibration q95
    share of ticks breaching L_SLA ≤ 2 %

Calibration median/q95 come from the summary of the run behind config/calibration.json (matched on
calibrated_at). Exit 0 = PASS (the frozen calibration stands), 1 = FAIL, 2 = could not run.

Usage:  source config/cluster.env
        python -m scripts.steady_check [--ticks 30] [--from data/m3/steady_check_X.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from env.contract import CALIBRATION_PATH, REPO_ROOT

MEDIAN_TOLERANCE = 0.30          # rule of 13 Oct 2026 (Amendment 3)
Q95_FACTOR = 1.5
MAX_BREACH_SHARE = 0.02


@dataclass(frozen=True)
class Verdict:
    ticks: int
    stale: int
    median_ms: float
    q95_ms: float
    breach_share: float
    median_ok: bool
    q95_ok: bool
    breach_ok: bool

    @property
    def passed(self) -> bool:
        return self.median_ok and self.q95_ok and self.breach_ok


def nearest_rank(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def evaluate(latencies_ms: list[float], stale: int, cal_median_ms: float, cal_q95_ms: float,
             l_sla_ms: float) -> Verdict:
    """The pre-registered rule, pure."""
    if not latencies_ms:
        raise ValueError("no non-stale ticks")
    med, q95 = statistics.median(latencies_ms), nearest_rank(latencies_ms, 0.95)
    breach = sum(x > l_sla_ms for x in latencies_ms) / len(latencies_ms)
    return Verdict(len(latencies_ms) + stale, stale, med, q95, breach,
                   abs(med - cal_median_ms) <= MEDIAN_TOLERANCE * cal_median_ms,
                   q95 <= Q95_FACTOR * cal_q95_ms, breach <= MAX_BREACH_SHARE)


def calibration_reference() -> tuple[float, float, float]:
    cal = json.loads(CALIBRATION_PATH.read_text())
    for f in sorted((REPO_ROOT / "data" / "calibration").glob("*.summary.json")):
        s = json.loads(f.read_text())
        if s.get("measured_at") == cal["calibrated_at"]:
            return float(s["latency_median_ms"]), float(s["latency_q95_ms"]), float(cal["l_sla_ms"])
    raise FileNotFoundError("no calibration summary matches calibration.json calibrated_at")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticks", type=int, default=30)
    ap.add_argument("--from", dest="src", type=Path, default=None, help="evaluate an existing recording instead")
    args = ap.parse_args(argv)
    try:
        cal_med, cal_q95, l_sla = calibration_reference()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"steady_check: {exc}", file=sys.stderr)
        return 2
    src = args.src
    if src is None:
        src = REPO_ROOT / "data" / "m3" / f"steady_check_{time.strftime('%Y%m%dT%H%M')}.jsonl"
        rc = subprocess.run([sys.executable, "-m", "scripts.record_ticks", "--n", str(args.ticks), "--out", str(src)],
                            cwd=REPO_ROOT, check=False).returncode
        if rc != 0:
            print(f"steady_check: record_ticks exited {rc}", file=sys.stderr)
            return 2
    rows = [json.loads(x) for x in src.read_text().splitlines() if x.strip()]
    ticks = [r["features"] for r in rows if r.get("kind") != "header"]
    lat = [float(f["latency_ms"]) for f in ticks if not f["stale"]]
    try:
        v = evaluate(lat, sum(1 for f in ticks if f["stale"]), cal_med, cal_q95, l_sla)
    except ValueError as exc:
        print(f"steady_check: {exc}", file=sys.stderr)
        return 2
    lo, hi = (1 - MEDIAN_TOLERANCE) * cal_med, (1 + MEDIAN_TOLERANCE) * cal_med
    print(f"steady_check: {src}")
    print(f"  ticks {v.ticks} (stale {v.stale})")
    print(f"  median {v.median_ms:.0f} ms  (rule {lo:.1f}-{hi:.1f})  {'ok' if v.median_ok else 'FAIL'}")
    print(f"  q95    {v.q95_ms:.0f} ms  (rule <= {Q95_FACTOR * cal_q95:.1f})  {'ok' if v.q95_ok else 'FAIL'}")
    print(f"  breach {v.breach_share:.1%} of ticks > {l_sla:.0f} ms  (rule <= {MAX_BREACH_SHARE:.0%})  "
          f"{'ok' if v.breach_ok else 'FAIL'}")
    print(f"  VERDICT: {'PASS: the frozen calibration stands' if v.passed else 'FAIL: do not start training'}")
    return 0 if v.passed else 1


if __name__ == "__main__":
    sys.exit(main())
