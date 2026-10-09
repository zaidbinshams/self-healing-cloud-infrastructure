"""Fault injector (CLAUDE.md §5.10). Controller client only; never imported by agents/.

Pure part: `sample_plan` (the episode's fault plan from `episode.*`, seeded env RNG) and the
cure predicates. I/O part: `Injector`, which injects at tick L, checks cure conditions every
tick, deletes the F2 StressChaos the moment its pod is gone, and cleans up for reset().

Ground truth (fault, target, severity, injection time) is returned for `info` and logs only;
it must never reach the observation or the mask (§4.1.4).
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import requests
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.contract import Contract
from env.k8s_actions import rollout_complete
from env.recorder import EventLog
from env.telemetry import RawTick

FAULT_ENV = "EXTRA_LATENCY"
F1_TARGET = "productcatalogservice"
F4_TARGET = "frontend"
APP_CONTAINER = "server"
CHAOS_GROUP, CHAOS_VERSION, CHAOS_PLURAL = "chaos-mesh.org", "v1alpha1", "stresschaos"
STRESS_LOAD_PCT = 100                 # §5.10 manifest: stressors.cpu.load
STRESS_DURATION = "30m"               # §5.10 manifest: the CR is deleted at cure, long before this
K8S_NAME_MAX = 63                     # RFC 1123 label: lowercase alphanumerics and '-', <= 63 chars


@dataclass(frozen=True)
class FaultPlan:
    fault: str                        # F1 | F2 | F3 | F4 | NULL
    target: str | None
    severity: str | int | float | None
    lead_in: int                      # L: injection happens in step() of tick L


def sample_plan(rng: random.Random, contract: Contract) -> FaultPlan:
    """Draw one episode plan from `episode.*` with the seeded env RNG (§5.9 step 6)."""
    e = contract.episode
    faults = sorted(e.fault_probs)
    fault = rng.choices(faults, weights=[e.fault_probs[f] for f in faults], k=1)[0]
    lo, hi = e.lead_in_ticks
    lead_in = rng.randint(lo, hi)
    if fault == "F1":
        return FaultPlan(fault, F1_TARGET, rng.choice(e.f1_latency), lead_in)
    if fault == "F2":
        return FaultPlan(fault, rng.choice(e.f2_targets), rng.choice(e.f2_workers), lead_in)
    if fault == "F3":
        return FaultPlan(fault, rng.choice(e.f3_targets), None, lead_in)
    if fault == "F4":
        return FaultPlan(fault, F4_TARGET, rng.choice(e.f4_multiplier), lead_in)
    return FaultPlan("NULL", None, None, lead_in)


def plan_from_schedule(entry: Mapping[str, Any]) -> FaultPlan:
    """Evaluation reads the plan from the committed schedule instead of sampling it."""
    return FaultPlan(str(entry["fault"]), entry.get("target"), entry.get("severity"), int(entry["lead_in"]))


# ----------------------------------------------------------------------------- cure predicates (pure)

def template_has_fault_env(template: Mapping[str, Any]) -> bool:
    return any(env.get("name") == FAULT_ENV
               for c in (template.get("spec") or {}).get("containers", [])
               for env in c.get("env") or [])


def f1_cured(template: Mapping[str, Any], raw: RawTick) -> bool:
    st = raw.deployments.items.get(F1_TARGET) if raw.deployments.ok else None
    return st is not None and not template_has_fault_env(template) and rollout_complete(st)


def f2_cured(stressed_uid: str, raw: RawTick, target: str) -> bool | None:
    """True once the stressed pod's UID is gone; None if pods could not be read this tick."""
    if not raw.pods.ok:
        return None
    return stressed_uid not in raw.pods.pod_uids.get(target, [])


def f3_cured(raw: RawTick, target: str) -> bool:
    st = raw.deployments.items.get(target) if raw.deployments.ok else None
    return st is not None and st.available_replicas >= 1


def chaos_name(run: str, episode: int) -> str:
    """StressChaos object name `f2-<run>-<episode>` made RFC-1123 valid (run names may contain '_')."""
    suffix = f"-{episode}"
    body = re.sub(r"[^a-z0-9-]+", "-", f"f2-{run}".lower()).strip("-")
    body = re.sub(r"-{2,}", "-", body)[: K8S_NAME_MAX - len(suffix)].rstrip("-")
    return body + suffix


def surge_users(multiplier: float, u_base: int) -> int:
    return round(multiplier * u_base)


def stresschaos_manifest(name: str, ns: str, target: str, workers: int) -> dict[str, Any]:
    """The F2 StressChaos of §5.10."""
    return {
        "apiVersion": f"{CHAOS_GROUP}/{CHAOS_VERSION}", "kind": "StressChaos",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"mode": "one", "selector": {"namespaces": [ns], "labelSelectors": {"app": target}},
                 "containerNames": [APP_CONTAINER],
                 "stressors": {"cpu": {"workers": int(workers), "load": STRESS_LOAD_PCT}},
                 "duration": STRESS_DURATION},
    }


