"""Reward, health and recovery — pure functions (CLAUDE.md §5.6).

    ℓ_t = clip(log2(Lat_t / L_SLA) / 3, 0, 1)      Lat_t: server-side SLA latency (quantile sla.latency_quantile)
    e_t = clip((F_t − e_sla) / (e_max − e_sla), 0, 1)
    v_t = clip(ℓ_t + e_t, 0, 1)
    ρ_t = Σ_d max(0, spec_d − base_d) / replica_denominator
    r_k = −(v_{k+1} + action_cost[kind(a_k^exec)] + w_replica · ρ_{k+1})

No ΔMTTR term and no other shaping.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from env.contract import Contract

LATENCY_LOG2_SPAN = 3.0       # ℓ saturates at Lat = 2^3 · L_SLA


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


def latency_violation(latency_ms: float, l_sla_ms: float) -> float:
    """ℓ_t; 0 at or below the SLA (including latency = 0)."""
    if latency_ms <= l_sla_ms:
        return 0.0
    return _clip01(math.log2(latency_ms / l_sla_ms) / LATENCY_LOG2_SPAN)


def error_violation(fail_ratio: float, contract: Contract) -> float:
    """e_t."""
    s = contract.sla
    return _clip01((fail_ratio - s.e_sla) / (s.e_max - s.e_sla))


def sla_violation(latency_ms: float, fail_ratio: float, l_sla_ms: float, contract: Contract) -> float:
    """v_t = clip(ℓ_t + e_t, 0, 1)."""
    for name, x in (("latency_ms", latency_ms), ("fail_ratio", fail_ratio), ("l_sla_ms", l_sla_ms)):
        if not math.isfinite(x):
            raise ValueError(f"sla_violation: non-finite {name}={x}")
    return _clip01(latency_violation(latency_ms, l_sla_ms) + error_violation(fail_ratio, contract))


def replica_surplus(spec_replicas: Mapping[str, int], contract: Contract) -> float:
    """ρ_t over the managed deployments."""
    base = contract.replicas.base
    surplus = sum(max(0, int(spec_replicas[d]) - base[d]) for d in contract.cluster.managed)
    return surplus / contract.reward.replica_denominator


def reward(v_next: float, executed_kind: str, rho_next: float, contract: Contract) -> float:
    """r_k for the *executed* action kind (a failed dispatch is charged as NOOP by the caller)."""
    cost = contract.reward.action_cost[executed_kind]
    return -(v_next + cost + contract.reward.w_replica * rho_next)


def healthy(latency_ms: float, fail_ratio: float, l_sla_ms: float, contract: Contract) -> bool:
    """H_t := Lat_t ≤ L_SLA ∧ F_t ≤ e_sla (Lat_t: server-side SLA latency)."""
    return latency_ms <= l_sla_ms and fail_ratio <= contract.sla.e_sla


def recovery_start(health: Sequence[bool], cured: Sequence[bool], k_inject: int, contract: Contract) -> int | None:
    """First tick t > k_inject with H_t ∧ … ∧ H_{t+n−1} and the cure condition at t+n−1.

    `health[t]` / `cured[t]` are per-tick flags since the episode start; n = sla.recovery_ticks.
    Returns t (MTTR is measured from injection to wall(T_t)), or None if not recovered yet.
    The episode terminates at t + n − 1.
    """
    n = contract.sla.recovery_ticks
    for t in range(k_inject + 1, len(health) - n + 1):
        if all(health[t:t + n]) and cured[t + n - 1]:
            return t
    return None
