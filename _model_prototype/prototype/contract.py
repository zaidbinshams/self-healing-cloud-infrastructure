"""Locked parameters, mirrored from CLAUDE.md §5.1 (config/contract.yaml).

The prototype reads every number from here so the agent code can later be
pointed at the real `config/contract.yaml` without edits.

CALIBRATION values are PLACEHOLDERS: the real `calibration.json` does not
exist yet (G6 is still open). They only parameterise the toy simulator.
"""
from __future__ import annotations

MANAGED = ["frontend", "cartservice", "currencyservice", "productcatalogservice"]

CONTRACT = {
    "clock": {"tick_s": 20, "inflight_timeout_s": 100},
    "telemetry": {"locf_max_ticks": 2, "stale_truncate_ticks": 3},
    "sla": {"e_sla": 0.01, "e_max": 0.20, "recovery_ticks": 3, "l_sla_factor": 1.5},
    "episode": {
        "lead_in_ticks": [2, 5],
        "fault_max_ticks": 18,
        "null_extra_ticks": 6,
        "fault_probs": {"F1": 0.22, "F2": 0.22, "F3": 0.22, "F4": 0.22, "NULL": 0.12},
        "f1_latency": ["300ms", "600ms", "1s"],
        "f2_targets": ["currencyservice", "cartservice"],
        "f2_workers": [1, 2],
        "f3_targets": ["cartservice", "currencyservice"],
        "f4_multiplier": [2.5, 3.0],
    },
    "replicas": {
        "base": {"frontend": 1, "cartservice": 1, "currencyservice": 1, "productcatalogservice": 1},
        "max": {"frontend": 3, "cartservice": 2, "currencyservice": 2, "productcatalogservice": 1},
    },
    "reward": {
        "action_cost": {"NOOP": 0.0, "SCALE": 0.02, "RESTART": 0.04, "RESTORE": 0.04},
        "w_replica": 0.05,
        "replica_denominator": 4,
    },
    "rl": {
        "gamma": 0.93, "n_step": 3, "tau": 0.005, "hidden": [256, 256],
        "lr_actor": 3.0e-4, "lr_critic": 3.0e-4, "lr_alpha": 3.0e-4,
        "batch_size": 128, "buffer_size": 50000, "updates_per_tick": 4,
        "alpha_init": 0.1, "target_entropy_frac": 0.4,
        "offline_warmstart_steps": 2000, "torch_threads": 2,
    },
    "per": {
        "alpha": 0.5, "beta_start": 0.4, "beta_end": 1.0,
        "beta_anneal_grad_steps": 15000, "eps": 1.0e-3, "priority_cap": 1.0,
    },
    "runbook": {
        "throttle_threshold": 0.40, "debounce_ticks": 2, "change_window_ticks": 9,
        "surge_rps_factor": 1.5, "scaleback_rps_factor": 1.2,
        "scaleback_healthy_ticks": 6, "restart_cooldown_ticks": 6,
        "warmstart_epsilon": 0.25, "warmstart_episodes": 120,
    },
}

# PLACEHOLDER calibration (real values come from scripts/calibrate.py)
CALIBRATION = {"l_sla_ms": 450.0, "rps_base": 30.0, "u_base": 40, "placeholder": True}

# Action catalog (CLAUDE.md §5.2): id -> (kind, target, label)
ACTIONS = [
    ("NOOP", None, "NOOP"),
    ("RESTART", "frontend", "RESTART fe"),
    ("RESTART", "cartservice", "RESTART cart"),
    ("RESTART", "currencyservice", "RESTART cur"),
    ("RESTART", "productcatalogservice", "RESTART pc"),
    ("SCALE", "frontend", "SCALE_UP fe"),
    ("SCALE", "frontend", "SCALE_DOWN fe"),
    ("SCALE", "cartservice", "SCALE_UP cart"),
    ("SCALE", "currencyservice", "SCALE_UP cur"),
    ("RESTORE", "productcatalogservice", "RESTORE pc"),
    ("RESTORE", "cartservice", "RESTORE cart"),
    ("RESTORE", "currencyservice", "RESTORE cur"),
]
N_ACTIONS = len(ACTIONS)
OBS_DIM = 36
SCALE_DELTA = {5: +1, 6: -1, 7: +1, 8: +1}
