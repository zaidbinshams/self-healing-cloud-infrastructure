"""n-step transition builder + prioritized replay (CLAUDE.md §5.5 storage rule, §5.12 rules 2, 6-8).

Pure NumPy, no I/O. `NStepBuilder` turns the env's 1-step transitions into n-step ones:

    ret      = Σ_{i<k} γ^i r_{t+i}
    discount = γ^k                       (k = n, or fewer at an episode end or a stale break)
    done     = terminated within the k steps   (the critic bootstraps unless done, §5.6)

A step whose s′ is stale (telemetry_stale = 1) is never stored (§5.5). It also breaks the chain: pending
transitions are closed at the last non-stale state (a shorter but valid k-step target), so no stored
target ever bootstraps from, or is built across, imputed telemetry.

`ReplayBuffer` is a ring buffer with proportional prioritization (Schaul et al., 2016) over a sum-tree:
P(i) = p_i^α / Σ p^α, IS weight w_i = (N·P(i))^-β normalised by the batch max (PLAN.md M4), β annealed
linearly over gradient steps. `prioritized=False` is the uniform ablation: same
code, uniform sampling, weights 1, priority updates ignored (§5.12 rule 8).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from env.contract import N_ACTIONS, OBS_DIM, PerCfg


@dataclass(frozen=True)
class Step:
    """One env transition as recorded (a = executed action, stale = s′ stale)."""
    obs: np.ndarray
    mask: np.ndarray
    action: int
    reward: float
    next_obs: np.ndarray
    next_mask: np.ndarray
    terminated: bool
    truncated: bool
    stale: bool


@dataclass(frozen=True)
class Transition:
    obs: np.ndarray
    mask: np.ndarray
    action: int
    ret: float
    next_obs: np.ndarray
    next_mask: np.ndarray
    done: bool
    discount: float


@dataclass
class _Pending:
    obs: np.ndarray
    mask: np.ndarray
    action: int
    ret: float
    k: int
    next_obs: np.ndarray
    next_mask: np.ndarray
    done: bool


class NStepBuilder:
    """Accumulates one episode's steps; call `reset()` at every episode start."""

    def __init__(self, gamma: float, n: int) -> None:
        if n < 1:
            raise ValueError("n_step must be >= 1")
        self.gamma, self.n = gamma, n
        self.pending: deque[_Pending] = deque()
        self.dropped_stale = 0

    def reset(self) -> None:
        self.pending.clear()

    def _close(self, p: _Pending) -> Transition:
        return Transition(p.obs, p.mask, p.action, p.ret, p.next_obs, p.next_mask, p.done, self.gamma ** p.k)

    def _flush(self) -> list[Transition]:
        out = [self._close(p) for p in self.pending]
        self.pending.clear()
        return out

    def finish(self) -> list[Transition]:
        """Close pending chains of an episode that ended without a terminal/truncated flag (an interrupted
        run's last file); they bootstrap like a truncation."""
        return self._flush()

    def push(self, s: Step) -> list[Transition]:
        """Add one step; return the transitions completed by it (possibly none)."""
        if s.stale:
            self.dropped_stale += 1
            return self._flush()                   # close pending chains at the last non-stale state
        for p in self.pending:
            p.ret += (self.gamma ** p.k) * s.reward
            p.k += 1
            p.next_obs, p.next_mask, p.done = s.next_obs, s.next_mask, s.terminated
        self.pending.append(_Pending(s.obs, s.mask, s.action, s.reward, 1, s.next_obs, s.next_mask, s.terminated))
        if s.terminated or s.truncated:
            return self._flush()
        out = []
        while self.pending and self.pending[0].k >= self.n:
            out.append(self._close(self.pending.popleft()))
        return out


def beta_at(cfg: PerCfg, grad_step: int) -> float:
    """β linear from beta_start to beta_end over gradient steps (§5.12 rule 7)."""
    frac = min(1.0, grad_step / cfg.beta_anneal_grad_steps)
    return cfg.beta_start + frac * (cfg.beta_end - cfg.beta_start)


def priority_from_td(td1: np.ndarray, td2: np.ndarray, cfg: PerCfg) -> np.ndarray:
    """p_i = min(mean(|δ1|, |δ2|), priority_cap) + eps (§5.12 rule 6)."""
    return np.minimum(0.5 * (np.abs(td1) + np.abs(td2)), cfg.priority_cap) + cfg.eps


