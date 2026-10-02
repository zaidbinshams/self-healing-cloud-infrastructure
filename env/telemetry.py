"""Telemetry: concurrent collection, imputation and normalization (CLAUDE.md §5.3–§5.5, §4.3).

Three layers, kept apart so the pure ones can be unit-tested on recorded real ticks:

1. **Fetchers (I/O).** Locust `/tick`, the three contract PromQL queries, and the two Kubernetes
   reads. Every call has the contract timeout, catches only the specific exceptions of §4.3.3 and
   returns a typed result with `ok=False` instead of raising, returning None or leaking NaN.
2. **Collector (I/O).** Runs the six fetchers concurrently under `collect_deadline_s`; a source
   that misses the deadline becomes a typed failure. Every failure is logged as a structured
   event and counted.
3. **Pure functions.** `impute()` applies §5.5 rules 1–8 and returns finite per-tick features
   plus the `stale` flag; `build_obs()` normalizes them into the 36-dim observation of §5.3.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import requests
import urllib3
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from env.contract import FEATURES_PER_SERVICE, OBS_DIM, Contract, Limits
from env.recorder import EventLog

# Pod name -> deployment, as in the PromQL DEP() macro (CLAUDE.md §5.4).
POD_TO_DEPLOYMENT_RE = r"^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$"
_POD_RE = re.compile(POD_TO_DEPLOYMENT_RE)
_SEL = '{namespace="boutique",container="server"}'
PROM_METRICS = ("cpu", "thr", "mem")
SOURCES = ("locust", "cpu", "thr", "mem", "deployments", "pods")

# Formula constants of the observation contract (CLAUDE.md §5.3).
P99_LOG_BASE_RATIO = 21.0          # p99 = log2(1 + P99/L_SLA) / log2(21)
RPS_SCALE = 3.0                    # rps = RPS / (3 * RPS_base)
RESTARTS_CAP = 3                   # restarts = min(delta, 3) / 3
ROLLOUT_RECENCY_TICKS = 5.0        # rollout_recency = exp(-ticks / 5)
TICKS_SINCE_ACTION_CAP = 10        # ticks_since_action = min(k, 10) / 10
IDX_IN_FLIGHT, IDX_TICKS_SINCE_ACTION, IDX_STALE = 33, 34, 35
SERVICE_OFFSET = 5


class TelemetryError(RuntimeError):
    """A non-finite value reached the observation (CLAUDE.md §5.5 rule 8)."""


# ----------------------------------------------------------------------------- PromQL

def _dep(expr: str) -> str:
    return f'label_replace({expr}, "deployment", "$1", "pod", "{POD_TO_DEPLOYMENT_RE}")'


def build_queries(rate_window: str) -> dict[str, str]:
    """Q_cpu, Q_thr, Q_mem verbatim from CLAUDE.md §5.4 (rate window from the contract)."""
    def rate(metric: str) -> str:
        return f"rate({metric}{_SEL}[{rate_window}])"

    def by_dep(expr: str) -> str:
        return f"sum by (deployment) ({_dep(expr)})"

    return {
        "cpu": by_dep(rate("container_cpu_usage_seconds_total")),
        "thr": f"{by_dep(rate('container_cpu_cfs_throttled_periods_total'))}\n"
               f"      / {by_dep(rate('container_cpu_cfs_periods_total'))}",
        "mem": by_dep(f"container_memory_working_set_bytes{_SEL}"),
    }


def pod_deployment(pod_name: str) -> str | None:
    m = _POD_RE.match(pod_name)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------- typed results

@dataclass(frozen=True)
class LocustResult:
    ok: bool
    n: int = 0
    failures: int = 0
    p50_ms: float = 0.0
    p99_ms: float = 0.0
    rps: float = 0.0
    error_type: str = ""
    error: str = ""
    latency_s: float = 0.0


@dataclass(frozen=True)
class PromResult:
    ok: bool
    query: str
    eval_time_s: float
    values: dict[str, float] = field(default_factory=dict)   # deployment -> value (may be NaN)
    error_type: str = ""
    error: str = ""
    latency_s: float = 0.0


@dataclass(frozen=True)
class DeploymentStatus:
    spec_replicas: int
    status_replicas: int
    available_replicas: int
    updated_replicas: int
    generation: int
    observed_generation: int


@dataclass(frozen=True)
class DeploymentsResult:
    ok: bool
    items: dict[str, DeploymentStatus] = field(default_factory=dict)
    error_type: str = ""
    error: str = ""
    latency_s: float = 0.0


@dataclass(frozen=True)
class PodsResult:
    ok: bool
    restarts: dict[str, int] = field(default_factory=dict)              # deployment -> sum restartCount
    pod_uids: dict[str, list[str]] = field(default_factory=dict)       # deployment -> pod UIDs
    error_type: str = ""
    error: str = ""
    latency_s: float = 0.0


@dataclass(frozen=True)
class RawTick:
    """Everything collected for one window (wall(T_k), wall(T_{k+1})]; nothing imputed yet."""
    tick: int
    t_from_wall: float
    t_to_wall: float
    locust: LocustResult
    prom: dict[str, PromResult]
    deployments: DeploymentsResult
    pods: PodsResult
    collect_s: float

    def source_ok(self) -> dict[str, bool]:
        return {"locust": self.locust.ok, **{m: self.prom[m].ok for m in PROM_METRICS},
                "deployments": self.deployments.ok, "pods": self.pods.ok}


def raw_tick_to_dict(raw: RawTick) -> dict[str, Any]:
    return dataclasses.asdict(raw)


def raw_tick_from_dict(d: Mapping[str, Any]) -> RawTick:
    deps = d["deployments"]
    return RawTick(
        tick=int(d["tick"]), t_from_wall=float(d["t_from_wall"]), t_to_wall=float(d["t_to_wall"]),
        locust=LocustResult(**d["locust"]),
        prom={m: PromResult(**{**p, "values": {k: float(v) for k, v in p["values"].items()}})
              for m, p in d["prom"].items()},
        deployments=DeploymentsResult(**{**deps, "items": {k: DeploymentStatus(**v)
                                                           for k, v in deps["items"].items()}}),
        pods=PodsResult(**d["pods"]),
        collect_s=float(d["collect_s"]),
    )


# ----------------------------------------------------------------------------- fetchers (I/O)

def make_api_client(kubeconfig: str | Path) -> client.ApiClient:
    """Kubernetes client with library retries disabled (§4.3.2). Telemetry uses the agent kubeconfig."""
    cfg = client.Configuration()
    config.load_kube_config(config_file=str(kubeconfig), client_configuration=cfg)
    cfg.retries = False
    return client.ApiClient(cfg)


def fetch_locust(locust_url: str, t_from_wall: float, t_to_wall: float,
                 timeout_s: tuple[float, float]) -> LocustResult:
    t0 = time.monotonic()
    try:
        resp = requests.get(f"{locust_url.rstrip('/')}/tick",
                            params={"from": f"{t_from_wall:.6f}", "to": f"{t_to_wall:.6f}"}, timeout=timeout_s)
        resp.raise_for_status()
        body = resp.json()
        result = LocustResult(True, int(body["n"]), int(body["failures"]), float(body["p50_ms"]),
                              float(body["p99_ms"]), float(body["rps"]))
    except requests.exceptions.RequestException as exc:
        result = LocustResult(False, error_type=type(exc).__name__, error=str(exc)[:300])
    except (ValueError, KeyError, TypeError) as exc:     # json.JSONDecodeError is a ValueError
        result = LocustResult(False, error_type=f"parse:{type(exc).__name__}", error=str(exc)[:300])
    return dataclasses.replace(result, latency_s=time.monotonic() - t0)


def fetch_prom(prom_url: str, name: str, expr: str, eval_time_s: float,
               timeout_s: tuple[float, float], managed: tuple[str, ...]) -> PromResult:
    t0 = time.monotonic()
    try:
        resp = requests.get(f"{prom_url.rstrip('/')}/api/v1/query",
                            params={"query": expr, "time": f"{eval_time_s:.3f}"}, timeout=timeout_s)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            result = PromResult(False, name, eval_time_s, error_type="prom_status",
                                error=f"status={body.get('status')} {body.get('error', '')}"[:300])
        else:
            values = {row["metric"].get("deployment", ""): float(row["value"][1])
                      for row in body["data"]["result"]}
            result = PromResult(True, name, eval_time_s, {d: v for d, v in values.items() if d in managed})
    except requests.exceptions.RequestException as exc:
        result = PromResult(False, name, eval_time_s, error_type=type(exc).__name__, error=str(exc)[:300])
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        result = PromResult(False, name, eval_time_s, error_type=f"parse:{type(exc).__name__}",
                            error=str(exc)[:300])
    return dataclasses.replace(result, latency_s=time.monotonic() - t0)


def fetch_deployments(apps: client.AppsV1Api, ns: str, managed: tuple[str, ...],
                      timeout_s: tuple[float, float]) -> DeploymentsResult:
    t0 = time.monotonic()
    try:
        deps = apps.list_namespaced_deployment(ns, _request_timeout=timeout_s).items
        items = {
            d.metadata.name: DeploymentStatus(
                spec_replicas=int(d.spec.replicas or 0),
                status_replicas=int(d.status.replicas or 0),
                available_replicas=int(d.status.available_replicas or 0),
                updated_replicas=int(d.status.updated_replicas or 0),
                generation=int(d.metadata.generation or 0),
                observed_generation=int(d.status.observed_generation or 0))
            for d in deps if d.metadata.name in managed}
        missing = sorted(set(managed) - set(items))
        result = (DeploymentsResult(True, items) if not missing else
                  DeploymentsResult(False, items, "missing_deployment", f"not found: {missing}"))
    except ApiException as exc:
        result = DeploymentsResult(False, error_type=f"ApiException:{exc.status}", error=str(exc.reason)[:300])
    except urllib3.exceptions.HTTPError as exc:
        result = DeploymentsResult(False, error_type=type(exc).__name__, error=str(exc)[:300])
    return dataclasses.replace(result, latency_s=time.monotonic() - t0)


def fetch_pods(core: client.CoreV1Api, ns: str, managed: tuple[str, ...],
               timeout_s: tuple[float, float]) -> PodsResult:
    t0 = time.monotonic()
    try:
        pods = core.list_namespaced_pod(ns, _request_timeout=timeout_s).items
        restarts = dict.fromkeys(managed, 0)
        uids: dict[str, list[str]] = {d: [] for d in managed}
        for pod in pods:
            dep = pod_deployment(pod.metadata.name)
            if dep not in restarts:
                continue
            uids[dep].append(pod.metadata.uid)
            restarts[dep] += sum(int(cs.restart_count or 0) for cs in pod.status.container_statuses or [])
        result = PodsResult(True, restarts, {d: sorted(u) for d, u in uids.items()})
    except ApiException as exc:
        result = PodsResult(False, error_type=f"ApiException:{exc.status}", error=str(exc.reason)[:300])
    except urllib3.exceptions.HTTPError as exc:
        result = PodsResult(False, error_type=type(exc).__name__, error=str(exc)[:300])
    return dataclasses.replace(result, latency_s=time.monotonic() - t0)


# ----------------------------------------------------------------------------- collector (I/O)

class Collector:
    """Concurrent collection of all six sources under the contract deadline (§4.3.5, §5.7)."""

    def __init__(self, contract: Contract, prom_url: str, locust_url: str,
                 agent_api: client.ApiClient, events: EventLog) -> None:
        self.c = contract
        self.prom_url, self.locust_url = prom_url, locust_url
        self.apps = client.AppsV1Api(agent_api)
        self.core = client.CoreV1Api(agent_api)
        self.events = events
        self.queries = build_queries(contract.telemetry.rate_window)
        self.counters: Counter[str] = Counter()
        # Timed-out calls finish in the background within their own timeouts, so 2x headroom
        # keeps the next tick's six submissions from queueing behind them.
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2 * len(SOURCES),
                                                           thread_name_prefix="telemetry")

    def _failure(self, source: str, eval_time_s: float, error_type: str, error: str) -> Any:
        if source == "locust":
            return LocustResult(False, error_type=error_type, error=error)
        if source in PROM_METRICS:
            return PromResult(False, source, eval_time_s, error_type=error_type, error=error)
        if source == "deployments":
            return DeploymentsResult(False, error_type=error_type, error=error)
        return PodsResult(False, error_type=error_type, error=error)

    def collect(self, tick: int, t_from_wall: float, t_to_wall: float) -> RawTick:
        """Collect the window (t_from_wall, t_to_wall]; Prometheus is evaluated at t_to_wall."""
        tel, managed, ns = self.c.telemetry, self.c.cluster.managed, self.c.cluster.namespace
        jobs: dict[str, Callable[[], Any]] = {
            "locust": lambda: fetch_locust(self.locust_url, t_from_wall, t_to_wall, tel.locust_timeout_s),
            **{m: (lambda m=m: fetch_prom(self.prom_url, m, self.queries[m], t_to_wall,
                                          tel.prom_timeout_s, managed)) for m in PROM_METRICS},
            "deployments": lambda: fetch_deployments(self.apps, ns, managed, tel.k8s_timeout_s),
            "pods": lambda: fetch_pods(self.core, ns, managed, tel.k8s_timeout_s),
        }
        t0 = time.monotonic()
        futures = {name: self._pool.submit(fn) for name, fn in jobs.items()}
        concurrent.futures.wait(futures.values(), timeout=self.c.clock.collect_deadline_s)
        results: dict[str, Any] = {}
        for name, fut in futures.items():
            if fut.done():
                results[name] = fut.result()
            else:
                fut.cancel()
                results[name] = self._failure(name, t_to_wall, "deadline",
                                              f"no result within {self.c.clock.collect_deadline_s} s")
        collect_s = time.monotonic() - t0
        for name, res in results.items():
            if not res.ok:
                self.counters[f"{name}:{res.error_type}"] += 1
                self.events.emit("source_failed", component=f"telemetry.{name}", error_type=res.error_type,
                                 tick=tick, error=res.error)
        return RawTick(tick, t_from_wall, t_to_wall, results["locust"],
                       {m: results[m] for m in PROM_METRICS}, results["deployments"], results["pods"], collect_s)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ----------------------------------------------------------------------------- imputation (pure)

@dataclass(frozen=True)
class Carried:
    value: float
    age: int          # ticks since the value was last valid (0 = valid this tick)


@dataclass(frozen=True)
class ImputeState:
    """Per-episode memory for §5.5: LOCF cache, previous restart sums, generation tracking."""
    cache: dict[str, Carried] = field(default_factory=dict)
    prev_restart_sum: dict[str, int] = field(default_factory=dict)
    generation: dict[str, int] = field(default_factory=dict)
    ticks_since_gen_change: dict[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceFeatures:
    cpu_cores: float
    throttle: float
    mem_bytes: float
    spec_replicas: float
    status_replicas: float
    available_replicas: float
    restarts_delta: float
    ticks_since_gen_change: int | None


@dataclass(frozen=True)
class TickFeatures:
    """Finite, denormalized features for one tick after §5.5 imputation."""
    p99_ms: float
    fail_ratio: float
    rps: float
    services: dict[str, ServiceFeatures]
    stale: bool
    imputed: tuple[str, ...]          # keys filled by rule 3 (LOCF) or rule 4 (zero)
    failed_sources: tuple[str, ...]


class _Imputer:
    """Accumulates one tick's imputation; used only inside `impute()`."""

    def __init__(self, state: ImputeState, locf_max_ticks: int) -> None:
        self.old = state.cache
        self.new: dict[str, Carried] = {}
        self.locf_max = locf_max_ticks
        self.imputed: list[str] = []

    def valid(self, key: str, value: float) -> float:
        self.new[key] = Carried(float(value), 0)
        return float(value)

    def fill(self, key: str) -> float:
        """Rules 3/4: carry forward a value <= locf_max_ticks old, else 0."""
        self.imputed.append(key)
        prev = self.old.get(key)
        if prev is not None and prev.age + 1 <= self.locf_max:
            self.new[key] = Carried(prev.value, prev.age + 1)
            return prev.value
        return 0.0

    def take(self, key: str, value: float | None) -> float:
        """Rule 2 if `value` is finite, else rules 3/4."""
        if value is not None and math.isfinite(value):
            return self.valid(key, value)
        return self.fill(key)


