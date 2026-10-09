"""BoutiqueEnv: the real-cluster Gymnasium environment (CLAUDE.md §5.3, §5.7–§5.9).

Composes the tested pieces: TickClock (fixed 20 s wall-clock tick), Collector (concurrent telemetry
under the 3.0 s deadline), impute/build_obs (§5.5/§5.3), compute_mask + Lock (§5.8),
ActionExecutor (agent client, async single worker), Injector (controller client), reward (§5.6)
and TransitionRecorder (§6). There is no simulated path: every step() spans one real tick.

Credentials: step() dispatches with the agent client only. reset() and the injector hold the
controller client (§4.2.4). Nothing here shells out to kubectl.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import gymnasium as gym
import numpy as np
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from env.clock import TickClock
from env.contract import N_ACTIONS, OBS_DIM, REPO_ROOT, Calibration, Contract, Limits
from env.golden import GoldenDeployment
from env.injector import FaultPlan, Injector, sample_plan
from env.k8s_actions import (
    CATALOG,
    RESTORE,
    Action,
    ActionExecutor,
    ExecResult,
    Lock,
    compute_mask,
    lock_should_clear,
    rollout_complete,
)
from env.recorder import EventLog, TransitionRecorder
from env.reward import healthy, recovery_start, replica_surplus, reward, sla_violation
from env.telemetry import (
    Collector,
    DeploymentStatus,
    ImputeState,
    RawTick,
    TickFeatures,
    build_obs,
    features_to_dict,
    fetch_locust,
    fetch_prom,
    impute,
)

# reset() timing from CLAUDE.md §5.9 (spec values, not tunables).
RESET_POLL_S = 5.0
RESET_DEADLINE_S = 180.0
SETTLE_AFTER_POD_CHANGE_S = 30.0
PROM_PING_QUERY = "vector(1)"
RESET_RESTORE_ID = -1          # reset() RESTOREs are not catalog actions and never reach the agent
TRANSITIONS_ROOT = REPO_ROOT / "data" / "transitions"


class EnvironmentDegraded(RuntimeError):
    """reset() could not restore a healthy golden cluster even after a hard reset (§5.9 step 4)."""


@dataclass
class EpisodeState:
    episode: int
    plan: FaultPlan
    k: int = 0
    raw: RawTick | None = None
    features: TickFeatures | None = None
    impute_state: ImputeState = field(default_factory=ImputeState)
    mask: np.ndarray = field(default_factory=lambda: np.zeros(N_ACTIONS, dtype=np.int8))
    obs: np.ndarray = field(default_factory=lambda: np.zeros(OBS_DIM, dtype=np.float32))
    lock: Lock | None = None
    last_action_tick: int | None = None
    health: list[bool] = field(default_factory=list)
    cured: list[bool] = field(default_factory=list)
    stale_run: int = 0
    t_inject_wall: float | None = None
    k_inject: int | None = None
    recovered_at: int | None = None
    done: bool = False


class BoutiqueEnv(gym.Env):
    metadata: ClassVar[dict[str, Any]] = {"render_modes": []}

    def __init__(self, *, contract: Contract, calibration: Calibration, golden: Mapping[str, GoldenDeployment],
                 limits: Mapping[str, Limits], agent_api: client.ApiClient, controller_api: client.ApiClient,
                 prom_url: str, locust_url: str, run: str, seed: int, events: EventLog,
                 faults_enabled: bool = True, transitions_root: Path = TRANSITIONS_ROOT) -> None:
        super().__init__()
        self.c, self.cal, self.golden, self.limits = contract, calibration, golden, limits
        self.run, self.events, self.faults_enabled = run, events, faults_enabled
        self.transitions_root = transitions_root
        self.observation_space = gym.spaces.Dict({
            "obs": gym.spaces.Box(-1.0, 1.0, (OBS_DIM,), np.float32),
            "mask": gym.spaces.MultiBinary(N_ACTIONS),
        })
        self.action_space = gym.spaces.Discrete(N_ACTIONS)
        self.clock = TickClock(contract.clock.tick_s, contract.clock.late_frac)
        self.collector = Collector(contract, prom_url, locust_url, agent_api, events)
        self.executor = ActionExecutor(contract, agent_api, golden, events)           # step(): agent client
        self.reset_executor = ActionExecutor(contract, controller_api, golden, events)  # reset(): controller
        self.injector = Injector(contract, controller_api, locust_url, calibration.u_base, run, events)
        self.ctrl_apps = client.AppsV1Api(controller_api)
        self.ctrl_core = client.CoreV1Api(controller_api)
        self.prom_url, self.locust_url = prom_url, locust_url
        self.rng = random.Random(seed)
        self.dispatch_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="dispatch")
        self.ep: EpisodeState | None = None
        self.episode_count = 0
        self.recorder: TransitionRecorder | None = None
        self.source = "policy"              # tag for transitions (runbook | eps | policy)
        self.schedule_plan: FaultPlan | None = None

    # ========================================================================= reset (§5.9)

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None
              ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        super().reset(seed=seed)
        if seed is not None:
            self.rng.seed(seed)
        if self.recorder is not None:
            self.recorder.close()
        t0 = time.monotonic()
        # 1. remove faults
        if not self.injector.remove_all_faults():
            self.events.emit("reset_faults_not_cleared", component="env", tick=None)
        # 2. restore golden (template + base replicas) for all four managed deployments. These are
        #    reset-only RESTOREs (id -1): the catalog has no RESTORE frontend for the agent.
        for d in self.c.cluster.managed:
            res = self.reset_executor.execute(Action(RESET_RESTORE_ID, RESTORE, d))
            if not res.ok:
                self.events.emit("reset_restore_failed", component="env", tick=None, deployment=d,
                                 error_type=res.error_type)
        # 3–4. converge, escalating to a hard reset once
        if not self._wait_converged(RESET_DEADLINE_S):
            self.events.emit("reset_hard", component="env", tick=None)
            self._delete_all_pods()
            if not self._wait_converged(RESET_DEADLINE_S):
                raise EnvironmentDegraded("cluster did not converge to golden after a hard reset")
        # 5. settle: >= 30 s after the last pod change, then 3 consecutive healthy ticks
        self._wait_pods_settled()
        last_raw, last_features = self._settle_healthy_ticks()
        # 6. start the episode
        self.episode_count += 1
        plan = (options or {}).get("plan") or self.schedule_plan
        if plan is None:
            plan = sample_plan(self.rng, self.c) if self.faults_enabled else FaultPlan("NULL", None, None, 0)
        self.ep = EpisodeState(self.episode_count, plan)
        self.ep.raw, self.ep.features = last_raw, last_features
        self.injector.arm(plan, self.episode_count)
        self.recorder = TransitionRecorder(self.transitions_root, self.run, self.episode_count)
        self.clock.anchor()
        obs = build_obs(last_features, None, contract=self.c, limits=self.limits, l_sla_ms=self.cal.l_sla_ms,
                        rps_base=self.cal.rps_base, in_flight=False, ticks_since_action=None)
        self.ep.mask = compute_mask(self._spec(last_features), lock_held=False, contract=self.c)
        self.ep.obs = obs
        info = {"episode": self.episode_count, "reset_s": time.monotonic() - t0, **self._truth()}
        self.events.emit("reset_done", component="env", tick=0, **info)
        return {"obs": obs, "mask": self.ep.mask.copy()}, info

    def _list_all_deployments(self) -> list[Any] | None:
        try:
            return self.ctrl_apps.list_namespaced_deployment(self.c.cluster.namespace,
                                                             _request_timeout=self.c.telemetry.k8s_timeout_s).items
        except ApiException as exc:
            self.events.emit("reset_read_failed", component="env", error_type=f"ApiException:{exc.status}", tick=None)
        except urllib3.exceptions.HTTPError as exc:
            self.events.emit("reset_read_failed", component="env", error_type=type(exc).__name__, tick=None)
        return None

    def _converged_now(self) -> bool:
        deps = self._list_all_deployments()
        if not deps:
            return False
        for dep in deps:
            st = DeploymentStatus(int(dep.spec.replicas or 0), int(dep.status.replicas or 0),
                                  int(dep.status.available_replicas or 0), int(dep.status.updated_replicas or 0),
                                  int(dep.metadata.generation or 0), int(dep.status.observed_generation or 0))
            if not rollout_complete(st):
                return False
        now = time.time()
        prom_ok = fetch_prom(self.prom_url, "ping", PROM_PING_QUERY, now, self.c.telemetry.prom_timeout_s, ()).ok
        loc = fetch_locust(self.locust_url, now - self.c.clock.tick_s, now, self.c.telemetry.locust_timeout_s)
        return prom_ok and loc.ok and loc.n > 0

    def _wait_converged(self, deadline_s: float) -> bool:
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self._converged_now():
                return True
            time.sleep(RESET_POLL_S)
        return False

    def _delete_all_pods(self) -> None:
        try:
            pods = self.ctrl_core.list_namespaced_pod(self.c.cluster.namespace,
                                                      _request_timeout=self.c.telemetry.k8s_timeout_s).items
            for p in pods:
                self.ctrl_core.delete_namespaced_pod(p.metadata.name, self.c.cluster.namespace,
                                                     _request_timeout=self.c.telemetry.k8s_timeout_s)
        except ApiException as exc:
            self.events.emit("hard_reset_failed", component="env", error_type=f"ApiException:{exc.status}", tick=None)
        except urllib3.exceptions.HTTPError as exc:
            self.events.emit("hard_reset_failed", component="env", error_type=type(exc).__name__, tick=None)

    def _last_pod_change(self) -> float | None:
        try:
            pods = self.ctrl_core.list_namespaced_pod(self.c.cluster.namespace,
                                                      _request_timeout=self.c.telemetry.k8s_timeout_s).items
        except ApiException as exc:
            self.events.emit("reset_read_failed", component="env", error_type=f"ApiException:{exc.status}", tick=None)
            return None
        except urllib3.exceptions.HTTPError as exc:
            self.events.emit("reset_read_failed", component="env", error_type=type(exc).__name__, tick=None)
            return None
        times = []
        for p in pods:
            times.append(p.metadata.creation_timestamp.timestamp())
            for cond in p.status.conditions or []:
                if cond.last_transition_time is not None:
                    times.append(cond.last_transition_time.timestamp())
        return max(times) if times else None

    def _wait_pods_settled(self) -> None:
        end = time.monotonic() + RESET_DEADLINE_S
        while time.monotonic() < end:
            last = self._last_pod_change()
            if last is not None:
                wait = SETTLE_AFTER_POD_CHANGE_S - (time.time() - last)
                if wait <= 0:
                    return
                time.sleep(min(wait, RESET_POLL_S))
            else:
                time.sleep(RESET_POLL_S)
        raise EnvironmentDegraded("pods did not settle within the reset deadline")

    def _settle_healthy_ticks(self) -> tuple[RawTick, TickFeatures]:
        """Run real ticks until `recovery_ticks` consecutive healthy, non-stale ones (§5.9 step 5)."""
        need = self.c.sla.recovery_ticks
        max_ticks = int(RESET_DEADLINE_S // self.c.clock.tick_s)
        state, streak = ImputeState(), 0
        self.clock.anchor()
        for k in range(max_ticks):
            self.clock.wait_boundary(k + 1)
            raw = self.collector.collect(k, self.clock.wall(k), self.clock.wall(k + 1))
            features, state = impute(raw, state, self.c)
            ok = not features.stale and healthy(features.p99_ms, features.fail_ratio, self.cal.l_sla_ms, self.c)
            streak = streak + 1 if ok else 0
            if streak >= need:
                return raw, features
        raise EnvironmentDegraded(f"no {need} consecutive healthy ticks within {max_ticks} settle ticks")

    # ========================================================================= step (§5.7)

    def step(self, action: int) -> tuple[dict[str, np.ndarray], float, bool, bool, dict[str, Any]]:
        ep = self.ep
        if ep is None or ep.done:
            raise RuntimeError("step() called before reset() or after the episode ended")
        t_enter = self.clock.now()
        k = ep.k
        a_chosen = int(action)
        valid, missed = True, False
        if self.clock.missed(t_enter, k):                       # missed the tick entirely
            missed, valid = True, False
            k_new = self.clock.next_boundary_index(t_enter) - 1
            for _skipped in range(k, k_new):                    # unobserved ticks count as not healthy
                ep.health.append(False)
                ep.cured.append(ep.cured[-1] if ep.cured else False)
            self.events.emit("tick_missed", component="env", tick=k, realigned_to=k_new)
            k = ep.k = k_new
        late = self.clock.is_late(t_enter, k)
        a = a_chosen
        if missed or not (0 <= a < N_ACTIONS) or not ep.mask[a]:
            if not missed:
                self.events.emit("mask_violation", component="env", tick=k, action=a)
            a = 0
        decision_latency_s = t_enter - self.clock.boundary(k)
        future: concurrent.futures.Future[ExecResult] | None = None
        if a != 0 and ep.lock is None:
            act = CATALOG[a]
            st = ep.raw.deployments.items.get(act.target) if ep.raw is not None and ep.raw.deployments.ok else None
            ep.lock = Lock(act, str(act.target), self.clock.wall_now(), self.clock.now(),
                           st.generation if st else None)
            future = self.dispatch_pool.submit(self.executor.execute, act, k)
            ep.last_action_tick = k
        inject = self.injector.on_tick(k)
        if inject is not None and inject.ok and inject.t_inject_wall is not None:
            ep.t_inject_wall, ep.k_inject = inject.t_inject_wall, k
        self.clock.wait_boundary(k + 1)
        raw = self.collector.collect(k, self.clock.wall(k), self.clock.wall(k + 1))

        # resolve the dispatch (it had a whole tick; not done now counts as a timeout)
        exec_error, a_exec = False, a
        if future is not None:
            res = future.result() if future.done() else ExecResult(False, a, False, error_type="dispatch_timeout")
            if not res.ok:
                exec_error, a_exec, ep.lock = True, 0, None
                self.events.emit("exec_error", component="env", tick=k, error_type=res.error_type, action=a)
            elif CATALOG[a].kind == RESTORE and not res.api_called and ep.lock is not None:
                ep.lock = dataclasses.replace(ep.lock, no_call=True)
        # in-flight lock (§5.8)
        if ep.lock is not None:
            st = raw.deployments.items.get(ep.lock.target) if raw.deployments.ok else None
            why = lock_should_clear(ep.lock, st, self.clock.now(), self.c)
            if why is not None:
                self.events.emit("lock_released", component="env", tick=k, reason=why, action=ep.lock.action.id)
                ep.lock = None
        cured = self.injector.check_cure(raw, k)

        features, ep.impute_state = impute(raw, ep.impute_state, self.c)
        prev = ep.features
        ticks_since_action = None if ep.last_action_tick is None else k + 1 - ep.last_action_tick
        obs = build_obs(features, prev, contract=self.c, limits=self.limits, l_sla_ms=self.cal.l_sla_ms,
                        rps_base=self.cal.rps_base, in_flight=ep.lock is not None,
                        ticks_since_action=ticks_since_action)
        v = sla_violation(features.p99_ms, features.fail_ratio, self.cal.l_sla_ms, self.c)
        rho = replica_surplus(self._spec(features), self.c)
        r = reward(v, CATALOG[a_exec].kind, rho, self.c)

        ep.health.append(healthy(features.p99_ms, features.fail_ratio, self.cal.l_sla_ms, self.c))
        ep.cured.append(cured)
        ep.stale_run = ep.stale_run + 1 if features.stale else 0
        terminated, truncated, mttr_s = self._done_flags(k)
        if ep.stale_run >= self.c.telemetry.stale_truncate_ticks:
            truncated, valid = True, False
        mask_next = compute_mask(self._spec(features), lock_held=ep.lock is not None, contract=self.c)

        info = {
            "tick": k, "phase": self._phase(k), **self._truth(),
            "a_chosen": a_chosen, "a_exec": a_exec, "exec_error": exec_error, "late": late, "valid": valid,
            "stale": features.stale, "decision_latency_s": decision_latency_s, "inflight": ep.lock is not None,
            "cured": cured, "recovered": ep.recovered_at is not None, "mttr_s": mttr_s,
        }
        assert self.recorder is not None
        self.recorder.append({
            "run": self.run, "episode": ep.episode, "tick": k, "t_wall": self.clock.wall(k + 1),
            "phase": info["phase"], "fault": ep.plan.fault, "target": ep.plan.target, "severity": ep.plan.severity,
            "source": self.source, "obs": ep.obs,
            "mask": ep.mask, "a_chosen": a_chosen, "a_exec": a_exec, "reward": r, "next_obs": obs,
            "next_mask": mask_next, "terminated": terminated, "truncated": truncated, "valid": valid,
            "stale": features.stale, "raw": {"locust": dataclasses.asdict(raw.locust), "features": features_to_dict(features)},
            "decision_latency_s": decision_latency_s, "exec_error": exec_error, "late": late,
        })
        ep.raw, ep.features, ep.mask, ep.obs, ep.k = raw, features, mask_next, obs, k + 1
        ep.done = terminated or truncated
        return {"obs": obs, "mask": mask_next.copy()}, r, terminated, truncated, info

    # ------------------------------------------------------------------------- helpers

    def _spec(self, f: TickFeatures) -> dict[str, int]:
        return {d: round(s.spec_replicas) for d, s in f.services.items()}

    def _truth(self) -> dict[str, Any]:
        """Ground truth for info/logs only (never obs or mask, §4.1.4)."""
        p = self.ep.plan if self.ep else None
        return {"fault": p.fault if p else None, "target": p.target if p else None,
                "severity": p.severity if p else None, "lead_in": p.lead_in if p else None}

    def _phase(self, k: int) -> str:
        assert self.ep is not None
        if self.ep.k_inject is None or k < self.ep.k_inject:
            return "lead_in"
        return "post" if self.ep.recovered_at is not None else "fault"

    def _done_flags(self, k: int) -> tuple[bool, bool, float | None]:
        """terminated (recovered), truncated (fault_max_ticks / NULL horizon), MTTR (§5.6)."""
        ep = self.ep
        assert ep is not None
        e = self.c.episode
        if ep.plan.fault == "NULL":
            return False, k >= ep.plan.lead_in + e.null_extra_ticks, None
        if ep.k_inject is None:
            return False, False, None
        t = recovery_start(ep.health, ep.cured, ep.k_inject, self.c)
        if t is not None and k >= t + self.c.sla.recovery_ticks - 1:
            ep.recovered_at = t
            return True, False, self.clock.wall(t) - (ep.t_inject_wall or self.clock.wall(t))
        return False, k - ep.k_inject >= e.fault_max_ticks, None

    def close(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
        self.collector.close()
        self.dispatch_pool.shutdown(wait=True)
        super().close()