# ----------------------------------------------------------------------------- injector (I/O)

@dataclass(frozen=True)
class InjectResult:
    ok: bool
    fault: str
    t_inject_wall: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error: str = ""


class Injector:
    """Holds the controller client; used only by the env's step() and reset()."""

    def __init__(self, contract: Contract, controller_api: client.ApiClient, locust_url: str, u_base: int,
                 run: str, events: EventLog) -> None:
        self.c, self.locust_url, self.u_base, self.run, self.events = contract, locust_url, u_base, run, events
        self.ns = contract.cluster.namespace
        self.k8s_timeout = contract.telemetry.k8s_timeout_s
        self.locust_timeout = contract.telemetry.locust_timeout_s
        self.apps = client.AppsV1Api(controller_api)
        self.core = client.CoreV1Api(controller_api)
        self.custom = client.CustomObjectsApi(controller_api)
        self.plan: FaultPlan | None = None
        self.episode = 0
        self.injected: InjectResult | None = None
        self.stressed_uid: str | None = None
        self.chaos_name: str | None = None
        self.cured = False
        self.counters: Counter[str] = Counter()

    def _log_failure(self, event: str, error_type: str, tick: int | None, **fields: Any) -> None:
        """Structured event + counter for every caught failure (§4.3.4)."""
        self.counters[f"{event}:{error_type}"] += 1
        self.events.emit(event, component="injector", error_type=error_type, tick=tick, **fields)

    # --- lifecycle ---------------------------------------------------------------

    def arm(self, plan: FaultPlan, episode: int) -> None:
        self.plan, self.episode = plan, episode
        self.injected, self.stressed_uid, self.chaos_name, self.cured = None, None, None, plan.fault == "NULL"

    def _failed(self, fault: str, error_type: str, error: str, tick: int | None) -> InjectResult:
        self._log_failure("inject_failed", error_type=error_type, tick=tick,
                         fault=fault, error=error[:300])
        return InjectResult(False, fault, error_type=error_type, error=error[:300])

    def on_tick(self, k: int) -> InjectResult | None:
        """Inject iff k == L (once). Returns the result on the injection tick, else None."""
        p = self.plan
        if p is None or self.injected is not None or k != p.lead_in:
            return None
        if p.fault == "NULL":
            self.injected = InjectResult(True, "NULL", time.time())
            return self.injected
        try:
            if p.fault == "F1":
                body = {"spec": {"template": {"spec": {"containers": [
                    {"name": APP_CONTAINER, "env": [{"name": FAULT_ENV, "value": str(p.severity)}]}]}}}}
                t = time.time()
                self.apps.patch_namespaced_deployment(F1_TARGET, self.ns, body, _request_timeout=self.k8s_timeout)
                res = InjectResult(True, "F1", t, {"severity": p.severity})
            elif p.fault == "F2":
                pods = self.core.list_namespaced_pod(self.ns, label_selector=f"app={p.target}",
                                                     _request_timeout=self.k8s_timeout).items
                running = [x for x in pods if x.status.phase == "Running" and x.metadata.deletion_timestamp is None]
                if len(running) != 1:
                    return self._remember(self._failed("F2", "precondition", f"{len(running)} running pods of "
                                                       f"{p.target}, need exactly 1", k))
                self.stressed_uid = running[0].metadata.uid
                self.chaos_name = chaos_name(self.run, self.episode)
                t = time.time()
                self.custom.create_namespaced_custom_object(
                    CHAOS_GROUP, CHAOS_VERSION, self.ns, CHAOS_PLURAL,
                    stresschaos_manifest(self.chaos_name, self.ns, str(p.target), int(p.severity or 1)),
                    _request_timeout=self.k8s_timeout)
                res = InjectResult(True, "F2", t, {"pod_uid": self.stressed_uid, "cr": self.chaos_name,
                                                   "workers": p.severity})
            elif p.fault == "F3":
                t = time.time()
                self.apps.patch_namespaced_deployment_scale(p.target, self.ns, {"spec": {"replicas": 0}},
                                                            _request_timeout=self.k8s_timeout)
                res = InjectResult(True, "F3", t)
            else:                                                   # F4
                users = surge_users(float(p.severity), self.u_base)
                t = time.time()
                self._swarm(users)
                res = InjectResult(True, "F4", t, {"users": users})
        except ApiException as exc:
            res = self._failed(p.fault, f"ApiException:{exc.status}", str(exc.reason), k)
        except urllib3.exceptions.HTTPError as exc:
            res = self._failed(p.fault, type(exc).__name__, str(exc), k)
        except requests.exceptions.RequestException as exc:
            res = self._failed(p.fault, type(exc).__name__, str(exc), k)
        except (ValueError, KeyError) as exc:
            res = self._failed(p.fault, f"parse:{type(exc).__name__}", str(exc), k)
        if res.ok:
            fields = {"fault": p.fault, "target": p.target, "severity": p.severity,
                      "t_inject_wall": res.t_inject_wall, **res.detail}   # one dict: detail may repeat keys
            self.events.emit("injected", component="injector", tick=k, **fields)
        return self._remember(res)

    def _remember(self, res: InjectResult) -> InjectResult:
        self.injected = res
        return res

    # --- cure --------------------------------------------------------------------

    def check_cure(self, raw: RawTick, tick: int) -> bool:
        """Evaluate the plan's cure condition on this tick's collection (§5.10). For F2 the
        StressChaos CR is deleted as soon as the stressed pod is gone."""
        p = self.plan
        if p is None or self.injected is None or not self.injected.ok:
            return self.cured
        if p.fault == "F1":
            try:
                dep = self.apps.read_namespaced_deployment(F1_TARGET, self.ns, _request_timeout=self.k8s_timeout)
                tpl = self.apps.api_client.sanitize_for_serialization(dep.spec.template)
                self.cured = f1_cured(tpl, raw)
            except ApiException as exc:
                self._cure_read_failed(f"ApiException:{exc.status}", str(exc.reason), tick)
            except urllib3.exceptions.HTTPError as exc:
                self._cure_read_failed(type(exc).__name__, str(exc), tick)
        elif p.fault == "F2" and self.stressed_uid is not None:
            gone = f2_cured(self.stressed_uid, raw, str(p.target))
            if gone:
                self.delete_chaos(tick)
                self.cured = True
        elif p.fault == "F3":
            self.cured = f3_cured(raw, str(p.target))
        elif p.fault in ("F4", "NULL"):
            self.cured = True
        return self.cured

    def _cure_read_failed(self, error_type: str, error: str, tick: int) -> None:
        self._log_failure("cure_check_failed", error_type=error_type, tick=tick,
                         error=error[:300])

    # --- cleanup (reset) -----------------------------------------------------------

    def delete_chaos(self, tick: int | None = None) -> bool:
        if self.chaos_name is None:
            return True
        try:
            self.custom.delete_namespaced_custom_object(CHAOS_GROUP, CHAOS_VERSION, self.ns, CHAOS_PLURAL,
                                                        self.chaos_name, _request_timeout=self.k8s_timeout)
        except ApiException as exc:
            if exc.status != 404:
                self._log_failure("chaos_delete_failed", error_type=f"ApiException:{exc.status}",
                                 tick=tick, cr=self.chaos_name)
                return False
        except urllib3.exceptions.HTTPError as exc:
            self._log_failure("chaos_delete_failed", error_type=type(exc).__name__,
                             tick=tick, cr=self.chaos_name)
            return False
        self.events.emit("chaos_deleted", component="injector", tick=tick, cr=self.chaos_name)
        self.chaos_name = None
        return True

    def list_chaos(self) -> list[str] | None:
        """Names of all StressChaos CRs in the namespace; None if the list call failed."""
        try:
            body = self.custom.list_namespaced_custom_object(CHAOS_GROUP, CHAOS_VERSION, self.ns, CHAOS_PLURAL,
                                                             _request_timeout=self.k8s_timeout)
            return [item["metadata"]["name"] for item in body.get("items", [])]
        except ApiException as exc:
            self._log_failure("chaos_list_failed", error_type=f"ApiException:{exc.status}", tick=None)
        except urllib3.exceptions.HTTPError as exc:
            self._log_failure("chaos_list_failed", error_type=type(exc).__name__, tick=None)
        except (KeyError, TypeError) as exc:
            self._log_failure("chaos_list_failed", error_type=f"parse:{type(exc).__name__}",
                             tick=None)
        return None

    def remove_all_faults(self) -> bool:
        """reset() step 1: delete every StressChaos CR, return Locust to U_base, confirm none remain."""
        names = self.list_chaos()
        if names is None:
            return False
        for n in names:
            self.chaos_name = n
            self.delete_chaos()
        self.chaos_name = None
        try:
            self._swarm(self.u_base)
        except requests.exceptions.RequestException as exc:
            self._log_failure("swarm_failed", error_type=type(exc).__name__, tick=None)
            return False
        remaining = self.list_chaos()
        return remaining == []

    def _swarm(self, users: int) -> None:
        # All users within ~1 s: spawn rate = user count (same default as locust/run_locust.sh).
        resp = requests.post(f"{self.locust_url.rstrip('/')}/swarm",
                             data={"user_count": users, "spawn_rate": users}, timeout=self.locust_timeout)
        resp.raise_for_status()
