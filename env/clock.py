"""TickClock: the fixed wall-clock tick schedule on Machine B (CLAUDE.md §2, §5.7).

Boundaries are absolute: T_k = T_0 + k * tick_s on `time.monotonic()`, so lateness never
accumulates. wall(T_k) is `time.time()` read when boundary k is reached; it defines the Locust
window (wall(T_k), wall(T_{k+1})] and the Prometheus evaluation time. `sleep_until` is the only
sleep allowed inside `step()` (§4.3.6). Time sources are injectable for unit tests.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Boundary:
    k: int
    target_mono_s: float     # T_k
    reached_mono_s: float    # monotonic time when the sleep returned
    wall_s: float            # wall(T_k), B's time.time()

    @property
    def overrun_s(self) -> float:
        """How late the boundary was reached (scheduler jitter); >= 0."""
        return max(0.0, self.reached_mono_s - self.target_mono_s)


class ClockNotAnchoredError(RuntimeError):
    pass


class TickClock:
    def __init__(self, tick_s: float, late_frac: float,
                 mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.tick_s = tick_s
        self.late_frac = late_frac
        self._mono, self._wall, self._sleep = mono, wall, sleep
        self._t0: float | None = None
        self._walls: dict[int, float] = {}

    # --- schedule ---------------------------------------------------------------

    def anchor(self) -> Boundary:
        """Re-anchor T_0 at now (called by reset()); boundary 0 is reached immediately."""
        now = self._mono()
        self._t0 = now
        self._walls = {0: self._wall()}
        return Boundary(0, now, now, self._walls[0])

    @property
    def t0(self) -> float:
        if self._t0 is None:
            raise ClockNotAnchoredError("TickClock.anchor() has not been called")
        return self._t0

    def boundary(self, k: int) -> float:
        """T_k on the monotonic clock."""
        return self.t0 + k * self.tick_s

    def wall(self, k: int) -> float:
        """wall(T_k), recorded when boundary k was reached."""
        return self._walls[k]

    def now(self) -> float:
        return self._mono()

    def wall_now(self) -> float:
        return self._wall()

    # --- waiting ----------------------------------------------------------------

    def sleep_until(self, target_mono_s: float) -> float:
        """Sleep until `target_mono_s` (no-op if already past). Returns the monotonic time on return."""
        while True:
            remaining = target_mono_s - self._mono()
            if remaining <= 0:
                return self._mono()
            self._sleep(remaining)

    def wait_boundary(self, k: int) -> Boundary:
        """Sleep until T_k and record wall(T_k)."""
        target = self.boundary(k)
        reached = self.sleep_until(target)
        self._walls[k] = self._wall()
        return Boundary(k, target, reached, self._walls[k])

    # --- step() timing predicates (§5.7) ------------------------------------------

    def is_late(self, t_enter_mono_s: float, k: int) -> bool:
        """step() entered after T_k + late_frac * tick_s."""
        return t_enter_mono_s > self.boundary(k) + self.late_frac * self.tick_s

    def missed(self, t_enter_mono_s: float, k: int) -> bool:
        """step() for tick k entered at or after T_{k+1}: the tick was missed entirely."""
        return t_enter_mono_s >= self.boundary(k + 1)

    def next_boundary_index(self, t_mono_s: float) -> int:
        """Smallest k with T_k > t (used to realign after a missed tick)."""
        return math.floor((t_mono_s - self.t0) / self.tick_s) + 1
