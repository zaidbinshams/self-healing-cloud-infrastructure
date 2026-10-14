"""Export Paper B's figure and table data as plain CSV (for redrawing in any tool). Read-only over data/.

Writes one CSV per figure/table into paper_b/figure_data/, from figures/paperB/analysis.json and the recorded
diag run used by Fig. b2. Every value is a recorded measurement or a statistic computed from one.

Usage:  python -m eval.paperb_analysis && python -m eval.paperb_export [--out paper_b/figure_data]
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from env.contract import REPO_ROOT
from eval.paperb_analysis import DATA, read_jsonl
from eval.paperb_figures import TREATMENT_A_RUN


def write(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def r3(x: Any) -> Any:
    return round(x, 4) if isinstance(x, float) else x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, default=REPO_ROOT / "figures" / "paperB" / "analysis.json")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "paper_b" / "figure_data")
    args = ap.parse_args(argv)
    a = json.loads(args.analysis.read_text())
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    write(out / "fig_b1_decomposition.csv",
          ["run", "ticks", "cv_client_p99", "cv_server_p99", "r_client_vs_ping_p99", "r_server_vs_ping_p99",
           "median_client_p99_ms", "median_server_p99_ms", "median_ping_p99_ms", "server_requests_over_500ms"],
          [[r["run"], r["ticks"], r3(r["cv_client_p99"]), r3(r["cv_server_p99"]), r3(r["r_client_net"]),
            r3(r["r_server_net"]), r3(r["median_client_p99_ms"]), r3(r["median_server_p99_ms"]),
            r3(r["median_net_p99_ms"]), r["server_requests_over_500ms"]] for r in a["A1_decomposition"]])

    rows = [r for r in read_jsonl(DATA / TREATMENT_A_RUN) if not r.get("errors")]
    write(out / "fig_b2_timeseries_treatmentA.csv",
          ["tick", "client_p99_ms", "ping_p99_ms", "server_p99_ms", "client_n_requests", "server_n_requests"],
          [[r["tick"], r3(r["client"]["p99_ms"]), r3(r["net"]["p99_ms"]), r3(r["server"]["p99_ms"]),
            r["client"]["n"], r["server"]["n"]] for r in rows])

    a3 = a["A3_estimator_noise"]
    write(out / "fig_b3_estimator_noise.csv",
          ["window_s", "quantile", "windows", "median_requests_per_window", "expected_tail_samples_k",
           "bootstrap_sampling_cv", "observed_window_to_window_cv", "sampling_share_of_variance"],
          [[c["window_s"], c["q"], c["windows"], c["n_median"], r3(c["k_tail"]), r3(c["bootstrap_cv"]),
            r3(c["observed_cv"]), r3(c["sampling_share_of_variance"])] for c in a3["cells"]])
    write(out / "fig_b3_fit.csv", ["model", "a", "b", "k_for_cv_0.10", "k_for_cv_0.15", "requests", "span_min"],
          [[a3["fit"]["model"], r3(a3["fit"]["a"]), r3(a3["fit"]["b"]), r3(a3["k_tail_needed_for_target_cv"]["0.10"]),
            r3(a3["k_tail_needed_for_target_cv"]["0.15"]), a3["requests"], r3(a3["span_min"])]])

    write(out / "fig_b4_detectability.csv",
          ["smoke", "fault", "target", "severity", "signal", "threshold_ms", "peak_ms", "peak_over_threshold",
           "lead_in_max_over_threshold"],
          [[r["smoke"], r["fault"], r["target"], r["severity"], sig, r3(r[sig]["threshold_ms"]), r3(r[sig]["peak_ms"]),
            r3(r[sig]["peak_over_threshold"]), r3(r[sig]["lead_in_max_over_threshold"])]
           for r in a["A5_detectability"] for sig in ("server p95", "client p99")])

    write(out / "table_stalls.csv", ["run", "requests", "slow_over_500ms", "slow_share", "span_s", "stall_events",
                                     "events_per_min", "event_duration_s_median", "events_with_ping_spike"],
          [[r["run"], r["requests"], r["slow"], r3(r["slow_share"]), r3(r["span_s"]), r["events"],
            r3(r["events_per_min"]), r3(r["event_duration_s_median"]), r["events_with_ping_spike"]]
           for r in a["A2_stalls"]])

    write(out / "table_threshold_inflation.csv",
          ["run", "signal", "ticks", "cv", "threshold_ms", "threshold_over_median", "heldout_breach_rate",
           "heldout_breaches_per_hour", "heldout_consecutive_pairs_per_hour"],
          [[r["run"], r["signal"], r["ticks"], r3(r["cv"]), r["threshold_ms"], r3(r["threshold_over_median"]),
            r3(r["breach_rate"]), r3(r["breaches_per_hour"]), r3(r["breach_pairs_per_hour"])]
           for r in a["A4_false_alarms"]])

    a7 = a["A7_runbook_view"]
    write(out / "table_runbook_view_detection.csv",
          ["fault", "signal", "detected_latency_only", "detected_full_health", "episodes"],
          [[f, sig, a7["detected_within_2_ticks"][sig][f]["detected"],
            a7["detected_within_2_ticks_full_health"][sig][f]["detected"], v["episodes"]]
           for sig in a7["detected_within_2_ticks"] for f, v in a7["detected_within_2_ticks"][sig].items()])
    write(out / "table_runbook_view_fault_free.csv",
          ["signal", "threshold_ms", "fault_free_ticks", "over_threshold", "share", "consecutive_pairs"],
          [[sig, a7["thresholds_ms"][sig], v["ticks"], v["over_threshold"], r3(v["share"]), v["consecutive_pairs"]]
           for sig, v in a7["fault_free"].items()])

    write(out / "table_transport.csv", ["transport", "calibration_runs", "ticks", "prom_connect_timeouts",
                                        "timeouts_per_100_ticks", "stale_share"],
          [[r["transport"], " ".join(r["runs"]), r["ticks"], r["prom_connect_timeouts"],
            r3(r["timeouts_per_100_ticks"]), r3(r["stale_share"])] for r in a["A6_transport"]])

    print(f"wrote {len(list(out.glob('*.csv')))} CSV files to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
