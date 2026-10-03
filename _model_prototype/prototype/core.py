"""Pure functions shared by the agent, the runbook and the toy simulator.

These follow CLAUDE.md §5.3 (observation), §5.6 (reward/health) and §5.8 (mask)
and contain no I/O, so they can be reused unchanged against the real cluster.
"""
from __future__ import annotations

import math

import numpy as np

from .contract import ACTIONS, CALIBRATION, CONTRACT, MANAGED, N_ACTIONS, OBS_DIM

L_SLA = CALIBRATION["l_sla_ms"]
RPS_BASE = CALIBRATION["rps_base"]
BASE = CONTRACT["replicas"]["base"]
MAX = CONTRACT["replicas"]["max"]
SLA = CONTRACT["sla"]
RW = CONTRACT["reward"]


# ---------------------------------------------------------------- reward (§5.6)
def latency_violation(p99_ms: float) -> float:
    return float(np.clip(math.log2(max(p99_ms, 1e-6) / L_SLA) / 3.0, 0.0, 1.0))


def error_violation(fail: float) -> float:
    return float(np.clip((fail - SLA["e_sla"]) / (SLA["e_max"] - SLA["e_sla"]), 0.0, 1.0))


def sla_violation(p99_ms: float, fail: float) -> float:
    return float(np.clip(latency_violation(p99_ms) + error_violation(fail), 0.0, 1.0))


def replica_surplus(spec: dict[str, int]) -> float:
    return sum(max(0, spec[d] - BASE[d]) for d in MANAGED) / RW["replica_denominator"]


def reward(p99_next_ms: float, fail_next: float, spec_next: dict[str, int], a_exec: int) -> float:
    kind = ACTIONS[a_exec][0]
    return -(sla_violation(p99_next_ms, fail_next) + RW["action_cost"][kind]
             + RW["w_replica"] * replica_surplus(spec_next))


def healthy(p99_ms: float, fail: float) -> bool:
    return p99_ms <= L_SLA and fail <= SLA["e_sla"]


# ------------------------------------------------------------------ mask (§5.8)
def compute_mask(spec: dict[str, int], lock_held: bool) -> np.ndarray:
    m = np.zeros(N_ACTIONS, dtype=np.int8)
    m[0] = 1
    if lock_held:
        return m
    for a, (kind, tgt, label) in enumerate(ACTIONS):
        if kind == "NOOP":
            continue
        if kind == "RESTART":
            m[a] = int(spec[tgt] > 0)
        elif kind == "SCALE":
            if label.startswith("SCALE_UP"):
                m[a] = int(spec[tgt] < MAX[tgt])
            else:
                m[a] = int(spec[tgt] > 1)
        elif kind == "RESTORE":
            m[a] = 1  # never masked on drift: that would leak fault identity
    return m


# ------------------------------------------------------- observation (§5.3)
def p99_feature(p99_ms: float) -> float:
    return float(np.clip(math.log2(1.0 + p99_ms / L_SLA) / math.log2(21.0), 0.0, 1.0))


def p99_from_feature(x: float) -> float:
    return L_SLA * (21.0 ** x - 1.0)


def build_obs(raw: dict, prev: dict | None) -> np.ndarray:
    """raw: p99_ms, fail, rps, and per-service dicts; prev: previous (p99f, fail)."""
    o = np.zeros(OBS_DIM, dtype=np.float32)
    p99f = p99_feature(raw["p99_ms"])
    o[0] = p99f
    o[1] = np.clip(raw["fail"], 0.0, 1.0)
    o[2] = np.clip(raw["rps"] / (3.0 * RPS_BASE), 0.0, 1.0)
    o[3] = 0.0 if prev is None else p99f - prev["p99f"]
    o[4] = 0.0 if prev is None else o[1] - prev["fail"]
    for i, d in enumerate(MANAGED):
        s = raw["svc"][d]
        j = 5 + 7 * i
        o[j + 0] = np.clip(s["cpu_util"], 0, 1)
        o[j + 1] = np.clip(s["throttle"], 0, 1)
        o[j + 2] = np.clip(s["mem_util"], 0, 1)
        o[j + 3] = np.clip(s["available"] / BASE[d], 0, 1)
        o[j + 4] = s["spec"] / MAX[d]
        o[j + 5] = min(max(0, s["restart_delta"]), 3) / 3.0
        o[j + 6] = 0.0 if s["ticks_since_gen"] is None else math.exp(-s["ticks_since_gen"] / 5.0)
    o[33] = float(raw["in_flight"])
    o[34] = 1.0 if raw["ticks_since_action"] is None else min(raw["ticks_since_action"], 10) / 10.0
    o[35] = float(raw["stale"])
    if not np.all(np.isfinite(o)):
        raise ValueError("non-finite observation")
    return o


def decode_obs(o: np.ndarray) -> dict:
    """Denormalise an observation (used by the runbook; observation-only)."""
    out = {
        "p99_ms": p99_from_feature(float(o[0])), "fail": float(o[1]),
        "rps": float(o[2]) * 3.0 * RPS_BASE, "in_flight": bool(o[33] > 0.5), "svc": {},
    }
    for i, d in enumerate(MANAGED):
        j = 5 + 7 * i
        rec = float(o[j + 6])
        out["svc"][d] = {
            "throttle": float(o[j + 1]),
            "spec": int(round(float(o[j + 4]) * MAX[d])),
            "ticks_since_gen": None if rec <= 0 else -5.0 * math.log(rec),
        }
    return out
