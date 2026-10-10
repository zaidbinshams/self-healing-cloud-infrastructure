"""fetch_prom over a keep-alive session (2026-10-10): pure transport checks with a tiny fake session."""

from __future__ import annotations

from typing import Any

import requests

from env.telemetry import fetch_prom, make_http_session


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.body


class _Session:
    def __init__(self, outcome: Any) -> None:
        self.outcome, self.calls = outcome, []

    def get(self, url: str, **kw: Any) -> _Resp:
        self.calls.append((url, kw))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _Resp(self.outcome)


def test_fetch_prom_uses_the_given_session_and_keeps_managed_rows():
    body = {"status": "success", "data": {"result": [
        {"metric": {"deployment": "frontend"}, "value": [0, "0.25"]},
        {"metric": {"deployment": "adservice"}, "value": [0, "0.9"]}]}}
    s = _Session(body)
    r = fetch_prom("http://prom:30090/", "cpu", "q", 123.0, (1.0, 2.5), ("frontend",), s)  # type: ignore[arg-type]
    assert r.ok and r.values == {"frontend": 0.25}
    url, kw = s.calls[0]
    assert url == "http://prom:30090/api/v1/query" and kw["timeout"] == (1.0, 2.5)
    assert kw["params"] == {"query": "q", "time": "123.000"}


def test_fetch_prom_connect_timeout_is_a_typed_failure():
    s = _Session(requests.exceptions.ConnectTimeout("syn lost"))
    r = fetch_prom("http://prom:30090", "thr", "q", 1.0, (1.0, 2.5), ("frontend",), s)  # type: ignore[arg-type]
    assert not r.ok and r.error_type == "ConnectTimeout"


def test_session_has_library_retries_disabled():
    s = make_http_session(6)
    adapter = s.get_adapter("http://prom:30090")
    assert adapter.max_retries.total == 0 and adapter._pool_maxsize == 6   # type: ignore[attr-defined]
    s.close()
