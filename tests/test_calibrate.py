"""Pure calibration math (scripts/calibrate.py): L_SLA rounding, U_base search step, G6 gate."""

from __future__ import annotations

import pytest

from scripts.calibrate import g6, l_sla_from, next_users, quantile_nearest_rank


def test_l_sla_is_factor_times_q95_ceiled_to_10ms():
    p99s = [100.0] * 19 + [301.0]                       # q95 (nearest rank) of 20 values = 19th = 100
    assert quantile_nearest_rank(p99s, 0.95) == 100.0
    assert l_sla_from(p99s, 1.5) == 150.0
    assert l_sla_from([101.0] * 20, 1.5) == 160.0       # 151.5 -> ceil to 160


@pytest.mark.parametrize(("users", "util", "want"), [(40, 0.70, 31), (40, 0.40, 55), (40, 0.55, 39)])
def test_next_users_moves_toward_band_centre(users, util, want):
    assert next_users(users, util, (0.50, 0.60)) == want


def test_next_users_always_moves():
    assert next_users(10, 0.49, (0.50, 0.60)) == 11     # proportional rounds to 11
    assert next_users(1, 0.9, (0.50, 0.60)) == 1        # never below 1


def test_g6_gate():
    stable = [100.0, 110.0, 95.0, 105.0, 100.0]
    assert g6(stable, [0.0] * 5, 200.0, 0.01)["pass"]
    jittery = [100.0, 600.0, 90.0, 500.0, 100.0]
    r = g6(jittery, [0.0] * 5, 1000.0, 0.01)
    assert not r["pass"] and r["cv"] > 0.25
    assert g6(stable, [0.0, 0.0, 0.0, 0.0, 0.5], 200.0, 0.01)["breach_share"] == pytest.approx(0.2)


def test_g6_override_threshold():
    from scripts.calibrate import G6_MAX_CV
    assert G6_MAX_CV == 0.30                              # human-approved [override], documented gate 0.25
