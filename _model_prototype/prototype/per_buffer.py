"""Replay buffer with optional proportional prioritisation (CLAUDE.md §5.12 rules 6–8).

`prioritized=False` gives the uniform ablation through the same code path
(uniform sampling, IS weights = 1).
"""
from __future__ import annotations

import numpy as np

from .contract import CONTRACT, N_ACTIONS, OBS_DIM

PER = CONTRACT["per"]


class ReplayBuffer:
    def __init__(self, capacity: int, prioritized: bool, rng: np.random.Generator):
        self.cap, self.prioritized, self.rng = capacity, prioritized, rng
        self.s = np.zeros((capacity, OBS_DIM), np.float32)
        self.m = np.zeros((capacity, N_ACTIONS), np.bool_)
        self.a = np.zeros(capacity, np.int64)
        self.R = np.zeros(capacity, np.float32)      # n-step discounted return
        self.s2 = np.zeros((capacity, OBS_DIM), np.float32)
        self.m2 = np.zeros((capacity, N_ACTIONS), np.bool_)
        self.term = np.zeros(capacity, np.float32)   # 1 if terminated within the n steps
        self.gn = np.zeros(capacity, np.float32)     # gamma ** n_actual
        self.src = np.zeros(capacity, np.int8)       # 0 online, 1 runbook, 2 eps
        self.prio = np.zeros(capacity, np.float64)   # stores p_i (before ** alpha)
        self.max_prio = 1.0 + PER["eps"]
        self.size = self.ptr = 0

    def add(self, s, m, a, R, s2, m2, term, gn, src=0):
        i = self.ptr
        self.s[i], self.m[i], self.a[i], self.R[i] = s, m, a, R
        self.s2[i], self.m2[i], self.term[i], self.gn[i], self.src[i] = s2, m2, term, gn, src
        self.prio[i] = self.max_prio          # new transitions get current max priority
        self.ptr = (self.ptr + 1) % self.cap
        self.size = min(self.size + 1, self.cap)

    def beta(self, grad_step: int) -> float:
        f = min(1.0, grad_step / PER["beta_anneal_grad_steps"])
        return PER["beta_start"] + f * (PER["beta_end"] - PER["beta_start"])

    def sample(self, batch: int, grad_step: int):
        n = self.size
        if self.prioritized:
            pa = self.prio[:n] ** PER["alpha"]
            P = pa / pa.sum()
            idx = self.rng.choice(n, size=batch, p=P)
            w = (n * P[idx]) ** (-self.beta(grad_step))
            w = w / w.max()                    # normalised by batch max
        else:
            idx = self.rng.integers(0, n, size=batch)
            w = np.ones(batch)
        return idx, w.astype(np.float32), {
            "s": self.s[idx], "m": self.m[idx], "a": self.a[idx], "R": self.R[idx],
            "s2": self.s2[idx], "m2": self.m2[idx], "term": self.term[idx], "gn": self.gn[idx],
        }

    def update_priorities(self, idx: np.ndarray, td_mean_abs: np.ndarray):
        p = np.minimum(td_mean_abs, PER["priority_cap"]) + PER["eps"]
        self.prio[idx] = p
        self.max_prio = max(self.max_prio, float(p.max()))


def nstep_transitions(ep: list[dict], gamma: float, n: int):
    """Turn one episode's 1-step records into n-step transitions.

    Each record: s, m, a, r, s2, m2, terminated, stale2 (stale flag of s2), src.
    Transitions whose s' is stale are skipped (CLAUDE.md §5.5). The bootstrap
    is cut only by `terminated` (truncation still bootstraps).
    """
    out = []
    T = len(ep)
    for t in range(T):
        if ep[t]["stale2"]:
            continue
        R, g, term, k = 0.0, 1.0, 0.0, t
        for k in range(t, min(t + n, T)):
            R += g * ep[k]["r"]
            g *= gamma
            if ep[k]["terminated"]:
                term = 1.0
                break
        out.append((ep[t]["s"], ep[t]["m"], ep[t]["a"], R, ep[k]["s2"], ep[k]["m2"], term, g,
                    ep[t].get("src", 0)))
    return out
