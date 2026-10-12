"""Per-episode evaluation metrics (docs/PREREGISTRATION.md §4). Pure: transition records in, metrics out.

    MTTR            info["mttr_s"] of a recovered episode; censored at fault_max_ticks·tick_s otherwise
    sla_penalty     Σ_t v_t over the episode (v_t from env.reward.sla_violation on the recorded raw tick)
    false_remed.    NULL episode with ≥ 1 executed non-NOOP action (FRR numerator)
    mask_violations chosen action replaced by NOOP although the tick was valid and dispatch did not fail
    exec_errors     failed dispatches (executor refusal, API error, credential error)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from env.contract import Calibration, Contract
from env.reward import sla_violation

NOOP = 0


@dataclass(frozen=True)
class EpisodeMetrics:
    policy: str
    index: int
    fault: str
    target: str | None
    severity: Any
    valid: bool
    recovered: bool
    mttr_s: float | None
    mttr_censored_s: float
    event_observed: bool              # False = censored (not recovered within the fault window)
    sla_penalty: float
    actions: int
    false_remediation: bool
    mask_violations: int
    exec_errors: int
    ticks: int
    stale_ticks: int
    decision_latency_p95_s: float | None
    episode_return: float

    def row(self) -> dict[str, Any]:
        return asdict(self)


def censor_s(contract: Contract) -> float:
    return float(contract.episode.fault_max_ticks * contract.clock.tick_s)


def episode_metrics(records: Sequence[Mapping[str, Any]], last_info: Mapping[str, Any], policy: str, index: int,
                    cal: Calibration, contract: Contract) -> EpisodeMetrics:
    if not records:
        raise ValueError("episode without transition records")
    first = records[0]
    fault = str(first["fault"])
    valid = all(bool(r["valid"]) for r in records) and not any(bool(r["truncated"]) and not r["valid"] for r in records)
    recovered = bool(last_info.get("recovered")) and fault != "NULL"
    mttr = float(last_info["mttr_s"]) if recovered and last_info.get("mttr_s") is not None else None
    v = [sla_violation(float(r["raw"]["features"]["latency_ms"]), float(r["raw"]["features"]["fail_ratio"]),
                       cal.l_sla_ms, contract) for r in records]
    actions = sum(1 for r in records if int(r["a_exec"]) != NOOP)
    mask_viol = sum(1 for r in records if r["valid"] and not r["exec_error"] and int(r["a_chosen"]) != int(r["a_exec"]))
    lat = [float(r["decision_latency_s"]) for r in records]
    return EpisodeMetrics(
        policy=policy, index=index, fault=fault, target=first.get("target"), severity=first.get("severity"),
        valid=valid, recovered=recovered, mttr_s=mttr,
        mttr_censored_s=mttr if mttr is not None else censor_s(contract), event_observed=mttr is not None,
        sla_penalty=float(sum(v)), actions=actions, false_remediation=(fault == "NULL" and actions > 0),
        mask_violations=mask_viol, exec_errors=sum(1 for r in records if r["exec_error"]), ticks=len(records),
        stale_ticks=sum(1 for r in records if r["stale"]),
        decision_latency_p95_s=float(np.percentile(lat, 95)) if lat else None,
        episode_return=float(sum(float(r["reward"]) for r in records)))
