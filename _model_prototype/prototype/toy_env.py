"""SYNTHETIC toy fault model — prototype use only.

!!! This is NOT the cluster. CLAUDE.md §4.1 forbids using a simulator for the
!!! project's training or evaluation results. This module exists only to
!!! exercise the agent code end-to-end and to produce clearly labelled
!!! illustrative numbers. It lives outside env/, agents/ and eval/ on purpose.

It reproduces the *interface* of env/boutique_env.py (36-dim obs, 12-action
mask, contract reward, 20 s ticks, recovery/termination rules, in-flight lock)
with hand-written, approximate dynamics loosely based on numbers measured in
M2–M3 (CAPSTONE_DEFENSE_NOTES.md): frontend ~60 % of its 400m limit at
U_base, baseline throttle ~0.11, rare whole-process cartservice pauses that
make ~1 % of healthy ticks breach the SLA.
"""
from __future__ import annotations

import math

import numpy as np

from .contract import ACTIONS, CONTRACT, MANAGED, SCALE_DELTA
from .core import (BASE, L_SLA, MAX, RPS_BASE, build_obs, compute_mask, healthy, reward,
                   sla_violation)

DATA_ORIGIN = "SYNTHETIC_TOY_SIMULATOR"
EP = CONTRACT["episode"]
TICK_S = CONTRACT["clock"]["tick_s"]

LIMIT_CORES = {"frontend": 0.4, "cartservice": 0.3, "currencyservice": 0.3,
               "productcatalogservice": 0.2}
BASE_DEMAND = {"frontend": 0.24, "cartservice": 0.09, "currencyservice": 0.12,
               "productcatalogservice": 0.08}
SEV_MS = {"300ms": 300.0, "600ms": 600.0, "1s": 1000.0}
P_FAST_ROLLOUT = {"frontend": 0.65, "cartservice": 0.4, "currencyservice": 0.65,
                  "productcatalogservice": 0.65}
HPA_TARGET = 0.70          # modelled as 70 % of the CPU *limit* (see REPORT.md caveat)
HPA_DOWN_STABILISE = 15    # 300 s
HPA_LAG_TICKS = 2          # metrics-server window + HPA sync + decision (~40 s, assumption)


def sample_plan(rng: np.random.Generator) -> dict:
    names = list(EP["fault_probs"])
    fault = str(rng.choice(names, p=[EP["fault_probs"][n] for n in names]))
    L = int(rng.integers(EP["lead_in_ticks"][0], EP["lead_in_ticks"][1] + 1))
    plan = {"fault": fault, "target": None, "severity": None, "L": L}
    if fault == "F1":
        plan.update(target="productcatalogservice", severity=str(rng.choice(EP["f1_latency"])))
    elif fault == "F2":
        plan.update(target=str(rng.choice(EP["f2_targets"])), severity=int(rng.choice(EP["f2_workers"])))
    elif fault == "F3":
        plan.update(target=str(rng.choice(EP["f3_targets"])))
    elif fault == "F4":
        plan.update(target="frontend", severity=float(rng.choice(EP["f4_multiplier"])))
    return plan


