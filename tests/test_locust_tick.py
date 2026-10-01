"""Pure-function tests for the /tick window aggregation in locust/locustfile.py (hand-written data)."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "boutique_locustfile", Path(__file__).resolve().parent.parent / "locust" / "locustfile.py")
lf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lf)


def test_window_is_open_closed():
    log = lf.RequestLog(retention_s=100.0)
    for t in (10.0, 11.0, 12.0):
        log.record(t, 1.0, True)
    assert [e[0] for e in log.window(10.0, 12.0)] == [11.0, 12.0]


def test_prune_tracks_horizon():
    log = lf.RequestLog(retention_s=5.0)
    for t in range(10):
        log.record(float(t), 1.0, True)
    assert log.pruned_through == 4.0
    assert [e[0] for e in log.entries] == [5.0, 6.0, 7.0, 8.0, 9.0]


def test_aggregate_nearest_rank():
    window = [(float(i), float(i + 1), i != 0) for i in range(100)]   # latencies 1..100 ms, 1 failure
    agg = lf.aggregate(window, duration_s=20.0)
    assert agg == {"n": 100, "failures": 1, "p50_ms": 50.0, "p99_ms": 99.0, "rps": 5.0}


def test_aggregate_empty_is_finite():
    agg = lf.aggregate([], duration_s=20.0)
    assert agg["n"] == 0 and agg["p99_ms"] == 0.0 and agg["rps"] == 0.0


@pytest.mark.parametrize("exc", [TimeoutError("t"), lf.gevent.Timeout()])
def test_timeouts_detected(exc):
    assert lf._is_timeout(exc)
