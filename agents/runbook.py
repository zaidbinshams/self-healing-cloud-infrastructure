"""Scripted SRE runbook (CLAUDE.md §5.11). Observation-only, stateful, mask-respecting.

Inputs are the env observation (de-normalized here with the contract and calibration) and the
runbook's own memory of its past actions. It never reads `info`, so it never sees the fault
identity, target, severity or injection time (§4.1.4). Rules are evaluated in order every tick;
the first match fires. All thresholds come from `contract.runbook`.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from env.contract import FEATURES_PER_SERVICE, Calibration, Contract
from env.k8s_actions import CATALOG, RESTART, RESTORE, SCALE
from env.telemetry import (
    IDX_IN_FLIGHT,
    P99_LOG_BASE_RATIO,
    ROLLOUT_RECENCY_TICKS,
    RPS_SCALE,
    SERVICE_OFFSET,
)

F_THROTTLE, F_REPLICAS, F_RECENCY = 1, 4, 6          # per-service feature offsets (§5.3)
FRONTEND = "frontend"
GEN_MATCH_TOLERANCE_TICKS = 1                         # recency is quantized by exp(-k/5) in float32


@dataclass(frozen=True)
class View:
    """The runbook's de-normalized reading of one observation."""
    healthy: bool
    rps: float
    in_flight: bool
    throttle: dict[str, float]
    spec: dict[str, int]
    ticks_since_gen_change: dict[str, int | None]


def read_obs(obs: np.ndarray, contract: Contract, cal: Calibration) -> View:
    p99_at_sla = math.log2(2.0) / math.log2(P99_LOG_BASE_RATIO)     # obs[0] when P99 == L_SLA
    healthy = float(obs[0]) <= p99_at_sla + 1e-6 and float(obs[1]) <= contract.sla.e_sla + 1e-9
    rps = float(obs[2]) * RPS_SCALE * cal.rps_base
    thr, spec, since = {}, {}, {}
    for i, d in enumerate(contract.cluster.managed):
        base = SERVICE_OFFSET + FEATURES_PER_SERVICE * i
        thr[d] = float(obs[base + F_THROTTLE])
        spec[d] = round(float(obs[base + F_REPLICAS]) * contract.replicas.max[d])
        rec = float(obs[base + F_RECENCY])
        since[d] = None if rec <= 0.0 else round(-ROLLOUT_RECENCY_TICKS * math.log(rec))
    return View(healthy, rps, float(obs[IDX_IN_FLIGHT]) >= 0.5, thr, spec, since)


def _action_id(kind: str, target: str, delta: int = 0) -> int | None:
    for a in CATALOG:
        if a.kind == kind and a.target == target and (kind != SCALE or a.delta == delta):
            return a.id
    return None


