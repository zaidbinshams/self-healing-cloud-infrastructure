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