class SumTree:
    """Binary sum-tree over `capacity` leaves (leaf values = p^α)."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.size = 1
        while self.size < capacity:
            self.size *= 2
        self.tree = np.zeros(2 * self.size, dtype=np.float64)

    @property
    def total(self) -> float:
        return float(self.tree[1])

    def set(self, idx: np.ndarray, values: np.ndarray) -> None:
        for i, v in zip(np.asarray(idx).ravel(), np.asarray(values, dtype=np.float64).ravel(), strict=True):
            j = int(i) + self.size
            self.tree[j] = v
            j //= 2
            while j >= 1:
                self.tree[j] = self.tree[2 * j] + self.tree[2 * j + 1]
                j //= 2

    def find(self, mass: float) -> int:
        """Leaf index whose cumulative-sum interval contains `mass` (0 <= mass < total)."""
        j = 1
        while j < self.size:
            left = self.tree[2 * j]
            if mass < left:
                j = 2 * j
            else:
                mass -= left
                j = 2 * j + 1
        return j - self.size


@dataclass(frozen=True)
class Batch:
    idx: np.ndarray
    obs: np.ndarray
    mask: np.ndarray
    action: np.ndarray
    ret: np.ndarray
    next_obs: np.ndarray
    next_mask: np.ndarray
    done: np.ndarray
    discount: np.ndarray
    weight: np.ndarray


class ReplayBuffer:
    def __init__(self, capacity: int, cfg: PerCfg, *, prioritized: bool = True, seed: int = 0) -> None:
        self.capacity, self.cfg, self.prioritized = capacity, cfg, prioritized
        self.rng = np.random.default_rng(seed)
        self.obs = np.zeros((capacity, OBS_DIM), np.float32)
        self.next_obs = np.zeros((capacity, OBS_DIM), np.float32)
        self.mask = np.zeros((capacity, N_ACTIONS), np.bool_)
        self.next_mask = np.zeros((capacity, N_ACTIONS), np.bool_)
        self.action = np.zeros(capacity, np.int64)
        self.ret = np.zeros(capacity, np.float32)
        self.done = np.zeros(capacity, np.float32)
        self.discount = np.zeros(capacity, np.float32)
        self.priority = np.zeros(capacity, np.float64)     # raw p_i (before ^α)
        self.tree = SumTree(capacity)
        self.next_idx = 0
        self.n = 0

    def __len__(self) -> int:
        return self.n

    def max_priority(self) -> float:
        """Current max priority (new transitions get it, §5.12 rule 6); cap + eps when empty."""
        return float(self.priority[: self.n].max()) if self.n else self.cfg.priority_cap + self.cfg.eps

    def add(self, t: Transition) -> None:
        i = self.next_idx
        p = self.max_priority()
        self.obs[i], self.mask[i], self.action[i] = t.obs, t.mask.astype(np.bool_), t.action
        self.ret[i], self.done[i], self.discount[i] = t.ret, float(t.done), t.discount
        self.next_obs[i], self.next_mask[i] = t.next_obs, t.next_mask.astype(np.bool_)
        self.priority[i] = p
        self.tree.set(np.array([i]), np.array([p ** self.cfg.alpha]))
        self.next_idx = (i + 1) % self.capacity
        self.n = min(self.n + 1, self.capacity)

    def reset_priorities_to_max(self) -> None:
        """Resume rule (§5.12 rule 10): every stored transition gets the cap + eps priority."""
        p = self.cfg.priority_cap + self.cfg.eps
        idx = np.arange(self.n)
        self.priority[idx] = p
        self.tree.set(idx, np.full(self.n, p ** self.cfg.alpha))

    def sample(self, batch_size: int, beta: float) -> Batch:
        if self.n == 0:
            raise ValueError("sample from an empty buffer")
        if not self.prioritized:
            idx = self.rng.integers(0, self.n, size=batch_size)
            weight = np.ones(batch_size, np.float32)
        else:
            total = self.tree.total
            seg = total / batch_size
            mass = (np.arange(batch_size) + self.rng.random(batch_size)) * seg     # stratified
            idx = np.array([min(self.tree.find(min(m, np.nextafter(total, 0))), self.n - 1) for m in mass])
            prob = self.priority[idx] ** self.cfg.alpha / total
            w = (self.n * prob) ** -beta
            weight = (w / w.max()).astype(np.float32)
        return Batch(idx, self.obs[idx], self.mask[idx], self.action[idx], self.ret[idx], self.next_obs[idx],
                     self.next_mask[idx], self.done[idx], self.discount[idx], weight)

    def update_priorities(self, idx: np.ndarray, priorities: np.ndarray) -> None:
        if not self.prioritized:
            return
        self.priority[idx] = priorities
        self.tree.set(idx, priorities ** self.cfg.alpha)
