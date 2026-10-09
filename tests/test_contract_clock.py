"""Contract loading/validation and TickClock scheduling (pure; hand-written inputs)."""

from __future__ import annotations

import copy

import pytest
import yaml

from env.clock import TickClock
from env.contract import CONTRACT_PATH, ContractError, load_contract


def write_contract(tmp_path, mutate):
    raw = yaml.safe_load(CONTRACT_PATH.read_text())
    mutate(raw)
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_locked_contract_loads(contract):
    assert contract.cluster.managed == ("frontend", "cartservice", "currencyservice", "productcatalogservice")
    assert dict(contract.episode.fault_probs)["NULL"] == pytest.approx(0.12)   # quoted "NULL" key, not YAML null
    assert contract.telemetry.locf_max_ticks == 2 and len(contract.sha256) == 64


@pytest.mark.parametrize("mutate", [
    lambda r: r["clock"].pop("tick_s"),                                  # missing key
    lambda r: r["clock"].update(extra=1),                                # unknown key
    lambda r: r["telemetry"].update(locf_max_ticks="2"),                 # wrong type
    lambda r: r["replicas"]["max"].update(frontend=0),                   # base > max
    lambda r: r["reward"].update(replica_denominator=5),                 # != sum(max - base)
    lambda r: r["episode"]["fault_probs"].update(F1=0.5),                # probs do not sum to 1
    lambda r: r["clock"].update(collect_deadline_s=25),                  # deadline >= tick
    lambda r: r["episode"]["fault_probs"].update({None: r["episode"]["fault_probs"].pop("NULL")}),  # bare NULL key
    lambda r: r["golden_overrides"].update(adservice={"cpu_limit": "500m"}),  # not managed nor allow-listed
    lambda r: r["golden_overrides"].update(cartservice={"image": "cart:latest"}),  # image not digest-pinned
    lambda r: r["golden_overrides"].update(productcatalogservice={"env.EXTRA_LATENCY": "1s"}),  # fault var
    lambda r: r["golden_overrides"].update(cartservice={"env.bad-name": "1"}),  # invalid env name
])
def test_invalid_contract_rejected(tmp_path, mutate):
    with pytest.raises(ContractError):
        load_contract(write_contract(tmp_path, lambda r: mutate(r)))


def test_contract_file_untouched_by_validation(tmp_path):
    before = copy.deepcopy(CONTRACT_PATH.read_text())
    load_contract()
    assert CONTRACT_PATH.read_text() == before


class FakeTime:
    def __init__(self, mono: float = 100.0, wall: float = 1_000_000.0) -> None:
        self.m, self.w = mono, wall

    def mono(self) -> float:
        return self.m

    def wall(self) -> float:
        return self.w

    def sleep(self, s: float) -> None:
        self.m += s + 0.002      # oversleep slightly, like a real scheduler
        self.w += s + 0.002


def test_boundaries_are_absolute_and_record_wall():
    ft = FakeTime()
    clock = TickClock(20.0, 0.5, ft.mono, ft.wall, ft.sleep)
    b0 = clock.anchor()
    assert b0.k == 0 and clock.wall(0) == 1_000_000.0
    for k in (1, 2, 3):
        b = clock.wait_boundary(k)
        assert b.target_mono_s == 100.0 + 20.0 * k                       # oversleep never accumulates
        assert 0 <= b.overrun_s < 0.01
        assert clock.wall(k) == pytest.approx(1_000_000.0 + 20.0 * k, abs=0.01)


def test_late_missed_and_realign():
    ft = FakeTime()
    clock = TickClock(20.0, 0.5, ft.mono, ft.wall, ft.sleep)
    clock.anchor()
    assert not clock.is_late(100.0 + 9.9, 0) and clock.is_late(100.0 + 10.1, 0)
    assert not clock.missed(100.0 + 19.9, 0) and clock.missed(100.0 + 20.0, 0)
    assert clock.next_boundary_index(100.0 + 45.0) == 3


def test_sleep_until_past_target_does_not_sleep():
    ft = FakeTime()
    clock = TickClock(20.0, 0.5, ft.mono, ft.wall, ft.sleep)
    assert clock.sleep_until(50.0) == 100.0


def test_allow_listed_unmanaged_override_accepted(tmp_path):
    path = write_contract(tmp_path, lambda r: r["golden_overrides"].update(recommendationservice={"cpu_limit": "500m"}))
    assert dict(load_contract(path).golden_overrides)["recommendationservice"]["cpu_limit"] == "500m"