class ToyBoutiqueEnv:
    def __init__(self, seed: int, hpa: bool = False):
        self.rng = np.random.default_rng(seed)
        self.hpa = hpa

    # ------------------------------------------------------------------ reset
    def reset(self, plan: dict | None = None):
        self.plan = plan or sample_plan(self.rng)
        self.k = 0
        self.spec = dict(BASE)
        self.avail = dict(BASE)
        self.pending: list[dict] = []
        self.lock_until = -1               # lock held for windows < lock_until
        self.f1_active = self.f2_active = False
        self.f4_mult = 1.0
        self.gen_tick: dict[str, int | None] = {d: None for d in MANAGED}
        self.last_action_k = None
        self.health: list[bool] = []
        self.stale_run = 0
        self.hpa_low = {d: 0 for d in MANAGED}
        self.prev_util = {d: BASE_DEMAND[d] / LIMIT_CORES[d] for d in MANAGED}
        self.util_hist = [dict(self.prev_util)] * HPA_LAG_TICKS
        self.last_raw = None
        self.prev_feat = None
        self.mttr_s = None
        raw = self._physics(settle=True)
        obs = build_obs(raw, None)
        self.prev_feat = {"p99f": float(obs[0]), "fail": float(obs[1])}
        self.mask = compute_mask(self.spec, False)
        return obs, self.mask.copy(), {"plan": dict(self.plan), "data_origin": DATA_ORIGIN}

    # ------------------------------------------------------------- mechanics
    def _rollout_ticks(self, d: str) -> int:
        return 1 if self.rng.random() < P_FAST_ROLLOUT[d] else 2

    def _dispatch(self, a: int):
        kind, d, label = ACTIONS[a]
        k = self.k
        if kind == "RESTART":
            D = self._rollout_ticks(d)
            self.pending.append({"at": k + D, "d": d, "op": "restart"})
            self.gen_tick[d] = k
        elif kind == "SCALE":
            n = self.spec[d] + SCALE_DELTA[a]
            if not (1 <= n <= MAX[d]):
                return 0, 1                                     # executor refuses
            self.spec[d] = n
            self.gen_tick[d] = k
            if SCALE_DELTA[a] > 0:
                D = self._rollout_ticks(d)
                self.pending.append({"at": k + D, "d": d, "op": "pod_up"})
            else:
                self.avail[d] = min(self.avail[d], n)
                D = 1
        else:  # RESTORE: JSON patch to golden template + base replicas
            template_drift = d == "productcatalogservice" and self.f1_active
            if not template_drift and self.spec[d] == BASE[d]:
                return a, 1                                     # no API call, lock 1 tick
            self.gen_tick[d] = k
            D = self._rollout_ticks(d)
            if template_drift:
                self.pending.append({"at": k + D, "d": d, "op": "restore_template"})
            if self.spec[d] < BASE[d]:
                self.pending.append({"at": k + D, "d": d, "op": "pod_up"})
            elif self.spec[d] > BASE[d]:
                self.avail[d] = min(self.avail[d], BASE[d])
            self.spec[d] = BASE[d]
        return a, D

    def _apply_pending(self):
        keep = []
        for ev in self.pending:
            if ev["at"] > self.k:
                keep.append(ev)
                continue
            d = ev["d"]
            if ev["op"] == "restart":
                if self.f2_active and self.plan["target"] == d:
                    self.f2_active = False                      # stressed pod UID gone -> CR deleted
                # restarting pc keeps EXTRA_LATENCY (it is in the template)
            elif ev["op"] == "pod_up":
                self.avail[d] = min(self.spec[d], self.avail[d] + 1)
            elif ev["op"] == "restore_template":
                self.f1_active = False
        self.pending = keep

    def _inject(self):
        p = self.plan
        if p["fault"] == "F1":
            self.f1_active = True
            self.gen_tick["productcatalogservice"] = self.k
        elif p["fault"] == "F2":
            self.f2_active = True
        elif p["fault"] == "F3":
            d = p["target"]
            self.spec[d] = self.avail[d] = 0
            self.pending = [e for e in self.pending if e["d"] != d]
            self.gen_tick[d] = self.k
        elif p["fault"] == "F4":
            self.f4_mult = float(p["severity"])

    def _hpa(self):
        for d in ("frontend", "cartservice", "currencyservice"):
            if self.spec[d] == 0:
                continue                                         # HPA ignores zero replicas
            seen = self.util_hist[-HPA_LAG_TICKS][d]
            desired = math.ceil(self.spec[d] * seen / HPA_TARGET - 1e-9)
            desired = int(np.clip(desired, 1, MAX[d]))
            if desired > self.spec[d]:
                for _ in range(desired - self.spec[d]):
                    self.pending.append({"at": self.k + self._rollout_ticks(d), "d": d, "op": "pod_up"})
                self.spec[d] = desired
                self.gen_tick[d] = self.k
                self.hpa_low[d] = 0
            elif desired < self.spec[d]:
                self.hpa_low[d] += 1
                if self.hpa_low[d] >= HPA_DOWN_STABILISE:
                    self.spec[d] -= 1
                    self.avail[d] = min(self.avail[d], self.spec[d])
                    self.gen_tick[d] = self.k
                    self.hpa_low[d] = 0
            else:
                self.hpa_low[d] = 0

    # ---------------------------------------------------------------- physics
    def _physics(self, settle: bool = False) -> dict:
        r = self.rng
        load = self.f4_mult
        restarting = {e["d"] for e in self.pending if e["op"] in ("restart", "restore_template")}
        svc, util_lim = {}, {}
        lat = 1.0
        fail = 0.002

        # frontend: CPU-quota bound, saturates under surge
        cap = LIMIT_CORES["frontend"] * max(self.avail["frontend"], 0)
        if "frontend" in restarting:
            cap *= 0.8
        dem = BASE_DEMAND["frontend"] * load * r.normal(1, 0.05)
        u = dem / max(cap, 1e-6) if cap > 0 else 9.9
        thr_fe = np.clip(0.10 + 1.8 * max(0.0, u - 0.55) + r.normal(0, 0.02), 0, 0.97)
        lat *= 1 + 6 * max(0.0, u - 0.75) ** 1.2
        fail += np.clip(0.15 * (u - 1.3), 0, 0.3)
        svc["frontend"] = (min(dem, cap), thr_fe)

        for d in ("cartservice", "currencyservice", "productcatalogservice"):
            n = self.avail[d]
            if n == 0:
                svc[d] = (0.0, 0.0)
                fail += 0.55 if d == "cartservice" else (0.65 if d == "currencyservice" else 0.9)
                continue
            dem = BASE_DEMAND[d] * min(load, 1.5) * r.normal(1, 0.06)
            per = dem / n
            base_thr = 0.11 if d == "productcatalogservice" else 0.06
            thr_pods = [np.clip(base_thr + 1.5 * max(0.0, per / LIMIT_CORES[d] - 0.6), 0, 0.97)] * n
            use = dem
            if self.f2_active and self.plan["target"] == d:
                w = int(self.plan["severity"])
                thr_pods[0] = min(0.97, 0.52 + 0.15 * w + r.normal(0, 0.03))
                use = dem - per + LIMIT_CORES[d]
                slow = (1.0 + 0.9 * w) * (1.0 if n == 1 else 0.55)
                lat *= 1 + slow
            thr = float(np.mean(thr_pods) + r.normal(0, 0.015))
            svc[d] = (use, thr)
        if self.f1_active:
            f1_add = SEV_MS[self.plan["severity"]] * 1.3
        else:
            f1_add = 0.0
        if restarting:
            lat *= 1.08

        base_p99 = 0.45 * L_SLA * math.exp(r.normal(0, 0.2))
        if r.random() < 0.025:                                   # cartservice process pause
            base_p99 *= r.uniform(1.6, 2.6)
        p99 = base_p99 * lat + f1_add * math.exp(r.normal(0, 0.1))
        fail = float(np.clip(fail, 0, 0.95))
        rps = RPS_BASE * load / (1 + 0.08 * (lat - 1)) * r.normal(1, 0.03)
        nreq = max(int(rps * TICK_S), 1)
        fail = r.binomial(nreq, fail) / nreq
        if fail > 0.3:
            p99 = max(p99, 0.9 * L_SLA)

        out_svc = {}
        for d in MANAGED:
            use, thr = svc[d]
            reps = max(1, self.spec[d])
            util_lim[d] = use / (LIMIT_CORES[d] * reps)
            tsg = None if self.gen_tick[d] is None else self.k - self.gen_tick[d]
            out_svc[d] = {"cpu_util": util_lim[d] if self.avail[d] else 0.0,
                          "throttle": thr, "mem_util": (r.uniform(0.35, 0.6) if self.avail[d] else 0.0),
                          "available": self.avail[d], "spec": self.spec[d], "restart_delta": 0,
                          "ticks_since_gen": tsg}
        self.prev_util = util_lim
        if not settle:
            self.util_hist = (self.util_hist + [util_lim])[-HPA_LAG_TICKS:]
        stale = (not settle) and r.random() < 0.005
        if stale and self.last_raw is not None:                  # LOCF for Prometheus features
            for d in MANAGED:
                for f in ("cpu_util", "throttle", "mem_util"):
                    out_svc[d][f] = self.last_raw["svc"][d][f]
        raw = {"p99_ms": float(p99), "fail": float(fail), "rps": float(rps), "svc": out_svc,
               "in_flight": False, "ticks_since_action": None, "stale": bool(stale)}
        self.last_raw = raw
        return raw

    def _cured(self) -> bool:
        f, d = self.plan["fault"], self.plan["target"]
        if f == "F1":
            return not self.f1_active and not any(e["d"] == d for e in self.pending)
        if f == "F2":
            return not self.f2_active
        if f == "F3":
            return self.avail[d] >= 1
        return True

    # ------------------------------------------------------------------- step
    def step(self, a_chosen: int):
        k, L = self.k, self.plan["L"]
        a = a_chosen if self.mask[a_chosen] else 0
        a_exec, D = 0, 0
        if a != 0:
            a_exec, D = self._dispatch(a)
            if a_exec != 0:
                self.lock_until = k + D
                self.last_action_k = k
        if k == L and self.plan["fault"] != "NULL":
            self._inject()
        self._apply_pending()
        if self.hpa:
            self._hpa()
        raw = self._physics()
        self.k += 1
        lock_held = self.k < self.lock_until
        raw["in_flight"] = lock_held
        raw["ticks_since_action"] = None if self.last_action_k is None else self.k - 1 - self.last_action_k
        obs = build_obs(raw, self.prev_feat)
        self.prev_feat = {"p99f": float(obs[0]), "fail": float(obs[1])}
        r = reward(raw["p99_ms"], raw["fail"], self.spec, a_exec)
        H = healthy(raw["p99_ms"], raw["fail"])
        self.health.append(H)
        self.stale_run = self.stale_run + 1 if raw["stale"] else 0

        terminated = truncated = False
        valid = True
        recovered = False
        if self.plan["fault"] != "NULL" and k >= L + 3:
            t = k - 2
            if t > L and all(self.health[t:k + 1]) and self._cured():
                terminated = recovered = True
                self.mttr_s = (t - L) * TICK_S
        if not terminated:
            if self.plan["fault"] == "NULL" and k >= L + EP["null_extra_ticks"]:
                truncated = True
            elif self.plan["fault"] != "NULL" and k >= L + EP["fault_max_ticks"]:
                truncated = True
            if self.stale_run >= CONTRACT["telemetry"]["stale_truncate_ticks"]:
                truncated, valid = True, False
        self.mask = compute_mask(self.spec, lock_held)
        phase = "lead_in" if k < L else ("fault" if not recovered else "post")
        info = {"tick": k, "phase": phase, "fault": self.plan["fault"], "target": self.plan["target"],
                "severity": self.plan["severity"], "a_chosen": int(a_chosen), "a_exec": int(a_exec),
                "valid": valid, "stale": raw["stale"], "inflight": lock_held, "healthy": H,
                "v": sla_violation(raw["p99_ms"], raw["fail"]), "cured": self._cured(),
                "recovered": recovered, "mttr_s": self.mttr_s, "raw_p99_ms": raw["p99_ms"],
                "raw_fail": raw["fail"], "raw_rps": raw["rps"], "spec": dict(self.spec),
                "data_origin": DATA_ORIGIN}
        return obs, self.mask.copy(), r, terminated, truncated, info
