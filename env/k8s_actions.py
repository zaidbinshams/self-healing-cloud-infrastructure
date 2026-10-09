"""Action catalog, mask and in-flight lock (CLAUDE.md §5.2, §5.8).

Pure part (this module's top half): the 12-action catalog, `compute_mask`, the lock state machine
and replica-bound checks. The executor that talks to the Kubernetes API (agent client only) is
`ActionExecutor` at the bottom; it re-checks replica bounds independently of the mask.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.contract import N_ACTIONS, Contract
from env.golden import RESTARTED_AT_ANNOTATION, GoldenDeployment, template_hash
from env.recorder import EventLog
from env.telemetry import DeploymentStatus

NOOP, RESTART, SCALE, RESTORE = "NOOP", "RESTART", "SCALE", "RESTORE"


@dataclass(frozen=True)
class Action:
    id: int
    kind: str
    target: str | None
    delta: int = 0          # SCALE only: +1 / −1

    @property
    def name(self) -> str:
        if self.kind == SCALE:
            return f"SCALE_{'UP' if self.delta > 0 else 'DOWN'} {self.target}"
        return self.kind if self.target is None else f"{self.kind} {self.target}"


CATALOG: tuple[Action, ...] = (
    Action(0, NOOP, None),
    Action(1, RESTART, "frontend"),
    Action(2, RESTART, "cartservice"),
    Action(3, RESTART, "currencyservice"),
    Action(4, RESTART, "productcatalogservice"),
    Action(5, SCALE, "frontend", +1),
    Action(6, SCALE, "frontend", -1),
    Action(7, SCALE, "cartservice", +1),
    Action(8, SCALE, "currencyservice", +1),
    Action(9, RESTORE, "productcatalogservice"),
    Action(10, RESTORE, "cartservice"),
    Action(11, RESTORE, "currencyservice"),
)
assert len(CATALOG) == N_ACTIONS and all(a.id == i for i, a in enumerate(CATALOG))
SCALE_DOWN_FLOOR = 1      # SCALE_DOWN frontend is invalid at spec <= 1 (§5.8)


def validate_catalog(contract: Contract) -> None:
    targets = {a.target for a in CATALOG if a.target is not None}
    if not targets <= set(contract.cluster.managed):
        raise ValueError(f"catalog targets {sorted(targets)} must be managed deployments")


# ----------------------------------------------------------------------------- mask (pure)

def compute_mask(spec_replicas: Mapping[str, int], lock_held: bool, contract: Contract) -> np.ndarray:
    """§5.8 mask: NOOP always valid; only NOOP while the lock is held. RESTORE is never masked by
    drift from golden (that would leak the fault identity)."""
    mask = np.zeros(N_ACTIONS, dtype=np.int8)
    mask[0] = 1
    if lock_held:
        return mask
    for a in CATALOG[1:]:
        spec = int(spec_replicas[a.target])
        if a.kind == RESTART:
            ok = spec > 0
        elif a.kind == SCALE and a.delta > 0:
            ok = spec < contract.replicas.max[a.target]
        elif a.kind == SCALE:
            ok = spec > SCALE_DOWN_FLOOR
        else:                                  # RESTORE
            ok = True
        mask[a.id] = 1 if ok else 0
    return mask


def scale_target(action: Action, spec: int, contract: Contract) -> int | None:
    """Replica count a SCALE action would set, or None if it would leave [1, max] (executor check)."""
    n = spec + action.delta
    return n if 1 <= n <= contract.replicas.max[action.target] else None


def rollout_complete(st: DeploymentStatus) -> bool:
    """§5.8 rollout-complete predicate on a collected deployment status."""
    return (st.generation == st.observed_generation and st.updated_replicas == st.spec_replicas
            and st.available_replicas == st.spec_replicas and st.status_replicas == st.spec_replicas)


# ----------------------------------------------------------------------------- lock (pure)

@dataclass(frozen=True)
class Lock:
    action: Action
    target: str
    t_dispatch_wall: float
    t_dispatch_mono: float
    gen_before: int | None
    no_call: bool = False        # RESTORE that matched golden: clears at the next collection


def lock_should_clear(lock: Lock, st: DeploymentStatus | None, now_mono: float, contract: Contract) -> str | None:
    """Reason the lock clears now, or None. `st` is the target's freshly collected status (None if
    the deployments read failed this tick)."""
    if lock.no_call:
        return "no_call"
    if now_mono - lock.t_dispatch_mono >= contract.clock.inflight_timeout_s:
        return "stalled"
    if st is None:
        return None
    advanced = lock.gen_before is None or st.generation != lock.gen_before
    if advanced and rollout_complete(st):
        return "rollout_complete"
    return None


# ----------------------------------------------------------------------------- executor (I/O)

@dataclass(frozen=True)
class ExecResult:
    ok: bool
    action_id: int
    api_called: bool
    gen_before: int | None = None
    error_type: str = ""
    error: str = ""
    latency_s: float = 0.0


class ActionExecutor:
    """Dispatches catalog actions with the **agent** client only (§4.2.4). Never deletes pods,
    never changes requests or limits; re-checks replica bounds before every SCALE."""

    def __init__(self, contract: Contract, agent_api: client.ApiClient, golden: Mapping[str, GoldenDeployment],
                 events: EventLog) -> None:
        validate_catalog(contract)
        self.c, self.golden, self.events = contract, golden, events
        self.apps = client.AppsV1Api(agent_api)
        self.ns = contract.cluster.namespace
        self.timeout = contract.telemetry.k8s_timeout_s

    def _fail(self, action: Action, t0: float, error_type: str, error: str, tick: int | None,
              gen_before: int | None = None, api_called: bool = False) -> ExecResult:
        self.events.emit("exec_failed", component="k8s_actions", error_type=error_type, tick=tick,
                         action=action.name, error=error[:300])
        return ExecResult(False, action.id, api_called, gen_before, error_type, error[:300], time.monotonic() - t0)

    def execute(self, action: Action, tick: int | None = None) -> ExecResult:
        t0 = time.monotonic()
        if action.kind == NOOP:
            return ExecResult(True, action.id, False, latency_s=0.0)
        name = action.target
        assert name is not None
        try:
            dep = self.apps.read_namespaced_deployment(name, self.ns, _request_timeout=self.timeout)
            gen_before = int(dep.metadata.generation or 0)
            if action.kind == RESTART:
                body = {"spec": {"template": {"metadata": {"annotations": {
                    RESTARTED_AT_ANNOTATION: datetime.now(timezone.utc).isoformat()}}}}}
                self.apps.patch_namespaced_deployment(name, self.ns, body, _request_timeout=self.timeout)
            elif action.kind == SCALE:
                target = scale_target(action, int(dep.spec.replicas or 0), self.c)
                if target is None:
                    return self._fail(action, t0, "replica_bound", f"spec={dep.spec.replicas} out of bounds", tick,
                                      gen_before)
                self.apps.patch_namespaced_deployment_scale(name, self.ns, {"spec": {"replicas": target}},
                                                            _request_timeout=self.timeout)
            else:                                                     # RESTORE (§5.2)
                g = self.golden[name]
                live_tpl: dict[str, Any] = self.apps.api_client.sanitize_for_serialization(dep.spec.template)
                if template_hash(live_tpl) == g.template_hash and int(dep.spec.replicas or 0) == g.replicas:
                    self.events.emit("restore_no_call", component="k8s_actions", tick=tick, action=action.name)
                    return ExecResult(True, action.id, False, gen_before, latency_s=time.monotonic() - t0)
                # kubernetes-client v36.0.3 sends a list body as application/json-patch+json and
                # downgrades a dict body to strategic-merge-patch+json (rest.RESTClientObject.request).
                # RESTORE must be JSON Patch: strategic merge would keep EXTRA_LATENCY (§5.2).
                patch = [{"op": "replace", "path": "/spec/template", "value": g.template},
                         {"op": "replace", "path": "/spec/replicas", "value": g.replicas}]
                self.apps.patch_namespaced_deployment(name, self.ns, patch, _request_timeout=self.timeout)
        except ApiException as exc:
            return self._fail(action, t0, f"ApiException:{exc.status}", str(exc.reason), tick, api_called=True)
        except urllib3.exceptions.HTTPError as exc:
            return self._fail(action, t0, type(exc).__name__, str(exc), tick, api_called=True)
        return dataclasses.replace(ExecResult(True, action.id, True, gen_before), latency_s=time.monotonic() - t0)