def impute(raw: RawTick, state: ImputeState, contract: Contract) -> tuple[TickFeatures, ImputeState]:
    """Apply CLAUDE.md §5.5 rules 1–8 to one raw tick. Pure: returns new features and new state."""
    managed = contract.cluster.managed
    imp = _Imputer(state, contract.telemetry.locf_max_ticks)
    failed = tuple(s for s, ok in raw.source_ok().items() if not ok)

    # Rule 6/7 — Locust: endpoint failure or n == 0 (Locust dead) -> carry forward + stale.
    loc = raw.locust
    loc_ok = loc.ok and loc.n > 0
    p99_ms = imp.take("locust/p99_ms", loc.p99_ms if loc_ok else None)
    fail_ratio = imp.take("locust/fail_ratio", loc.failures / loc.n if loc_ok else None)
    rps = imp.take("locust/rps", loc.rps if loc_ok else None)
    if loc.ok and loc.n == 0:
        failed = (*failed, "locust:n=0")

    deps_ok, pods_ok = raw.deployments.ok, raw.pods.ok
    generation = dict(state.generation)
    since = dict(state.ticks_since_gen_change)
    prev_sum = dict(state.prev_restart_sum)
    services: dict[str, ServiceFeatures] = {}
    for d in managed:
        # Kubernetes deployment status (rule 7 on whole-source failure).
        st = raw.deployments.items.get(d) if deps_ok else None
        spec = imp.take(f"dep/{d}/spec", st.spec_replicas if st else None)
        status_replicas = imp.take(f"dep/{d}/status", st.status_replicas if st else None)
        available = imp.take(f"dep/{d}/available", st.available_replicas if st else None)
        if st is not None:
            if d not in generation:                 # first observation this episode
                since[d] = None
            elif st.generation != generation[d]:
                since[d] = 0
            elif since.get(d) is not None:
                since[d] = since[d] + 1
            generation[d] = st.generation
        elif since.get(d) is not None:
            since[d] = since[d] + 1

        # Restarts: rule 5 (counter decreased -> 0); first tick of an episode -> 0.
        if pods_ok and d in raw.pods.restarts:
            total = raw.pods.restarts[d]
            delta = max(0, total - prev_sum[d]) if d in prev_sum else 0
            restarts_delta = imp.valid(f"pods/{d}/restarts", float(delta))
            prev_sum[d] = total
        else:
            restarts_delta = imp.fill(f"pods/{d}/restarts")

        # Prometheus metrics: rule 1 (no pods = true zero), else rules 2–4.
        values: dict[str, float] = {}
        for m in PROM_METRICS:
            key = f"prom/{m}/{d}"
            if st is not None and st.status_replicas == 0:
                values[m] = imp.valid(key, 0.0)
            else:
                pr = raw.prom[m]
                values[m] = imp.take(key, pr.values.get(d) if pr.ok else None)

        services[d] = ServiceFeatures(values["cpu"], values["thr"], values["mem"], spec, status_replicas,
                                      available, restarts_delta, since.get(d))

    features = TickFeatures(p99_ms, fail_ratio, rps, services,
                            stale=bool(imp.imputed or failed), imputed=tuple(imp.imputed), failed_sources=failed)
    _assert_finite(features)
    return features, ImputeState(imp.new, prev_sum, generation, since)


