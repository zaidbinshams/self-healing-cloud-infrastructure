"""Shared fixtures: recorded real ticks (scripts/record_ticks.py) and the locked contract."""

from __future__ import annotations

# Locust (imported by test_locust_tick.py) gevent-monkey-patches ssl on import, which recurses if
# requests/urllib3 imported ssl first. Patch once, before anything else imports ssl.
from gevent import monkey

monkey.patch_all()

import json
from pathlib import Path
from typing import Any

import pytest

from env.contract import Contract, Limits, load_contract, load_limits
from env.telemetry import RawTick, raw_tick_from_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TICKS_STEADY = FIXTURES / "ticks_steady.jsonl"


def load_ticks(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    header = lines[0]
    assert header["kind"] == "header", f"{path}: first line must be the recording header"
    return header, [rec for rec in lines[1:] if rec["kind"] == "tick"]


@pytest.fixture(scope="session")
def contract() -> Contract:
    return load_contract()


@pytest.fixture(scope="session")
def limits(contract: Contract) -> dict[str, Limits]:
    return load_limits(contract)


@pytest.fixture(scope="session")
def steady() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return load_ticks(TICKS_STEADY)


@pytest.fixture(scope="session")
def steady_raw(steady: tuple[dict[str, Any], list[dict[str, Any]]]) -> list[RawTick]:
    return [raw_tick_from_dict(rec["raw"]) for rec in steady[1]]
