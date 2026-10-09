"""Append-and-flush JSONL logs (CLAUDE.md §6).

`EventLog` writes structured events (one JSON object per line) to
`data/logs/<run>.events.jsonl`. Runtime code never print-debugs; it emits events here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import IO, Any


class EventLog:
    """Structured event log. With `path=None` events are kept in memory only (unit tests)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._fh: IO[str] | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def emit(self, event: str, component: str, **fields: Any) -> dict[str, Any]:
        record = {"t_wall": time.time(), "event": event, "component": component, **fields}
        if self._fh is None:
            self.events.append(record)
        else:
            self._fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            self._fh.flush()
        return record

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


TRANSITION_FIELDS = (
    "run", "episode", "tick", "t_wall", "phase", "fault", "target", "severity", "source",
    "obs", "mask", "a_chosen", "a_exec", "reward", "next_obs", "next_mask",
    "terminated", "truncated", "valid", "stale", "raw", "decision_latency_s", "exec_error", "late",
)


class TransitionRecorder:
    """One JSONL line per tick at data/transitions/<run>/episode_<n>.jsonl, flushed every tick (§6)."""

    def __init__(self, root: Path, run: str, episode: int) -> None:
        self.path = root / run / f"episode_{episode}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] | None = self.path.open("a", encoding="utf-8")

    def append(self, record: dict[str, Any]) -> None:
        missing = [k for k in TRANSITION_FIELDS if k not in record]
        if missing:
            raise ValueError(f"transition record missing fields {missing}")
        if self._fh is None:
            raise ValueError(f"{self.path}: recorder already closed")
        self._fh.write(json.dumps(record, sort_keys=True, default=_jsonable) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _jsonable(obj: Any) -> Any:
    """numpy arrays / scalars -> plain JSON (obs, masks)."""
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")
