"""Scripted SRE runbook, rules R0–R6 (CLAUDE.md §5.11).

Observation-only: it decodes the normalised observation and keeps memory of
its own past actions. It never reads `info`.
"""
from __future__ import annotations

import numpy as np

from .contract import CONTRACT, MANAGED
from .core import BASE, MAX, RPS_BASE, decode_obs, healthy

RB = CONTRACT["runbook"]
RESTART_ID = {"frontend": 1, "cartservice": 2, "currencyservice": 3, "productcatalogservice": 4}
RESTORE_ID = {"productcatalogservice": 9, "cartservice": 10, "currencyservice": 11}


class Runbook:
    def __init__(self, epsilon: float = 0.0, rng: np.random.Generator | None = None):
        self.eps, self.rng = epsilon, rng or np.random.default_rng(0)
        self.reset()

    def reset(self):
        self.k = 0
        self.health: list[bool] = []
        self.thr_hist: dict[str, list[float]] = {d: [] for d in MANAGED}
        self.caused: dict[str, int] = {}      # target -> tick of runbook's own change
        self.restarted: dict[str, int] = {}
        self.last_action_k = None
        self.last_source = "runbook"

    def _unhealthy(self, n: int) -> bool:
        return len(self.health) >= n and not any(self.health[-n:])

    def _healthy_for(self, n: int) -> bool:
        return len(self.health) >= n and all(self.health[-n:])

    def rule_action(self, o: dict) -> int:
        th = RB["throttle_threshold"]
        svc = o["svc"]
        if o["in_flight"]:                                                   # R0
            return 0
        for d in MANAGED:                                                    # R1
            if svc[d]["spec"] < BASE[d] and d in RESTORE_ID:
                return RESTORE_ID[d]
        if self._unhealthy(RB["debounce_ticks"]):                            # R2
            cands = []
            for d, rid in RESTORE_ID.items():
                tsg = svc[d]["ticks_since_gen"]
                if tsg is None or tsg > RB["change_window_ticks"]:
                    continue
                own = self.caused.get(d)
                change_k = self.k - round(tsg)
                if own is not None and abs(own - change_k) <= 1:
                    continue
                cands.append((tsg, rid))
            if cands:
                return min(cands)[1]
        surge = o["rps"] >= RB["surge_rps_factor"] * RPS_BASE
        if (self._unhealthy(RB["debounce_ticks"]) and surge                  # R3
                and svc["frontend"]["throttle"] >= th and svc["frontend"]["spec"] < MAX["frontend"]):
            return 5
        if self._unhealthy(RB["debounce_ticks"]) and not surge:              # R4
            hot = [(svc[d]["throttle"], d) for d in MANAGED
                   if len(self.thr_hist[d]) >= 2 and min(self.thr_hist[d][-2:]) >= th
                   and svc[d]["spec"] > 0
                   and (d not in self.restarted
                        or self.k - self.restarted[d] > RB["restart_cooldown_ticks"])]
            if hot:
                return RESTART_ID[max(hot)[1]]
        if (self._healthy_for(RB["scaleback_healthy_ticks"])                  # R5
                and o["rps"] < RB["scaleback_rps_factor"] * RPS_BASE):
            if svc["frontend"]["spec"] > 1:
                return 6
            for d, rid in RESTORE_ID.items():
                if svc[d]["spec"] > BASE[d]:
                    return rid
        return 0                                                             # R6

    def act(self, obs: np.ndarray, mask: np.ndarray) -> int:
        o = decode_obs(obs)
        self.health.append(healthy(o["p99_ms"], o["fail"]))
        for d in MANAGED:
            self.thr_hist[d].append(o["svc"][d]["throttle"])
        a = self.rule_action(o)
        self.last_source = "runbook"
        if self.eps > 0 and self.rng.random() < self.eps:
            a = int(self.rng.choice(np.flatnonzero(mask)))
            self.last_source = "eps"
        if not mask[a]:
            a = 0
        if a != 0:
            from .contract import ACTIONS
            kind, tgt, _ = ACTIONS[a]
            self.caused[tgt] = self.k
            if kind == "RESTART":
                self.restarted[tgt] = self.k
            self.last_action_k = self.k
        self.k += 1
        return a
