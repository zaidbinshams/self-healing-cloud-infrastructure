"""Load and validate config/contract.yaml and config/calibration.json (CLAUDE.md §5.1, §7).

Every entry point calls `load_contract()` on startup. It checks the schema (exact key sets, value
types) and the cross-field invariants, and returns frozen dataclasses. Entry points that use
calibrated values also call `load_calibration()`, which refuses a missing file or one calibrated
against an `env/` older than the latest commit touching `env/`. Exempt from the calibration
requirement: `scripts/calibrate.py`, and `scripts/record_ticks.py`, which records raw telemetry
only (human-approved 2026-10-02, CLAUDE.md §7).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import subprocess
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "config" / "contract.yaml"
CALIBRATION_PATH = REPO_ROOT / "config" / "calibration.json"
GOLDEN_LIVE_PATH = REPO_ROOT / "config" / "golden_live.json"
ENV_DIR = "env"

N_ACTIONS = 12            # CLAUDE.md §5.2 action catalog
OBS_DIM = 36              # CLAUDE.md §5.3
FEATURES_PER_SERVICE = 7
FAULT_KINDS = ("F1", "F2", "F3", "F4", "NULL")
ACTION_KINDS = ("NOOP", "SCALE", "RESTART", "RESTORE")
PROB_SUM_TOL = 1e-9
# Non-managed deployments whose resources golden_overrides may set (human-approved per entry).
# recommendationservice: 57% CFS-throttled at the 50-user baseline, the G6 tail-latency
# suspect (2026-10-02). The agent still neither observes nor acts on it.
UNMANAGED_OVERRIDABLE = ("recommendationservice",)


class ContractError(ValueError):
    """contract.yaml / calibration.json is missing, malformed, or violates an invariant."""


# ----------------------------------------------------------------------------- schema

@dataclass(frozen=True)
class ClusterCfg:
    namespace: str
    managed: tuple[str, ...]


@dataclass(frozen=True)
class ClockCfg:
    tick_s: float
    collect_deadline_s: float
    update_budget_s: float
    inflight_timeout_s: float
    late_frac: float


@dataclass(frozen=True)
class TelemetryCfg:
    scrape_interval_s: float
    rate_window: str
    prom_timeout_s: tuple[float, float]
    k8s_timeout_s: tuple[float, float]
    locust_timeout_s: tuple[float, float]
    locf_max_ticks: int
    stale_truncate_ticks: int


@dataclass(frozen=True)
class LocustCfg:
    wait_s: tuple[float, float]
    request_timeout_s: float
    cpu_cores: tuple[int, ...]


@dataclass(frozen=True)
class SlaCfg:
    e_sla: float
    e_max: float
    recovery_ticks: int
    l_sla_factor: float


@dataclass(frozen=True)
class EpisodeCfg:
    lead_in_ticks: tuple[int, int]
    fault_max_ticks: int
    null_extra_ticks: int
    fault_probs: dict[str, float]
    f1_latency: tuple[str, ...]
    f2_targets: tuple[str, ...]
    f2_workers: tuple[int, ...]
    f3_targets: tuple[str, ...]
    f4_multiplier: tuple[float, ...]


@dataclass(frozen=True)
class ReplicasCfg:
    base: dict[str, int]
    max: dict[str, int]


@dataclass(frozen=True)
class RewardCfg:
    action_cost: dict[str, float]
    w_replica: float
    replica_denominator: int


@dataclass(frozen=True)
class RlCfg:
    gamma: float
    n_step: int
    tau: float
    hidden: tuple[int, ...]
    lr_actor: float
    lr_critic: float
    lr_alpha: float
    batch_size: int
    buffer_size: int
    updates_per_tick: int
    alpha_init: float
    target_entropy_frac: float
    offline_warmstart_steps: int
    torch_threads: int


@dataclass(frozen=True)
class PerCfg:
    alpha: float
    beta_start: float
    beta_end: float
    beta_anneal_grad_steps: int
    eps: float
    priority_cap: float


@dataclass(frozen=True)
class RunbookCfg:
    throttle_threshold: float
    debounce_ticks: int
    change_window_ticks: int
    surge_rps_factor: float
    scaleback_rps_factor: float
    scaleback_healthy_ticks: int
    restart_cooldown_ticks: int
    warmstart_epsilon: float
    warmstart_episodes: int


@dataclass(frozen=True)
class Contract:
    cluster: ClusterCfg
    clock: ClockCfg
    telemetry: TelemetryCfg
    locust: LocustCfg
    sla: SlaCfg
    episode: EpisodeCfg
    replicas: ReplicasCfg
    golden_overrides: dict[str, dict[str, str]]
    reward: RewardCfg
    rl: RlCfg
    per: PerCfg
    runbook: RunbookCfg
    sha256: str = dataclasses.field(default="", compare=False)


@dataclass(frozen=True)
class Calibration:
    l_sla_ms: float
    rps_base: float
    u_base: int
    calibrated_at: str
    env_git_sha: str


# ----------------------------------------------------------------------------- typed builder

def _coerce(value: Any, tp: Any, path: str) -> Any:
    """Check `value` against the annotation `tp`; return it in its frozen form."""
    origin = typing.get_origin(tp)
    if dataclasses.is_dataclass(tp):
        return _build(tp, value, path)
    if tp is bool:
        if not isinstance(value, bool):
            raise ContractError(f"{path}: expected bool, got {type(value).__name__}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError(f"{path}: expected int, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ContractError(f"{path}: expected finite number, got {value!r}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise ContractError(f"{path}: expected str, got {value!r}")
        return value
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, list):
            raise ContractError(f"{path}: expected list, got {value!r}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(v, args[0], f"{path}[{i}]") for i, v in enumerate(value))
        if len(value) != len(args):
            raise ContractError(f"{path}: expected {len(args)} items, got {len(value)}")
        return tuple(_coerce(v, a, f"{path}[{i}]") for i, (v, a) in enumerate(zip(value, args)))
    if origin is dict:
        key_tp, val_tp = typing.get_args(tp)
        if not isinstance(value, dict):
            raise ContractError(f"{path}: expected mapping, got {value!r}")
        return types.MappingProxyType({_coerce(k, key_tp, f"{path} key"): _coerce(v, val_tp, f"{path}.{k}")
                                       for k, v in value.items()})
    raise ContractError(f"{path}: unsupported schema type {tp!r}")


def _build(cls: type, raw: Any, path: str) -> Any:
    if not isinstance(raw, dict):
        raise ContractError(f"{path}: expected mapping, got {raw!r}")
    hints = typing.get_type_hints(cls)
    fields = [f for f in dataclasses.fields(cls) if f.init and f.name != "sha256"]
    names = {f.name for f in fields}
    missing, extra = names - set(raw), set(raw) - names
    if missing or extra:
        raise ContractError(f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
    return cls(**{f.name: _coerce(raw[f.name], hints[f.name], f"{path}.{f.name}") for f in fields})


# ----------------------------------------------------------------------------- invariants

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractError(msg)


def _validate(c: Contract) -> None:
    managed = c.cluster.managed
    _require(len(managed) == 4 and len(set(managed)) == 4,
             f"cluster.managed must list 4 distinct deployments, got {managed}")
    _require(5 + FEATURES_PER_SERVICE * len(managed) + 3 == OBS_DIM, "obs layout does not match OBS_DIM")
    for name in ("base", "max"):
        _require(set(getattr(c.replicas, name)) == set(managed), f"replicas.{name} keys must equal cluster.managed")
    for d in managed:
        _require(1 <= c.replicas.base[d] <= c.replicas.max[d], f"replicas: need 1 <= base <= max for {d}")
    surplus = sum(c.replicas.max[d] - c.replicas.base[d] for d in managed)
    _require(c.reward.replica_denominator == surplus,
             f"reward.replica_denominator={c.reward.replica_denominator} != sum(max - base)={surplus}")
    _require(set(c.reward.action_cost) == set(ACTION_KINDS), f"reward.action_cost keys must be {ACTION_KINDS}")
    _require(set(c.episode.fault_probs) == set(FAULT_KINDS), f"episode.fault_probs keys must be {FAULT_KINDS}")
    _require(abs(sum(c.episode.fault_probs.values()) - 1.0) < PROB_SUM_TOL, "episode.fault_probs must sum to 1")
    for name in ("f2_targets", "f3_targets"):
        _require(set(getattr(c.episode, name)) <= set(managed), f"episode.{name} must be managed deployments")
    _require(0 < c.clock.collect_deadline_s < c.clock.tick_s, "clock.collect_deadline_s must be in (0, tick_s)")
    _require(0 < c.clock.late_frac < 1, "clock.late_frac must be in (0, 1)")
    for name in ("prom_timeout_s", "k8s_timeout_s", "locust_timeout_s"):
        connect_s, read_s = getattr(c.telemetry, name)
        _require(0 < connect_s and 0 < read_s, f"telemetry.{name} must be positive")
    _require(c.telemetry.locf_max_ticks >= 0, "telemetry.locf_max_ticks must be >= 0")
    _require(c.telemetry.stale_truncate_ticks >= 1, "telemetry.stale_truncate_ticks must be >= 1")
    _require(0 <= c.sla.e_sla < c.sla.e_max <= 1, "sla: need 0 <= e_sla < e_max <= 1")
    _require(set(c.golden_overrides) <= set(managed) | set(UNMANAGED_OVERRIDABLE),
             f"golden_overrides may only name managed deployments or {UNMANAGED_OVERRIDABLE}")


# ----------------------------------------------------------------------------- loaders

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> Contract:
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot read {path}: {type(exc).__name__}: {exc}") from exc
    contract = _build(Contract, raw, "contract")
    _validate(contract)
    return dataclasses.replace(contract, sha256=sha256_file(path))


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise ContractError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def load_calibration(path: Path = CALIBRATION_PATH, check_fresh: bool = True) -> Calibration:
    """Refuse a missing calibration, or one taken before the latest commit touching env/ (§7)."""
    if not path.exists():
        raise ContractError(f"{path} missing: run scripts/calibrate.py first (CLAUDE.md §7)")
    try:
        cal = _build(Calibration, json.loads(path.read_text()), "calibration")
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {path}: {type(exc).__name__}: {exc}") from exc
    _require(cal.l_sla_ms > 0 and cal.rps_base > 0 and cal.u_base > 0, "calibration values must be positive")
    if check_fresh:
        latest_env = _git("log", "-1", "--format=%H", "--", ENV_DIR)
        if latest_env:
            is_ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", latest_env, cal.env_git_sha],
                                         cwd=REPO_ROOT, capture_output=True, check=False).returncode == 0
            _require(is_ancestor, f"calibration env_git_sha {cal.env_git_sha[:8]} is older than the latest "
                                  f"env/ commit {latest_env[:8]}: re-run scripts/calibrate.py")
    return cal


@dataclass(frozen=True)
class Limits:
    cpu_cores: float
    memory_bytes: int


def load_limits(contract: Contract, path: Path = GOLDEN_LIVE_PATH) -> dict[str, Limits]:
    """limit_cores[d] and limit_bytes[d] for the managed services, read once from golden_live.json."""
    try:
        snap = json.loads(path.read_text())
        limits = {d: Limits(float(snap["deployments"][d]["limits"]["cpu_cores"]),
                            int(snap["deployments"][d]["limits"]["memory_bytes"]))
                  for d in contract.cluster.managed}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"cannot read limits from {path}: {type(exc).__name__}: {exc}") from exc
    for d, lim in limits.items():
        _require(lim.cpu_cores > 0 and lim.memory_bytes > 0, f"golden limits for {d} must be positive")
    return limits