@dataclass
class Runbook:
    contract: Contract
    cal: Calibration
    epsilon: float = 0.0
    rng: random.Random = field(default_factory=random.Random)
    tick: int = 0
    health: deque = field(default_factory=deque)                 # recent H flags, newest last
    throttle_hist: dict[str, deque] = field(default_factory=dict)
    my_actions: list[tuple[int, int]] = field(default_factory=list)   # (tick, action id)
    last_action_tick: int | None = None
    unhealthy_since_action: int = 0

    def reset(self) -> None:
        self.tick, self.last_action_tick, self.unhealthy_since_action = 0, None, 0
        self.health.clear()
        self.throttle_hist.clear()
        self.my_actions.clear()

    # --- memory helpers -----------------------------------------------------------

    def _caused_by_me(self, d: str, ticks_ago: int) -> bool:
        for t, aid in self.my_actions:
            a = CATALOG[aid]
            if a.target == d and abs((self.tick - t) - ticks_ago) <= GEN_MATCH_TOLERANCE_TICKS:
                return True
        return False

    def _restarted_recently(self, d: str) -> bool:
        cooldown = self.contract.runbook.restart_cooldown_ticks
        return any(CATALOG[aid].kind == RESTART and CATALOG[aid].target == d and self.tick - t <= cooldown
                   for t, aid in self.my_actions)

    # --- policy -------------------------------------------------------------------

    def decide(self, obs: np.ndarray, mask: np.ndarray) -> tuple[int, str]:
        """Pure rule evaluation on the current memory (no memory update). Returns (action, rule)."""
        rb, c = self.contract.runbook, self.contract
        v = read_obs(obs, c, self.cal)
        h = list(self.health) + [v.healthy]
        n_debounce = rb.debounce_ticks

        def unhealthy(n: int) -> bool:
            return len(h) >= n and not any(h[-n:])

        def ok(aid: int | None) -> bool:
            return aid is not None and bool(mask[aid])

        if v.in_flight:
            return 0, "R0"
        for d in c.cluster.managed:                                               # R1
            aid = _action_id(RESTORE, d)
            if v.spec[d] < c.replicas.base[d] and ok(aid):
                return int(aid), "R1"
        if unhealthy(n_debounce):                                                  # R2
            changed = [(since, d) for d, since in v.ticks_since_gen_change.items()
                       if since is not None and since <= rb.change_window_ticks
                       and not self._caused_by_me(d, since) and ok(_action_id(RESTORE, d))]
            if changed:
                _, d = min(changed)
                return int(_action_id(RESTORE, d)), "R2"
        surge = v.rps >= rb.surge_rps_factor * self.cal.rps_base
        if unhealthy(n_debounce) and surge and v.throttle[FRONTEND] >= rb.throttle_threshold \
                and v.spec[FRONTEND] < c.replicas.max[FRONTEND]:                     # R3
            aid = _action_id(SCALE, FRONTEND, +1)
            if ok(aid):
                return int(aid), "R3"
        if unhealthy(n_debounce) and not surge:                                    # R4
            hot = []
            for d in c.cluster.managed:
                hist = list(self.throttle_hist.get(d, [])) + [v.throttle[d]]
                if len(hist) >= n_debounce and all(x >= rb.throttle_threshold for x in hist[-n_debounce:]) \
                        and not self._restarted_recently(d) and ok(_action_id(RESTART, d)):
                    hot.append((v.throttle[d], d))
            if hot:
                _, d = max(hot)
                return int(_action_id(RESTART, d)), "R4"
        healthy_long = len(h) >= rb.scaleback_healthy_ticks and all(h[-rb.scaleback_healthy_ticks:])
        if healthy_long and v.rps < rb.scaleback_rps_factor * self.cal.rps_base:      # R5
            down = _action_id(SCALE, FRONTEND, -1)
            if v.spec[FRONTEND] > c.replicas.base[FRONTEND] and ok(down):
                return int(down), "R5"
            for d in c.cluster.managed:
                aid = _action_id(RESTORE, d)
                if v.spec[d] > c.replicas.base[d] and ok(aid):
                    return int(aid), "R5"
        return 0, "R6"

    def act(self, obs: np.ndarray, mask: np.ndarray) -> tuple[int, str, str]:
        """Choose an action, update memory. Returns (action, rule, source ∈ {runbook, eps})."""
        rb = self.contract.runbook
        v = read_obs(obs, self.contract, self.cal)
        aid, rule = self.decide(obs, mask)
        source = "runbook"
        if self.epsilon > 0 and self.rng.random() < self.epsilon:
            valid = [i for i in range(len(mask)) if mask[i]]
            aid, rule, source = self.rng.choice(valid), "eps", "eps"
        # memory update (after deciding on this tick's observation)
        self.health.append(v.healthy)
        while len(self.health) > max(rb.scaleback_healthy_ticks, rb.change_window_ticks):
            self.health.popleft()
        for d, x in v.throttle.items():
            q = self.throttle_hist.setdefault(d, deque(maxlen=rb.debounce_ticks))
            q.append(x)
        self.unhealthy_since_action = 0 if aid != 0 else self.unhealthy_since_action + (0 if v.healthy else 1)
        if rule == "R6" and self.unhealthy_since_action >= rb.scaleback_healthy_ticks:
            rule = "R6:unknown_incident"
        if aid != 0:
            self.my_actions.append((self.tick, aid))
            self.last_action_tick = self.tick
        self.tick += 1
        return aid, rule, source