def _assert_finite(f: TickFeatures) -> None:
    nums = [f.p99_ms, f.fail_ratio, f.rps]
    for s in f.services.values():
        nums += [s.cpu_cores, s.throttle, s.mem_bytes, s.spec_replicas, s.status_replicas,
                 s.available_replicas, s.restarts_delta]
    if not all(math.isfinite(x) for x in nums):
        raise TelemetryError(f"non-finite feature after imputation: {f}")


def features_to_dict(f: TickFeatures) -> dict[str, Any]:
    return dataclasses.asdict(f)


# ----------------------------------------------------------------------------- normalization (pure)

def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


def norm_p99(p99_ms: float, l_sla_ms: float) -> float:
    return _clip01(math.log2(1.0 + p99_ms / l_sla_ms) / math.log2(P99_LOG_BASE_RATIO))


def build_obs(f: TickFeatures, prev: TickFeatures | None, *, contract: Contract, limits: Mapping[str, Limits],
              l_sla_ms: float, rps_base: float, in_flight: bool, ticks_since_action: int | None) -> np.ndarray:
    """The 36-dim observation of CLAUDE.md §5.3 (float32, every entry in [-1, 1]).

    `prev` is the previous tick's features in the same episode (None on the first tick, giving
    d_p99 = d_fail = 0). `ticks_since_action` is None if no action has been taken this episode.
    """
    # Check inputs first: min/max clipping maps NaN to a bound, which would hide it (§5.5 rule 8).
    _assert_finite(f)
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    p99 = norm_p99(f.p99_ms, l_sla_ms)
    fail = _clip01(f.fail_ratio)
    obs[0], obs[1] = p99, fail
    obs[2] = _clip01(f.rps / (RPS_SCALE * rps_base))
    if prev is not None:
        obs[3] = p99 - norm_p99(prev.p99_ms, l_sla_ms)
        obs[4] = fail - _clip01(prev.fail_ratio)
    for i, d in enumerate(contract.cluster.managed):
        s, lim = f.services[d], limits[d]
        pods = max(1.0, s.status_replicas)
        base = SERVICE_OFFSET + FEATURES_PER_SERVICE * i
        obs[base + 0] = _clip01(s.cpu_cores / (lim.cpu_cores * pods))
        obs[base + 1] = _clip01(s.throttle)
        obs[base + 2] = _clip01(s.mem_bytes / (lim.memory_bytes * pods))
        obs[base + 3] = _clip01(s.available_replicas / contract.replicas.base[d])
        # spec <= max is enforced by mask and executor; the clip only guards Box(-1, 1).
        obs[base + 4] = _clip01(s.spec_replicas / contract.replicas.max[d])
        obs[base + 5] = min(s.restarts_delta, RESTARTS_CAP) / RESTARTS_CAP
        obs[base + 6] = (0.0 if s.ticks_since_gen_change is None
                         else math.exp(-s.ticks_since_gen_change / ROLLOUT_RECENCY_TICKS))
    obs[IDX_IN_FLIGHT] = 1.0 if in_flight else 0.0
    obs[IDX_TICKS_SINCE_ACTION] = (1.0 if ticks_since_action is None
                                   else min(ticks_since_action, TICKS_SINCE_ACTION_CAP) / TICKS_SINCE_ACTION_CAP)
    obs[IDX_STALE] = 1.0 if f.stale else 0.0
    if not np.all(np.isfinite(obs)):
        raise TelemetryError(f"non-finite observation: {obs.tolist()}")
    return obs


def dumps(obj: Any) -> str:
    """JSON for recorded ticks; keeps NaN as written by Prometheus so fixtures stay raw."""
    return json.dumps(obj, sort_keys=True)
