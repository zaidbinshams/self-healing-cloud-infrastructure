# Code Explainer

This document walks through the source code folder by folder. For each major file it shows the most important lines, copied from the source, and explains what they do and why they are written that way. `docs/PROJECT_EXPLAINER.md` covers the ideas (problem, design, results). This file covers the code. Setup, diagnostic and figure scripts that are not explained here are listed, one line each, in `docs/REPO_GUIDE.md`.

Line numbers drift as code changes, so snippets are identified by function name. Search for the name (`def step`, `def masked_policy`) to find the full code.

## Contents

1. [Python, PyTorch and Kubernetes syntax used throughout](#1-python-pytorch-and-kubernetes-syntax-used-throughout)
2. [Map of the code](#2-map-of-the-code)
3. [One tick, end to end](#3-one-tick-end-to-end)
4. [`config/` and `env/contract.py`: locked parameters](#4-config-and-envcontractpy-locked-parameters)
5. [`env/clock.py`: the tick schedule](#5-envclockpy-the-tick-schedule)
6. [`env/telemetry.py`: measuring the cluster](#6-envtelemetrypy-measuring-the-cluster)
7. [`env/k8s_actions.py`: actions, mask, lock, executor](#7-envk8s_actionspy-actions-mask-lock-executor)
8. [`env/golden.py`: the known-good state](#8-envgoldenpy-the-known-good-state)
9. [`env/injector.py`: faults](#9-envinjectorpy-faults)
10. [`env/reward.py`: reward, health, recovery](#10-envrewardpy-reward-health-recovery)
11. [`env/boutique_env.py`: the environment](#11-envboutique_envpy-the-environment)
12. [`env/recorder.py`: logs](#12-envrecorderpy-logs)
13. [`agents/masked_discrete_sac.py`: the agent](#13-agentsmasked_discrete_sacpy-the-agent)
14. [`agents/per_buffer.py`: n-step returns and prioritized replay](#14-agentsper_bufferpy-n-step-returns-and-prioritized-replay)
15. [`agents/warmstart.py` and `agents/train.py`: training](#15-agentswarmstartpy-and-agentstrainpy-training)
16. [`agents/runbook.py`: the scripted baseline](#16-agentsrunbookpy-the-scripted-baseline)
17. [`eval/`: evaluation and statistics](#17-eval-evaluation-and-statistics)
18. [`locust/locustfile.py`: the load generator](#18-locustlocustfilepy-the-load-generator)
19. [`scripts/`: operations](#19-scripts-operations)
20. [`tests/`](#20-tests)
21. [Where to look for…](#21-where-to-look-for)

---

## 1. Python, PyTorch and Kubernetes syntax used throughout

Read this first if some of the notation in the snippets is unfamiliar.

### Python

| Syntax | Meaning | Where it matters here |
|---|---|---|
| `from __future__ import annotations` | Type hints are stored as strings and not evaluated at import time | Top of every module; lets `X \| None` work on older Pythons |
| `def f(x: float, *, contract: Contract) -> float:` | Type hints. Everything after `*` must be passed by name (`f(1.0, contract=c)`) | Keyword-only arguments prevent swapping two numbers by mistake |
| `int \| None` | "an int or None" | E.g. `ticks_since_action` is None before the first action |
| `@dataclass(frozen=True)` | Generates `__init__`, `__eq__`, `__repr__`; `frozen` makes instances read-only | Every config and result type. A loaded contract cannot be changed by accident |
| `dataclasses.replace(obj, field=v)` | Copy of a frozen dataclass with one field changed | Updating the lock (`no_call=True`) without mutating it |
| `tuple[bool, bool, float \| None]` | A function returning three values | `_done_flags` returns (terminated, truncated, mttr) |
| `a, b = b, a` / `x, y = f()` | Tuple unpacking | Used for multi-value returns |
| `lambda m=m: fetch_prom(...)` | An anonymous function. The `m=m` default captures the current value of `m` | Without it, all lambdas in the loop would see the last `m` (a classic Python bug) |
| `{**a, "k": v}` | A new dict with the entries of `a` plus `k` | Building log records and result rows |
| `f"{x:.2f}"` | Formatted string; `.2f` = two decimals | Progress lines |
| `deque(maxlen=n)` | A list that drops its oldest entry when full | Runbook memory of the last n throttle values |
| `concurrent.futures.ThreadPoolExecutor` / `submit` / `wait(timeout=)` | Run functions on worker threads; wait up to a deadline | Telemetry collection (3 s deadline) and asynchronous action dispatch |
| `try: ... except ApiException as exc:` | Catch a specific exception type | Every remote call. Never a bare `except:` |
| `raise X from exc` | Re-raise with the original error attached | Contract loading |

### NumPy and PyTorch

| Syntax | Meaning |
|---|---|
| `np.zeros(36, dtype=np.float32)` | Array of 36 zeros, 32-bit floats (the observation) |
| `np.all(np.isfinite(obs))` | True if no entry is NaN or ±inf |
| `torch.where(cond, a, b)` | Element-wise: `a` where `cond` is true, else `b`. Used for masking |
| `torch.log_softmax(x, dim=-1)` | log of softmax over the last axis (the 12 actions); numerically stable |
| `q.gather(-1, act)` | Pick, for each row, the column given by `act`: Q(s, a) for the action actually taken |
| `x.unsqueeze(-1)` / `squeeze(-1)` | Add or remove a size-1 axis so shapes line up for `gather` |
| `torch.logsumexp(x, dim=-1)` | log Σ exp(x), stable; a "soft maximum" (CQL penalty) |
| `with torch.no_grad():` | Do not record operations for gradients (target computation) |
| `loss.backward()` then `opt.step()` | Compute gradients, then update weights |
| `x.detach()` | Treat `x` as a constant: no gradient flows through it |

### Kubernetes Python client

| Call | Meaning |
|---|---|
| `apps.read_namespaced_deployment(name, ns)` | GET one Deployment |
| `apps.patch_namespaced_deployment(name, ns, body)` | PATCH it. A **dict** body is sent as a strategic-merge patch; a **list** body as a JSON Patch |
| `apps.patch_namespaced_deployment_scale(name, ns, {"spec": {"replicas": n}})` | Change the replica count only |
| `core.list_namespaced_pod(ns, label_selector="app=frontend")` | List pods with a label |
| `custom.create_namespaced_custom_object(group, version, ns, plural, body)` | Create a CRD object (Chaos Mesh `StressChaos`) |
| `_request_timeout=(2, 5)` | (connect, read) timeout in seconds. Every call has one |
| `ApiException` | Raised for HTTP errors from the API server (`exc.status` = 404, 409, …) |

---

## 2. Map of the code

| Folder | Role | Key files |
|---|---|---|
| `config/` | Locked numbers and generated snapshots | `contract.yaml`, `calibration.json`, `golden_live.json`, `runs/*.yaml` |
| `env/` | The real-cluster Gymnasium environment | `boutique_env.py` (loop), `telemetry.py`, `k8s_actions.py`, `injector.py`, `reward.py`, `clock.py`, `contract.py`, `golden.py`, `recorder.py` |
| `agents/` | The learner, the baseline, training | `masked_discrete_sac.py`, `per_buffer.py`, `warmstart.py`, `train.py`, `runbook.py`, `run_policy.py` |
| `eval/` | Evaluation runs and analysis | `run_eval.py`, `metrics.py`, `stats.py`, `analyze.py`, `plots.py`, `papera_export.py`, `paperb_*.py`, `schedules/` |
| `locust/` | Load generator with a `/tick` endpoint | `locustfile.py`, `run_locust.sh` |
| `k8s/`, `chaos/` | Cluster setup (bootstrap only, run by humans) | K3s install, Online Boutique manifests, RBAC, Prometheus, HPA baseline, Chaos Mesh |
| `scripts/` | Operations | `preflight.py`, `calibrate.py`, `fault_smoke.py`, `session_start.sh`, `watchdog.sh`, `chain_*.sh` |
| `tests/` | Unit tests of pure logic | one file per module, recorded fixtures in `tests/fixtures/` |

**Design rule used throughout:** pure functions (math, rules, normalisation) are separate from I/O (Kubernetes, Prometheus, Locust). The pure parts are unit-tested on recorded data; the I/O parts are thin and always return a typed result. In the files, sections are marked `# ---- (pure)` and `# ---- (I/O)`.

---

## 3. One tick, end to end

Every 20 s, Machine B runs the following. Each box is a section below.

```
agent.act(obs, mask)  ──►  env.step(a)                               [§13, §11]
                              │ mask check, lock, submit executor     [§7]
                              │ injector.on_tick(k)                    [§9]
                              │ clock.wait_boundary(k+1)               [§5]
                              │ collector.collect(window)  (≤ 3 s)     [§6]
                              │ resolve dispatch, lock_should_clear    [§7]
                              │ injector.check_cure                    [§9]
                              │ impute → build_obs                     [§6]
                              │ sla_violation, reward, health          [§10]
                              │ _done_flags, compute_mask, recorder    [§11, §12]
                              ▼
          (obs', mask'), r, terminated, truncated, info
                              │
  train.py: NStepBuilder.push → ReplayBuffer.add → 4 × agent.update   [§14, §15]
```

---

## 4. `config/` and `env/contract.py`: locked parameters

`config/contract.yaml` holds every number the code uses (tick length, timeouts, reward weights, SAC hyper-parameters, runbook thresholds). Code never contains such numbers directly. `env/contract.py` turns the YAML into nested frozen dataclasses (`Contract`, `ClockCfg`, `RlCfg`, …) and checks types and ranges. Any later change to a locked file needs a `CONTRACT-CHANGE:` commit.

**The calibration freshness check** (`load_calibration`). The SLA threshold `L_SLA` in `calibration.json` was measured with a particular version of the measurement code. If that code changes afterwards, the old threshold is no longer valid, so every entry point refuses to start:

```python
if check_fresh:
    latest_env = _git("log", "-1", "--format=%H", "--", *CALIBRATION_MEASUREMENT_PATH)
    if latest_env:
        is_ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", latest_env, cal.env_git_sha],
                                     cwd=REPO_ROOT, capture_output=True, check=False).returncode == 0
        _require(is_ancestor, f"calibration env_git_sha {cal.env_git_sha[:8]} is older than the latest "
                              ...)
```

- `git log -1 --format=%H -- <paths>` gives the last commit that touched the measurement path (`env/clock.py`, `env/telemetry.py`, `config/contract.yaml`).
- `git merge-base --is-ancestor A B` exits 0 if commit A is contained in B. So the check passes only if calibration was taken at or after that commit.
- **Why:** the threshold and the code that measures against it can never get out of step.

---

## 5. `env/clock.py`: the tick schedule

All scheduling uses `time.monotonic()` (never goes backwards, unaffected by NTP); all timestamps use `time.time()` (wall clock, for Prometheus and logs). `TickClock` keeps both.

```python
def boundary(self, k: int) -> float:
    """T_k on the monotonic clock."""
    return self.t0 + k * self.tick_s

def wait_boundary(self, k: int) -> Boundary:
    """Sleep until T_k and record wall(T_k)."""
    target = self.boundary(k)
    reached = self.sleep_until(target)
    self._walls[k] = self._wall()
    return Boundary(k, target, reached, self._walls[k])

def is_late(self, t_enter_mono_s: float, k: int) -> bool:
    """step() entered after T_k + late_frac * tick_s."""
    return t_enter_mono_s > self.boundary(k) + self.late_frac * self.tick_s

def missed(self, t_enter_mono_s: float, k: int) -> bool:
    """step() for tick k entered at or after T_{k+1}: the tick was missed entirely."""
    return t_enter_mono_s >= self.boundary(k + 1)
```

- Boundaries are computed as `t0 + k·tick_s`, not "last boundary + 20 s", so small delays never accumulate into drift.
- `wait_boundary` records the wall time **at the moment the boundary is reached**. That wall time defines the measurement window `(wall(T_k), wall(T_{k+1})]` used by every data source.
- `sleep_until` loops until the target is reached (a single `sleep` can wake early). The code never uses `time.sleep(20)`.
- The clock functions `_mono`, `_wall`, `_sleep` are injected, so tests can drive the clock with a fake time source.

---

## 6. `env/telemetry.py`: measuring the cluster

The largest module (~670 lines). It has four jobs: fetch raw data, collect all sources under one deadline, impute missing values, and build the 36-number observation.

### 6.1 Prometheus queries

`build_queries` produces the three PromQL queries from `CLAUDE.md` §5.4. Pods are grouped by deployment through a regex on the pod name, because every Online Boutique container is named `server`:

```
label_replace(x, "deployment", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$")
```

`frontend-7d9f8c6b5-x2k9q` → `deployment="frontend"`. `rate(...[30s])` is the shortest window that reliably contains enough cAdvisor samples (scrape 5 s, housekeeping 5 s).

`fetch_prom` sends `GET /api/v1/query` with `time=<B's boundary time>`, so Prometheus evaluates the query at the same instant B closed the window. It catches `requests.exceptions.RequestException` and parse errors and returns `PromResult(ok=False, error_type=...)`. It never returns `None` or raises into the loop.

### 6.2 Server-side latency

The SLA latency is the p95 of the frontend's own request times, read from its logs each tick. `server_window_latencies` keeps only `"request complete"` lines whose timestamp falls inside the tick window, skips the health-check path, and takes `http.resp.took_ms`. `fetch_server_latency` reads the logs of all frontend pods in parallel and computes the quantile with `_nearest_rank` (the value at rank ⌈q·n⌉ of the sorted list; no interpolation, so the reported number is an actual request time).

**Why server-side:** the client-side latency measured by Locust on B included the Wi-Fi round trip, which no remediation action can change.

### 6.3 Collecting every source under one deadline (`Collector.collect`)

```python
jobs: dict[str, Callable[[], Any]] = {
    "locust": lambda: fetch_locust(self.locust_url, t_from_wall, t_to_wall, tel.locust_timeout_s),
    **{m: (lambda m=m: fetch_prom(self.prom_url, m, self.queries[m], t_to_wall,
                                  tel.prom_timeout_s, managed, self._prom_http)) for m in PROM_METRICS},
    "deployments": lambda: fetch_deployments(self.apps, ns, managed, tel.k8s_timeout_s),
    "pods": lambda: fetch_pods(self.core, ns, managed, tel.k8s_timeout_s),
    "server": lambda: fetch_server_latency(...),
}
futures = {name: self._pool.submit(fn) for name, fn in jobs.items()}
concurrent.futures.wait(futures.values(), timeout=self.c.clock.collect_deadline_s)
for name, fut in futures.items():
    if fut.done():
        results[name] = fut.result()
    else:
        fut.cancel()
        results[name] = self._failure(name, t_to_wall, "deadline",
                                      f"no result within {self.c.clock.collect_deadline_s} s")
```

- Seven fetches (Locust, three Prometheus metrics, deployments, pods, server logs) run at the same time on a thread pool.
- `wait(timeout=3.0)` returns after 3 s even if some fetches are still running. Each unfinished fetch becomes a typed "deadline" failure.
- Every failure increments a counter and emits a structured `source_failed` event.
- **Why:** over a lossy link, one slow request must not delay the agent's decision. A late source is treated as missing, and imputation handles it.

### 6.4 Imputation (`_Imputer`, `impute`)

```python
def take(self, key: str, value: float | None) -> float:
    """Rule 2 if `value` is finite, else rules 3/4."""
    if value is not None and math.isfinite(value):
        return self.valid(key, value)
    return self.fill(key)

def fill(self, key: str) -> float:
    """Rules 3/4: carry forward a value <= locf_max_ticks old, else 0."""
    self.imputed.append(key)
    prev = self.old.get(key)
    if prev is not None and prev.age + 1 <= self.locf_max:
        self.new[key] = Carried(prev.value, prev.age + 1)
        return prev.value
    return 0.0
```

- Every metric goes through `take`. A finite value is used and stored with age 0.
- A missing value is replaced by the last value if it is at most 2 ticks old (LOCF, "last observation carried forward"), otherwise by 0. Either way the key is added to `imputed`, which sets `telemetry_stale = 1`.
- `impute` is **pure**: it takes the old `ImputeState` and returns new features and a new state. That is why it can be tested on recorded ticks.

**The frontend pod-churn rule** (Amendment, 2026-10-19). When a frontend pod is replaced, its log disappears with it, so the latency read is short:

```python
churn = srv.ok and state.server_pods is not None and set(srv.pod_names) != set(state.server_pods)
full = not loc_ok or srv.n >= contract.telemetry.server_log_min_completeness * loc.n
partial = (churn and not full
           and srv.n * (1.0 - contract.sla.latency_quantile) >= contract.telemetry.server_churn_min_tail_samples)
complete = srv.ok and srv.n > 0 and (full or partial)
latency_ms = imp.take("server/latency_ms", srv.sla_ms if complete else None)
```

- `full`: the read has at least 90 % as many requests as Locust sent.
- `partial`: the pod set changed (a known reason for a short read), and the remaining requests still contain at least 5 above the p95 (`n·(1−q) ≥ 5`).
- **Why:** without this rule, every frontend restart made the next tick stale, so the agent could never learn what a restart does.

### 6.5 The observation (`build_obs`)

```python
obs = np.zeros(OBS_DIM, dtype=np.float32)
lat = norm_latency(f.latency_ms, l_sla_ms)
...
for i, d in enumerate(contract.cluster.managed):
    s, lim = f.services[d], limits[d]
    pods = max(1.0, s.status_replicas)
    base = SERVICE_OFFSET + FEATURES_PER_SERVICE * i
    obs[base + 0] = _clip01(s.cpu_cores / (lim.cpu_cores * pods))
    obs[base + 1] = _clip01(s.throttle)
    ...
    obs[base + 6] = (0.0 if s.ticks_since_gen_change is None
                     else math.exp(-s.ticks_since_gen_change / ROLLOUT_RECENCY_TICKS))
...
if not np.all(np.isfinite(obs)):
    raise TelemetryError(f"non-finite observation: {obs.tolist()}")
```

- Indices 0–4 are global (latency, failures, RPS and their changes); then 7 features for each of the 4 managed services (5 + 7·i + j); then 3 loop features (in-flight, ticks since action, stale).
- CPU and memory are divided by `limit × replicas`, so "0.8" means 80 % of what the deployment is allowed to use, whatever its size.
- `rollout_recency` decays as e^(−t/5) after a deployment's spec changes. This lets the agent and the runbook notice "something changed here recently" (F1 is a bad rollout) without being told the fault.
- `norm_latency` uses `log2(1 + L/L_SLA) / log2(21)`, so latency up to 20 × L_SLA is spread across [0, 1] instead of saturating.
- `_assert_finite(f)` runs **before** clipping, because `min`/`max` would silently turn NaN into a bound. The final `isfinite` check raises rather than return NaN.

---

## 7. `env/k8s_actions.py`: actions, mask, lock, executor

### 7.1 The catalog

`CATALOG` is a tuple of 12 `Action(id, kind, target, delta)` records (NOOP, 4 × RESTART, SCALE_UP/DOWN frontend, SCALE_UP cart/currency, 3 × RESTORE). The ids are fixed: they are the indices of the agent's output layer.

### 7.2 The mask

```python
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
```

- The mask depends only on replica counts and the lock. It never uses the fault.
- **RESTORE is always valid.** If it were valid only when the deployment differed from golden, the mask itself would reveal "this deployment was tampered with", which is the F1 fault identity.

### 7.3 The in-flight lock

```python
def rollout_complete(st: DeploymentStatus) -> bool:
    return (st.generation == st.observed_generation and st.updated_replicas == st.spec_replicas
            and st.available_replicas == st.spec_replicas and st.status_replicas == st.spec_replicas)

def lock_should_clear(lock: Lock, st: DeploymentStatus | None, now_mono: float, contract: Contract) -> str | None:
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
```

- `generation` increases each time the Deployment spec changes; `observedGeneration` is the last one the controller processed. Equal values plus all replica counts matching means the rollout has finished.
- `advanced` requires the generation to have moved past the value recorded at dispatch. Otherwise the lock could clear on the very next tick, before Kubernetes even started the rollout.
- After 100 s the lock clears anyway (`stalled`), so the agent can never be locked out forever.
- **Why a lock at all:** one action at a time keeps cause and effect readable, both for the agent learning and for us analysing.

### 7.4 The executor (`ActionExecutor.execute`)

```python
if action.kind == RESTART:
    body = {"spec": {"template": {"metadata": {"annotations": {
        RESTARTED_AT_ANNOTATION: datetime.now(timezone.utc).isoformat()}}}}}
    self.apps.patch_namespaced_deployment(name, self.ns, body, _request_timeout=self.timeout)
elif action.kind == SCALE:
    target = scale_target(action, int(dep.spec.replicas or 0), self.c)
    if target is None:
        return self._fail(action, t0, "replica_bound", ...)
    self.apps.patch_namespaced_deployment_scale(name, self.ns, {"spec": {"replicas": target}}, ...)
else:                                                     # RESTORE (§5.2)
    g = self.golden[name]
    live_tpl = self.apps.api_client.sanitize_for_serialization(dep.spec.template)
    if template_hash(live_tpl) == g.template_hash and int(dep.spec.replicas or 0) == g.replicas:
        ...
        return ExecResult(True, action.id, False, gen_before, ...)
    # RESTORE must be JSON Patch: strategic merge would keep EXTRA_LATENCY (§5.2).
    patch = [{"op": "replace", "path": "/spec/template", "value": g.template},
             {"op": "replace", "path": "/spec/replicas", "value": g.replicas}]
    self.apps.patch_namespaced_deployment(name, self.ns, patch, _request_timeout=self.timeout)
```

- **RESTART** is what `kubectl rollout restart` does: changing an annotation on the pod template makes Kubernetes roll out new pods one by one, with no downtime. No pod is deleted directly; the agent's credential is not allowed to delete pods.
- **SCALE** checks the bound again (`scale_target` returns `None` outside `1 … max`). The mask already checks it; the executor checks a second time in case the cluster state changed in between.
- **RESTORE** uses a **JSON Patch** (a list body). A strategic-merge patch (a dict body) merges the `env` list by name, so the injected `EXTRA_LATENCY` variable would survive. `replace /spec/template` swaps the whole template.
- If the deployment already equals golden (`template_hash` ignores `restartedAt`), no API call is made. The action is still charged its cost, so RESTORE is not a free "probe".
- `ApiException` and `urllib3.exceptions.HTTPError` become a typed failure via `_fail`. The env then records the action as NOOP and releases the lock.

---

## 8. `env/golden.py`: the known-good state

`config/golden_live.json` is captured from the API server right after a clean deployment, so it already contains the defaults the API server fills in. Comparing against the raw YAML would always show a difference.

```python
def template_hash(template: dict[str, Any]) -> str:
    """sha256 over canonical JSON of the template, ignoring restartedAt (CLAUDE.md §5.2)."""
    canonical = json.dumps(strip_restarted_at(template), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
```

- `sort_keys=True` and fixed separators give the same text for the same content, whatever the key order.
- `strip_restarted_at` removes the restart annotation: a restarted deployment is still "golden".

---

## 9. `env/injector.py`: faults

Uses the **controller** credential (the agent's credential cannot create chaos objects).

**Sampling a plan** (training only; evaluation reads the plan from the fixed schedule):

```python
def sample_plan(rng: random.Random, contract: Contract) -> FaultPlan:
    e = contract.episode
    faults = sorted(e.fault_probs)
    fault = rng.choices(faults, weights=[e.fault_probs[f] for f in faults], k=1)[0]
    lo, hi = e.lead_in_ticks
    lead_in = rng.randint(lo, hi)
    if fault == "F1":
        return FaultPlan(fault, F1_TARGET, rng.choice(e.f1_latency), lead_in)
    ...
```

`sorted(...)` makes the draw independent of dict order, so a seed always gives the same plan sequence. The random lead-in (2–5 ticks) means the agent cannot learn "the fault always starts at tick 3".

**Injecting** (`Injector.on_tick`), once, at tick `k == lead_in`:

```python
if p.fault == "F1":
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": APP_CONTAINER, "env": [{"name": FAULT_ENV, "value": str(p.severity)}]}]}}}}
    self.apps.patch_namespaced_deployment(F1_TARGET, self.ns, body, ...)
elif p.fault == "F2":
    pods = self.core.list_namespaced_pod(self.ns, label_selector=f"app={p.target}", ...).items
    running = [x for x in pods if x.status.phase == "Running" and x.metadata.deletion_timestamp is None]
    if len(running) != 1:
        return self._remember(self._failed("F2", "precondition", ...))
    self.stressed_uid = running[0].metadata.uid
    ...
    self.custom.create_namespaced_custom_object(CHAOS_GROUP, CHAOS_VERSION, self.ns, CHAOS_PLURAL,
        stresschaos_manifest(self.chaos_name, self.ns, str(p.target), int(p.severity or 1)), ...)
elif p.fault == "F3":
    self.apps.patch_namespaced_deployment_scale(p.target, self.ns, {"spec": {"replicas": 0}}, ...)
else:                                                   # F4
    users = surge_users(float(p.severity), self.u_base)
    self._swarm(users)
```

- **F1** (bad deploy): a strategic-merge patch *adds* `EXTRA_LATENCY` to the `server` container, the way a bad config change would. This creates a new rollout, which shows up as `rollout_recency` in the observation.
- **F2** (hot pod): Chaos Mesh `StressChaos` burns CPU inside one pod. The pod's UID is recorded, because the cure is "that pod no longer exists" (a restart replaced it).
- **F3**: the deployment is scaled to 0.
- **F4**: Locust's user count is multiplied by 2.5 or 3.
- If injection fails, the episode is marked invalid instead of continuing without a fault.

**Cure conditions** (`check_cure`): F1 = the live template has no `EXTRA_LATENCY` and the rollout is complete; F2 = the recorded UID is gone, and then the `StressChaos` object is **deleted immediately** (otherwise Chaos Mesh would stress the replacement pod); F3 = at least one replica available; F4 and NULL = always true (the SLA alone decides recovery).

---

## 10. `env/reward.py`: reward, health, recovery

All pure functions.

```python
def latency_violation(latency_ms, l_sla_ms):
    if latency_ms <= l_sla_ms: return 0.0
    return _clip01(math.log2(latency_ms / l_sla_ms) / LATENCY_LOG2_SPAN)

def replica_surplus(spec_replicas: Mapping[str, int], contract: Contract) -> float:
    base = contract.replicas.base
    surplus = sum(max(0, int(spec_replicas[d]) - base[d]) for d in contract.cluster.managed)
    return surplus / contract.reward.replica_denominator

def reward(v_next, executed_kind, rho_next, contract):
    cost = contract.reward.action_cost[executed_kind]
    return -(v_next + cost + contract.reward.w_replica * rho_next)

def healthy(latency_ms: float, fail_ratio: float, l_sla_ms: float, contract: Contract) -> bool:
    return latency_ms <= l_sla_ms and fail_ratio <= contract.sla.e_sla
```

- Latency penalty: 0 at or below L_SLA, rising with log2 of the ratio, reaching 1 at 8 × L_SLA (`LATENCY_LOG2_SPAN = 3`). The log keeps a 2× breach from being treated like a 20× one, without letting huge spikes dominate.
- The reward is charged for the **executed** action (`executed_kind`). A dispatch that failed is charged as NOOP.
- `replica_surplus` makes extra replicas cost a little every tick, so "scale everything up and leave it" is not free.

```python
def recovery_start(health, cured, k_inject, contract):
    n = contract.sla.recovery_ticks
    for t in range(k_inject + 1, len(health) - n + 1):
        if all(health[t:t + n]) and cured[t + n - 1]: return t
    return None
```

Recovery = the first tick after injection that starts 3 healthy ticks in a row, with the fault's cure condition true at the end. MTTR is measured to the **first** of those 3 ticks. Requiring both health and cure prevents a lucky quiet moment from counting as recovery.

---

## 11. `env/boutique_env.py`: the environment

`BoutiqueEnv` is a `gymnasium.Env`. It glues every module above into `reset()` and `step()`.

### 11.1 `reset()` (summary)

1. `injector.remove_all_faults()` and remove any HPA objects.
2. RESTORE all four managed deployments to golden.
3. Wait until every deployment is rollout-complete and Prometheus and Locust answer (180 s). If that fails: delete all pods in `boutique` (hard reset) and wait again; if that fails too, raise `EnvironmentDegraded`. The watchdog then stops and alerts, so training never continues on a broken cluster.
4. Wait 30 s after the last pod change, then require 2 consecutive healthy, non-stale ticks.
5. For the HPA baseline only, create the HPA objects.
6. Pick the plan (given, from the schedule, or sampled), arm the injector, open the transition log, re-anchor the clock, and return the first observation and mask.

### 11.2 `step(action)`

```python
t_enter = self.clock.now()
k = ep.k
if self.clock.missed(t_enter, k):                       # missed the tick entirely
    missed, valid = True, False
    k_new = self.clock.next_boundary_index(t_enter) - 1
    for _skipped in range(k, k_new):                    # unobserved ticks count as not healthy
        ep.health.append(False)
        ...
late = self.clock.is_late(t_enter, k)
a = a_chosen
if missed or not (0 <= a < N_ACTIONS) or not ep.mask[a]:
    if not missed:
        self.events.emit("mask_violation", component="env", tick=k, action=a)
    a = 0
```

- If the caller was so slow that a whole tick passed, the env skips ahead and counts the skipped ticks as **unhealthy**. MTTR can only get worse from a delay, never better.
- An invalid action is replaced by NOOP and logged.

```python
if a != 0 and ep.lock is None:
    act = CATALOG[a]
    ...
    ep.lock = Lock(act, str(act.target), self.clock.wall_now(), self.clock.now(),
                   st.generation if st else None)
    future = self.dispatch_pool.submit(self.executor.execute, act, k)
inject = self.injector.on_tick(k)
...
self.clock.wait_boundary(k + 1)
raw = self.collector.collect(k, self.clock.wall(k), self.clock.wall(k + 1))
```

- The action is **submitted** to a single-worker thread pool and not awaited. `step()` goes straight on to wait for the next boundary while Kubernetes works. This is the asynchronous dispatch from `CLAUDE.md` §5.7.
- The lock stores the generation before the action (`gen_before`) for the rollout check in §7.3.

```python
exec_error, a_exec = False, a
if future is not None:
    res = future.result() if future.done() else ExecResult(False, a, False, error_type="dispatch_timeout")
    if not res.ok:
        exec_error, a_exec, ep.lock = True, 0, None
    elif CATALOG[a].kind == RESTORE and not res.api_called and ep.lock is not None:
        ep.lock = dataclasses.replace(ep.lock, no_call=True)
```

The dispatch had a whole tick. Not done by now = timeout. A failure makes the executed action NOOP and releases the lock.

```python
cured = self.injector.check_cure(raw, k)
features, ep.impute_state = impute(raw, ep.impute_state, self.c)
obs = build_obs(features, prev, ...)
v = sla_violation(features.latency_ms, features.fail_ratio, self.cal.l_sla_ms, self.c)
rho = replica_surplus(self._spec(features), self.c)
r = reward(v, CATALOG[a_exec].kind, rho, self.c)
ep.health.append(healthy(...))
ep.stale_run = ep.stale_run + 1 if features.stale else 0
terminated, truncated, mttr_s = self._done_flags(k)
if ep.stale_run >= self.c.telemetry.stale_truncate_ticks:
    truncated, valid = True, False
```

Three stale ticks in a row end the episode as invalid: with that much missing data it cannot be scored fairly.

### 11.3 `_done_flags`

```python
if ep.plan.fault == "NULL":
    return False, k >= ep.plan.lead_in + e.null_extra_ticks, None
...
t = recovery_start(ep.health, ep.cured, ep.k_inject, self.c)
if t is not None and k >= t + self.c.sla.recovery_ticks - 1:
    ep.recovered_at = t
    return True, False, self.clock.wall(t) - (ep.t_inject_wall or self.clock.wall(t))
return False, k - ep.k_inject >= e.fault_max_ticks, None
```

- **terminated** = recovered. **truncated** = time ran out (18 ticks after injection) or the NULL horizon.
- The difference matters for learning: the critic bootstraps on truncation (the future still exists) but not on termination (§13).
- MTTR = wall time of the first healthy tick minus the wall time of injection.

---

## 12. `env/recorder.py`: logs

```python
def emit(self, event: str, component: str, **fields: Any) -> dict[str, Any]:
    record = {"t_wall": time.time(), "event": event, "component": component, **fields}
```

- `EventLog` writes one JSON object per line to `data/logs/<run>.events.jsonl` (`source_failed`, `mask_violation`, `lock_released`, `injected`, …).
- `TransitionRecorder` writes one line per tick to `data/transitions/<run>/episode_<n>.jsonl` with obs, mask, actions, reward, raw values and flags, and flushes every tick, so a crash loses at most the current tick.
- Runtime code never uses `print` for debugging. Everything the analysis needs is in these files.

---

## 13. `agents/masked_discrete_sac.py`: the agent

Plain PyTorch. An actor network outputs 12 logits; two critic networks each output 12 Q-values; there are target copies of the critics; and a learned temperature `log α` (held fixed online, Amendment 6).

### 13.1 Masked policy (the NaN trap)

```python
def masked_policy(logits: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """(probs, log_probs); both exactly 0 at masked entries, so π·logπ never forms 0·(−inf)."""
    log_probs = torch.log_softmax(torch.where(mask, logits, torch.full_like(logits, MASK_FILL)), dim=-1)
    log_probs = torch.where(mask, log_probs, torch.zeros_like(log_probs))
    probs = torch.where(mask, log_probs.exp(), torch.zeros_like(log_probs))
    return probs, log_probs
```

- Invalid actions get logit −1e9 (`MASK_FILL`), so their probability is 0.
- Their log-probability would be about −1e9 (or −inf). In `π·log π` that gives `0 · (−inf) = NaN`, which would spread through every loss. So masked log-probs are set to exactly 0 **before** any product.

### 13.2 Soft value and TD target

```python
def soft_value(probs, log_probs, q_min, mask, alpha):
    """V(s) = Σ_{a valid} π(a|s)·[min Q(s,a) − α log π(a|s)]."""
    q = torch.where(mask, q_min, torch.zeros_like(q_min))
    return (probs * (q - alpha * log_probs)).sum(-1)

def td_target(ret, done, discount, v_next):
    """y = R^(k) + (1 − done)·γ^k·V(s′); done = terminated only (truncation bootstraps, §5.6)."""
    return ret + (1.0 - done) * discount * v_next
```

- Discrete SAC can compute the expectation over actions exactly (a weighted sum over 12 values) instead of sampling.
- `min(Q1, Q2)` reduces overestimation (the "clipped double-Q" trick).
- `ret` is the n-step discounted reward and `discount` = γⁿ (computed in the replay buffer, §14).
- `done` is **terminated** only. A truncated episode still bootstraps, because the cluster would have continued.

### 13.3 Losses

```python
def critic_loss(q1_a, q2_a, y, weight):
    td1, td2 = q1_a - y, q2_a - y
    loss = (weight * td1.pow(2)).mean() + (weight * td2.pow(2)).mean()
    return loss, td1.detach(), td2.detach()

def actor_loss(probs, log_probs, q_min, mask, alpha):
    q = torch.where(mask, q_min, torch.zeros_like(q_min))
    return (probs * (alpha * log_probs - q)).sum(-1).mean()

def cql_penalty(q, q_a, mask):
    lse = torch.logsumexp(torch.where(mask, q, torch.full_like(q, MASK_FILL)), dim=-1)
    return (lse - q_a).mean()

def bc_loss(logits, mask, actions):
    _, log_probs = masked_policy(logits, mask)
    return -log_probs.gather(-1, actions.unsqueeze(-1)).squeeze(-1).mean()
```

- **Critic:** squared TD error, multiplied by the PER importance weight (`weight`). The TD errors are returned detached to become new priorities.
- **Actor:** move probability towards actions with high Q, with the entropy term `α log π` keeping the policy from collapsing too early.
- **CQL penalty:** `logsumexp` over valid actions is a soft maximum of Q. Subtracting Q of the action actually in the data pushes down Q-values of actions the data rarely shows. This counters the critic's tendency to overestimate untried actions, which made the online agent act in healthy states (attempt 4).
- **Behaviour cloning:** cross-entropy towards the runbook's action; used only in the offline warm start.
- `alpha_loss` (not shown) adjusts α towards a target entropy and **excludes states with only one valid action**, where entropy is always 0 and the loss would push α the wrong way.

### 13.4 `update` (one online gradient step)

```python
with torch.no_grad():
    p_next, lp_next = masked_policy(self.actor(next_obs), next_mask)
    q1t, q2t = self.critic_target(next_obs)
    y = td_target(ret, done, discount, soft_value(p_next, lp_next, torch.min(q1t, q2t), next_mask, alpha))

q1, q2 = self.critic(obs)
q1_a, q2_a = q1.gather(-1, act).squeeze(-1), q2.gather(-1, act).squeeze(-1)
c_loss, td1, td2 = critic_loss(q1_a, q2_a, y, weight)
if cql_weight > 0.0:
    c_loss = c_loss + cql_weight * (cql_penalty(q1, q1_a, mask) + cql_penalty(q2, q2_a, mask))
...
a_loss = actor_loss(probs, log_probs, torch.min(q1, q2).detach(), mask, alpha)
...
if learn_alpha:
    self.opt_alpha.zero_grad()
    al_loss.backward()
    self.opt_alpha.step()
self._polyak()
self.grad_step += 1
prio = priority_from_td(td1.numpy(), td2.numpy(), self.per)
```

- The target `y` uses the **next** state's mask (`next_mask`), so the value of s′ only counts actions that are valid there.
- The target is built inside `no_grad` from the slowly moving target critics; `_polyak` then moves them 0.5 % (τ = 0.005) towards the online critics.
- `torch.min(q1, q2).detach()` in the actor loss: the actor's gradient must not change the critics.
- `learn_alpha=False` online: α stays at 0.1 (Amendment 6). The α loss is still computed and logged.
- `self._check(loss, name)` stops training if a loss is not finite.

`offline_update` is the same critic step with the CQL penalty always on, and `bc_loss` instead of the SAC actor loss.

### 13.5 Acting

```python
if greedy:
    return int(torch.where(m, logits, torch.full_like(logits, MASK_FILL)).argmax(-1).item())
probs, _ = masked_policy(logits, m)
return int(torch.multinomial(probs[0], 1, generator=self.act_rng).item())
```

Greedy (evaluation and probes): the best valid logit. Training: sample from the masked distribution with a seeded generator.

---

## 14. `agents/per_buffer.py`: n-step returns and prioritized replay

### 14.1 `NStepBuilder.push`

```python
if s.stale:
    self.dropped_stale += 1
    return self._flush()                   # close pending chains at the last non-stale state
for p in self.pending:
    p.ret += (self.gamma ** p.k) * s.reward
    p.k += 1
    p.next_obs, p.next_mask, p.done = s.next_obs, s.next_mask, s.terminated
self.pending.append(_Pending(s.obs, s.mask, s.action, s.reward, 1, s.next_obs, s.next_mask, s.terminated))
if s.terminated or s.truncated:
    return self._flush()
out = []
while self.pending and self.pending[0].k >= self.n:
    out.append(self._close(self.pending.popleft()))
return out
```

- Each pending transition accumulates `r_t + γ r_{t+1} + γ² r_{t+2}` (n = 3). Once it has 3 rewards it is closed and goes to the buffer, with `discount = γ^k`.
- A **stale** step is never stored: the chains are closed at the last good state. A transition into a state built from imputed data would teach the agent things that did not happen.
- At the end of an episode the shorter chains are flushed with their own `γ^k`.

### 14.2 PER math

```python
def beta_at(cfg: PerCfg, grad_step: int) -> float:
    frac = min(1.0, grad_step / cfg.beta_anneal_grad_steps)
    return cfg.beta_start + frac * (cfg.beta_end - cfg.beta_start)

def priority_from_td(td1, td2, cfg):
    return np.minimum(0.5 * (np.abs(td1) + np.abs(td2)), cfg.priority_cap) + cfg.eps
```

- Priority = mean |TD error| of the two critics, capped at 1 (so one wild transition cannot take over) plus a small ε (so nothing has zero chance).
- β (importance-sampling correction) rises from 0.4 to 1.0 over 15 000 **gradient** steps. The bias correction becomes complete as training converges.

### 14.3 Sum tree and sampling

```python
def find(self, mass: float) -> int:
    j = 1
    while j < self.size:
        left = self.tree[2 * j]
        if mass < left:
            j = 2 * j
        else:
            mass -= left
            j = 2 * j + 1
    return j - self.size
```

A binary tree where each node holds the sum of its children; the leaves hold `p^α`. Picking a random number in `[0, total)` and walking down finds a leaf with probability proportional to its priority, in O(log n) instead of O(n).

```python
mass = (np.arange(batch_size) + self.rng.random(batch_size)) * seg     # stratified
idx = np.array([min(self.tree.find(min(m, np.nextafter(total, 0))), self.n - 1) for m in mass])
prob = self.priority[idx] ** self.cfg.alpha / total
w = (self.n * prob) ** -beta
weight = (w / w.max()).astype(np.float32)
```

- **Stratified:** the total is split into `batch_size` equal segments with one sample from each, so a batch is spread across the buffer.
- **IS weight** `(N·P(i))^−β`, normalised by the max so weights only scale losses down.
- `np.nextafter(total, 0)` is the float just below `total`, guarding against a rounding error that would walk off the tree.
- With `prioritized=False` the same class samples uniformly with weight 1: the uniform ablation is a switch, not a second implementation.

New transitions get the current **max** priority (`add`), so each is replayed at least once soon after it arrives.

---

## 15. `agents/warmstart.py` and `agents/train.py`: training

### 15.1 Offline warm start

`load_transitions` reads the 120 recorded runbook episodes (JSONL) and rebuilds n-step transitions with the same `NStepBuilder`. Then:

```python
for _ in range(steps):
    b = buffer.sample(batch_size, beta_at(per, agent.grad_step))
    s, prio = agent.offline_update(b, cql_weight)
    buffer.update_priorities(b.idx, prio)
```

2000 gradient steps: the actor copies the runbook (BC), the critic learns values with the CQL penalty. The docstring records why: plain offline SAC acted on 97 % of ticks where the runbook took NOOP on 70 %.

### 15.2 The online loop (`Trainer.run_episode`)

```python
while not (terminated or truncated):
    a = agent.act(obs["obs"], obs["mask"], greedy=probe)
    nxt, r, terminated, truncated, last = env.step(a)
    s = Step(obs["obs"], obs["mask"], int(last["a_exec"]), float(r), nxt["obs"], nxt["mask"],
             bool(terminated), bool(truncated), bool(last["stale"]))
    ...
    if not probe:
        for t in self.builder.push(s):
            self.buffer.add(t)
        n_up, secs = self._updates(stats)
```

- The stored action is `a_exec` (what really happened), not the action chosen.
- Probe episodes (greedy, no learning) run every few episodes to monitor the policy.

```python
def _updates(self, stats):
    ...
    while n < rl.updates_per_tick and time.monotonic() - t0 < self.c.clock.update_budget_s:
        b = self.buffer.sample(rl.batch_size, beta_at(self.c.per, self.agent.grad_step))
        s, prio = self.agent.update(b, self.c.rl.offline_cql_weight, learn_alpha=False)   # Amendment 6
        self.buffer.update_priorities(b.idx, prio)
```

Up to 4 gradient steps per tick, cut off after 1.0 s, so learning never delays the next decision.

**Other parts of `train.py`:**
- `pin_cpu`: `os.sched_setaffinity` moves PyTorch off Locust's CPU cores 0–1, and `torch.set_num_threads(2)`. Otherwise gradient updates slow Locust and inflate measured latency.
- Checkpoints every 25 episodes and on SIGTERM (`latest.pt`, `ep<N>.pt`, `final.pt`). They contain networks, optimisers, `log α`, gradient step and RNG states.
- `resume_or_start` and `rebuild_buffer`: on restart, the buffer is rebuilt from the run's JSONL transitions, with priorities reset to max.
- A kill switch (`kill`) stops the run if α leaves its allowed range.

---

## 16. `agents/runbook.py`: the scripted baseline

`read_obs` turns the normalised observation back into physical units (the runbook sees exactly what the agent sees, never `info`). `decide` checks the rules in order and returns the first match:

```python
if v.in_flight:
    return 0, "R0"
for d in c.cluster.managed:                                               # R1
    aid = _action_id(RESTORE, d)
    if v.spec[d] < c.replicas.base[d] and ok(aid):
        return int(aid), "R1"
if unhealthy(n_debounce):                                                  # R2
    changed = [(since, d) for d, since in v.ticks_since_gen_change.items()
               if since is not None and since <= rb.change_window_ticks
               and not self._caused_by_me(d, since) and ok(_action_id(RESTORE, d))]
    if changed:
        _, d = min(changed)
        return int(_action_id(RESTORE, d)), "R2"
surge = v.rps >= rb.surge_rps_factor * self.cal.rps_base
if unhealthy(n_debounce) and surge and v.throttle[FRONTEND] >= rb.throttle_threshold \
        and v.spec[FRONTEND] < c.replicas.max[FRONTEND]:                     # R3
    ...
if unhealthy(n_debounce) and not surge:                                    # R4
    ... RESTART the most throttled service (2 ticks above θ, not restarted recently)
...                                                                        # R5 scale back, R6 NOOP
```

| Rule | Plain English | Fault it targets |
|---|---|---|
| R0 | an action is in flight → wait | — |
| R1 | a service has fewer replicas than base → RESTORE it | F3 |
| R2 | unhealthy, and a service changed recently without the runbook doing it → RESTORE it ("roll back the last deploy") | F1 |
| R3 | unhealthy, traffic ≥ 1.5 × normal, frontend throttled → scale frontend up | F4 |
| R4 | unhealthy, normal traffic, a service throttled for 2 ticks → restart it | F2 |
| R5 | healthy for 6 ticks and traffic normal → undo extra replicas | cleanup |
| R6 | otherwise NOOP | — |

- `unhealthy(2)` (debounce) means 2 bad ticks in a row, so a single noisy tick does not trigger an action.
- `_caused_by_me` stops R2 from rolling back the runbook's own restarts.
- `act()` adds the warm-start mode: with probability 0.25 a uniformly random **valid** action, tagged `source="eps"`. The demonstrations then also show what happens after non-runbook actions.

---

## 17. `eval/`: evaluation and statistics

### 17.1 `run_eval.py`: interleaved evaluation

```python
for entry in entries:
    for name in policy_order(list(policies), int(entry["index"]), seed):
        if (int(entry["index"]), name) in done:
            continue
        for attempt in range(1, args.max_attempts + 1):
            row = {**run_one(env, policies[name], entry), "attempt": attempt}
            with rows_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            if row["valid"] or _stop:
                break
```

```python
def policy_order(names: list[str], index: int, seed: int) -> list[str]:
    order = list(names)
    random.Random(seed * 100_003 + index).shuffle(order)
    return order
```

- Every schedule entry (fault, target, severity, lead-in) is run by **every** policy before moving to the next entry, in a seeded random order per entry. Slow drift in the cluster or the Wi-Fi over days then affects all policies equally, instead of favouring whichever ran on a good day.
- `done` makes the run resumable: finished (entry, policy) pairs are skipped.
- An invalid episode is retried up to 3 times; the retry is recorded (`attempt`).
- The script refuses to run with a dirty git tree and records the hashes of the locked files.

### 17.2 `metrics.py`

`episode_metrics` turns one episode's transitions into a row: recovered, MTTR, MTTR censored at 360 s (not recovered = 360), SLA penalty, number of actions, and so on.

### 17.3 `stats.py`: the pre-registered tests

```python
def cliffs_delta(x, y) -> float:
    xa, ya = np.asarray(x, float)[:, None], np.asarray(y, float)[None, :]
    return float(((xa < ya).sum() - (xa > ya).sum()) / (xa.size * ya.size))
```

Broadcasting (`[:, None]` vs `[None, :]`) compares every x with every y at once. δ = P(x < y) − P(x > y); positive means the first group has **lower** MTTR (better).

```python
def holm(pvals):
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj.tolist()
```

Holm–Bonferroni: the smallest p is multiplied by m, the next by m−1, and so on, with `running = max(...)` keeping the adjusted values monotone. This controls the chance of any false positive across a family of tests.

```python
def bootstrap_median_diff(x, y, seed, n=BOOTSTRAP_RESAMPLES):
    rng = np.random.default_rng(seed)
    d = (np.median(xa[rng.integers(0, xa.size, (n, xa.size))], axis=1)
         - np.median(ya[rng.integers(0, ya.size, (n, ya.size))], axis=1))
    lo, hi = np.percentile(d, [100 * (1 - CI_LEVEL) / 2, 100 * (1 + CI_LEVEL) / 2])

def non_inferior(c, margin_s=NI_MARGIN_S) -> bool:
    return c.ci_high < margin_s
```

- Resample both groups with replacement many times (in one vectorised call), take the difference of medians each time, and read the 95 % interval.
- Non-inferiority (H2, H5): the agent is "no worse than the runbook" if the **upper** end of that interval is below +20 s.

```python
def kaplan_meier(times, observed):
    grid = np.unique(t[e])
    surv, s = [], 1.0
    for g in grid:
        at_risk = int((t >= g).sum())
        events = int(((t == g) & e).sum())
        s *= 1 - events / at_risk
        surv.append(s)
```

Kaplan–Meier "probability not yet recovered" over time. Episodes that never recovered are *censored* at 360 s: they stay in the risk set until then but never count as an event. `logrank` compares two such curves using SciPy's `CensoredData`.

### 17.4 Analysis and export

- `analyze.py` runs H1–H5 on the main, edge and seed-1 data and writes `analysis.json`.
- `plots.py` draws the figures in `figures/`.
- `papera_export.py` writes the CSVs in `paper_a/figure_data/`; `paper_a/make_figures.py` draws the paper figures from those CSVs alone.
- `paperb_*.py` do the same for Paper B.

---

## 18. `locust/locustfile.py`: the load generator

```python
@events.request.add_listener
def _on_request(response_time: float, exception: BaseException | None, **_: Any) -> None:
    t_b = time.time()
    if exception is not None and (_is_timeout(exception) or response_time >= TIMEOUT_RESPONSE_MS):
        LOG.record(t_b, TIMEOUT_RESPONSE_MS, False)
    else:
        LOG.record(t_b, float(response_time), exception is None)
```

```python
@environment.web_ui.app.route("/tick")
def tick() -> Response | tuple[Response, int]:
    try:
        t_from = float(request.args["from"])
        t_to = float(request.args["to"])
    except (KeyError, ValueError) as exc:
        return jsonify(error=f"bad query: {type(exc).__name__}: {exc}"), 400
    ...
    return jsonify(aggregate(LOG.window(t_from, t_to), t_to - t_from))
```

```python
class BoutiqueUser(FastHttpUser):
    wait_time = between(*WAIT_S)
    ...
    default_headers: ClassVar[dict[str, str]] = {"Connection": "close"}
```

- Every completed request is stored in a ring buffer with B's timestamp. A timeout is stored as a failure of 5000 ms; it is real data, not missing.
- The custom `/tick?from=&to=` route returns count, failures, p50, p99 and RPS for exactly the tick window, so Locust's numbers line up with Prometheus and the server logs.
- `Connection: close` opens a new TCP connection per request. kube-proxy balances per connection; with keep-alive, users stayed on the original frontend pod and new replicas received no traffic.
- The `/tick` route needs Locust's web UI, so Locust must not run with `--headless`.

---

## 19. `scripts/`: operations

| Script | What it does |
|---|---|
| `preflight.py --stage m1/m2/…` | Checks clock skew, LAN RTT, memory on A, credentials (`auth can-i`), Prometheus freshness, Locust, Chaos Mesh |
| `calibrate.py` | Measures `u_base`, `rps_base` and `L_SLA` in steady state; writes `calibration.json` |
| `snapshot_golden.py` | Captures `golden_live.json` from the API after a clean apply |
| `fault_smoke.py` | Injects one fault and checks it is visible and curable |
| `session_start.sh` | Start of every session: restore, preflight, steady-state check against the frozen calibration |
| `chain_m4.sh`, `chain_seed1.sh`, `edge_stage.sh` | The full experiment sequences used for the reported results |
| `watchdog.sh` | Restarts a failed run (below) |

**`watchdog.sh`** (key lines):

```bash
"$@" &
child=$!
wait "$child"; rc=$?
if [ "$rc" -eq 0 ]; then
  echo "watchdog: $(date -Is) command finished (exit 0)"; exit 0
fi
if ! lan_up; then
  ...
  while ! lan_up; do sleep "$LAN_POLL_S"; done
  if ! bash scripts/session_start.sh > "data/logs/session_start_$(date +%Y%m%dT%H%M).log" 2>&1; then
    alert "watchdog: session_start.sh failed after a LAN outage; not resuming: $*"
  fi
  continue
fi
if [ $((SECONDS - t_start)) -ge "$PROGRESS_S" ]; then fails=1; else fails=$((fails + 1)); fi
if [ "$fails" -ge "$MAX_FAILS" ]; then
  alert "watchdog gave up after $fails consecutive failures (last exit $rc): $*"
fi
```

- `"$@"` runs the wrapped command with its arguments; `$!` is its PID; `wait` returns its exit code.
- A failure while Machine A is unreachable (hotspot drop) is **not** counted. The watchdog waits for the LAN, re-runs `session_start.sh` (A may have rebooted), then resumes.
- A run that lasted 15 min or more before failing made progress, so the failure count restarts at 1.
- After 3 consecutive failures it writes `data/logs/ALERT` and stops.
- `trap ... TERM INT` forwards Ctrl-C/SIGTERM to the child, which finishes its tick and checkpoints.

---

## 20. `tests/`

Roughly one test file per module (`test_imputation.py`, `test_env_core.py`, `test_injector.py`, `test_runbook.py`, `test_sac_per.py`, `test_eval_stats.py`, …). They test pure logic on recorded fixtures (`tests/fixtures/`) or small hand-written tensors. The test names say what is guaranteed, for example:
- `test_mask_rules` (`test_env_core.py`): the §5.8 mask rules;
- `test_masked_policy_zeroes_masked_entries_without_nan`, `test_alpha_loss_excludes_single_action_states_and_has_right_sign` (`test_sac_per.py`);
- `test_stale_next_state_is_never_stored_and_breaks_the_chain`, `test_nstep_truncation_bootstraps` (`test_sac_per.py`);
- `test_rule4_locf_expires_after_locf_max_ticks` (`test_imputation.py`);
- `test_holm_matches_hand_computation`, `test_kaplan_meier_with_censoring` (`test_eval_stats.py`).

Tests that touch the cluster are marked `@pytest.mark.cluster` and do not run by default. Run everything with `pytest -q` and `ruff check .`.

---

## 21. Where to look for…

| Question | File, function |
|---|---|
| How is one tick run? | `env/boutique_env.py` `step` |
| What exactly is measured, and when? | `env/telemetry.py` `Collector.collect`, `build_queries`, `fetch_server_latency` |
| What happens when data is missing? | `env/telemetry.py` `_Imputer`, `impute` |
| What are the 36 observation numbers? | `env/telemetry.py` `build_obs`; `CLAUDE.md` §5.3 |
| Which actions are allowed when? | `env/k8s_actions.py` `compute_mask` |
| How is an action sent to Kubernetes? | `env/k8s_actions.py` `ActionExecutor.execute` |
| How are faults injected and cured? | `env/injector.py` `on_tick`, `check_cure` |
| How is the reward computed? | `env/reward.py` `reward`, `sla_violation` |
| When is an episode recovered? MTTR? | `env/reward.py` `recovery_start`; `env/boutique_env.py` `_done_flags` |
| How does the agent learn? | `agents/masked_discrete_sac.py` `update`, `offline_update` |
| How is replay prioritised? | `agents/per_buffer.py` `SumTree`, `ReplayBuffer.sample` |
| What does the runbook do? | `agents/runbook.py` `decide` |
| How is evaluation ordered? | `eval/run_eval.py` `policy_order`, `main` |
| How are the hypotheses tested? | `eval/stats.py`, `eval/analyze.py` |
| Where does every locked number come from? | `config/contract.yaml`, `env/contract.py` |
