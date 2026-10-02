# CLAUDE.md — Guardrails for Claude Code in This Repository

> Read this whole file before writing or running anything.
> This file is the **technical source of truth**:
> - `PLAN.md` says what to build and when.
> - This file says how it must work.
> - `config/contract.yaml` holds every locked number.
>
> If a task conflicts with this file, **stop and ask the human.**

---

## 1. What This Project Is

A real-cluster, closed-loop testbed in which an RL agent learns to remediate injected faults in Google's Online Boutique running on K3s:
- **Agent:** masked discrete Soft Actor-Critic with Prioritized Experience Replay (PER-iSAC).
- **Faults:** injected by Chaos Mesh and a fault injector.
- **Baselines:** default Kubernetes, Kubernetes + HPA, and a scripted SRE runbook.
- **Headline metric:** MTTR under a fixed fault schedule.

The scientific value comes entirely from **real system physics.** Anything that fakes, simulates, or shortcuts the cluster destroys the result.

---

## 2. Architecture (Non-Negotiable)

```
        LAN link (design: wired, RTT p99 < 2 ms; see Environment Overrides below)
┌───────────────────────────────┐            ┌──────────────────────────────────────────┐
│ MACHINE A — cluster (12 GB)   │            │ MACHINE B — execution (10 GB)             │
│ headless Linux, swap OFF      │            │ timestamp authority (clock synced to A)   │
│                               │            │                                           │
│ K3s (traefik disabled)        │◄── :6443 ──┤ env/ action executor   (agent.kubeconfig) │
│  ├ ns boutique: Online        │◄── :6443 ──┤ env/ injector + reset  (controller.kubecfg)│
│  │  Boutique (11 deployments) │◄── :30080 ─┤ Locust (cores 0,1) → /tick, /swarm :8089  │
│  ├ ns chaos-mesh: controller, │            │ runbook / PER-iSAC agent (other cores,     │
│  │  chaos-daemon (containerd) │            │   CPU-only torch, 2 threads)               │
│  ├ ns monitoring: Prometheus  │◄── :30090 ─┤ telemetry client (PromQL)                  │
│  │  (cAdvisor, 5 s scrape)    │            │ evaluation, Claude Code                    │
│  └ metrics-server (HPA)       │            │                                           │
└───────────────────────────────┘            └──────────────────────────────────────────┘
```

### Facts to keep in mind

**Online Boutique.**
- After `loadgenerator` is removed there are **11 deployments**: 10 application services plus `redis-cart`.
- Every application container is named **`server`**.
- Pod names follow `<deployment>-<rs-hash>-<5char>`.

**Managed services.** The agent observes and acts on exactly four:
- `frontend`
- `cartservice`
- `currencyservice`
- `productcatalogservice`

The other services run but are outside the agent's state and action space.

**Machine B is the clock authority.**
- All wall-clock timestamps are `time.time()` on B.
- All scheduling uses `time.monotonic()` on B.
- Prometheus queries pass an explicit `time=` parameter taken from B's clock.
- In the deployed environment B's clock is disciplined to A's NTP server (see "Clock-sync topology" below). B still stamps and schedules everything; only the time *source* is A.

**Three kubeconfigs:**

| Kubeconfig | Used by | Scope |
|---|---|---|
| `config/kube/admin.kubeconfig` | Humans and bootstrap scripts **only** | Full cluster |
| `config/kube/agent.kubeconfig` | `env/k8s_actions.py` executor and telemetry reads | `boutique`: deployments get/list/watch/patch, deployments/scale get/patch, pods get/list/watch |
| `config/kube/controller.kubeconfig` | `env/injector.py` and `reset()` | `boutique`: deployments + scale get/list/patch; pods get/list/delete; `stresschaos.chaos-mesh.org` create/get/list/delete |

**GPU.** There is no GPU anywhere in this project. Torch is the CPU build on B.

### Environment Overrides (Human-Approved 2026-10-01)

The deployed testbed differs from the design above. A and B are linked over a **wireless hotspot** (B runs in WSL2 behind NAT), and **A runs other workloads at the same time**. For this environment only, three preflight thresholds are relaxed. They are marked `[override]` in `scripts/preflight.py`:

| Check | Design gate | Override |
|---|---|---|
| LAN RTT p99 (`ping -c 200`) | < 2 ms | **< 800 ms** |
| Machine A idle memory | < 5 GiB | **< 10 GiB** |
| Machine A memory under load (M2) | < 8 GiB | **< 12 GiB** (approved 2026-10-01; A idles at ~8.8 GiB) |

The A–B clock-skew gate is back at its **200 ms design value**. Its earlier 1000 ms override was retired on 2026-10-01 after the clock fix ("Clock-sync topology" below).

Consequences to keep in mind:
- Decision latency (G1) has less headroom against the 3.0 s collection deadline (RTT).
- A has less memory headroom: Online Boutique, Prometheus and Chaos Mesh share it with unrelated workloads. Watch for OOM kills and evictions.
- Results must be reported as obtained under this environment.

#### Clock-sync topology (Human-Approved 2026-10-01)

The design has B as the chrony time source. In the deployed environment **A serves time and B follows**: B is WSL2 behind NAT, so A cannot reach it on udp/123.

- **A:** chrony serves NTP to `192.168.137.0/24` (`k8s/k3s/chrony-server.sh`, `local stratum 10 orphan`).
- **Windows host of B:** w32time uses A as its only peer (`scripts/w32time-follow-a.ps1`). Settings: 64 s poll, `UpdateInterval=100`, `FrequencyCorrectRate=2`, `MaxAllowedPhaseOffset=1`.
  - With default `UpdateInterval`, w32time overshot A by about 0.6 s. Do not drop these settings.
- **B (WSL2):** the kernel clock is owned by WSL's system VM. Its own chronyd follows the Windows clock through Hyper-V PTP (PHC0). B therefore tracks A through Windows.
- **B's chronyd** (`scripts/chrony-client.sh`) runs with `-x` under WSL. It is a passive A–B offset monitor: `chronyc -h 127.0.0.1 sources`. Never set `SYNC_IN_CONTAINER=yes`; two daemons would fight over one clock.
- **Verified 2026-10-01:**
  - 20 min NTP probe B→A: mean +6.8 ms, sd 3.1 ms, max |offset| 13.8 ms, no trend.
  - `preflight --stage m1` skew: −1 ± 6 ms, within the 200 ms design gate.
- **If skew grows:** check `w32tm /query /status` on Windows first, then `wsl --shutdown`.

#### Cluster tuning (Human-Approved 2026-10-02)

Five changes to the cluster itself (not gate thresholds), made during M2 after the live-edge freshness test of 2026-10-01:

| Change | Design | Deployed | Where |
|---|---|---|---|
| Kubelet cAdvisor housekeeping interval on A | 10 s (kubelet default; backs off to 15 s) | **5 s** | `k8s/k3s/kubelet-housekeeping.sh` (K3s drop-in `kubelet-arg+`) |
| `frontend` CPU limit | 200m (upstream) | **400m** | `contract.yaml` `golden_overrides` (`CONTRACT-CHANGE`) → `golden.yaml` → `golden_live.json` |
| `currencyservice` CPU limit | 200m (upstream) | **300m** | same path as frontend |
| `recommendationservice` CPU limit (not managed) | 200m (upstream) | **500m** | same path; allow-listed in `env/contract.py` `UNMANAGED_OVERRIDABLE` |
| `redis-cart` image (not managed) | `redis:alpine` (floating) | **`redis:8.10.2-alpine@sha256:3811…e5a0`** | `golden_overrides` `image` key (digest required); allow-listed |

- **Housekeeping.** With the default interval the newest cAdvisor sample was a median 12 s / p95 21 s old at query time, so `rate(...[30s])` at the live edge returned nothing for 8–46 % of evaluations per managed deployment. §5.5 would mark those ticks stale and truncate episodes.
- **Frontend limit.** At the 50-user baseline the 200m frontend was ~99 % CFS-throttled before any fault. That would have made the baseline itself unhealthy and blurred F4 (surge) with steady-state saturation. Frontend stays the intended F4 bottleneck; this is re-checked by the F4 fault smoke in M3.
- **Currencyservice limit.** With frontend unthrottled, currencyservice at 200m was 0.41–0.46 throttled at the 50-user baseline, above the runbook's `throttle_threshold` (0.40). That made a healthy baseline look like F2 and made the M2 smoke gate (≥ 0.40) pass before any stress.
- **Recommendationservice limit (M3, G6 test).** At 50 users it was 57% throttled (PSI wait 9%) while 2–3% of page renders took > 500 ms, making tick-P99 bimodal (CV 0.29–0.52 vs G6 < 0.25). It is outside the agent's state and action space; raising its limit removes background noise, not a fault the agent should handle.

These threshold overrides and cluster-tuning changes are the **only** approved deviations. §4.5.5 still applies to every other gate, and to any further loosening.

---

## 3. Repository Layout

```
.
├── CLAUDE.md                     # this file (technical source of truth)
├── PLAN.md                       # milestones, commands, gates
├── requirements.txt              # pinned; torch installed separately (CPU wheel)
├── config/
│   ├── cluster.env               # LAN IPs, URLs, kubeconfig paths, pinned versions
│   ├── contract.yaml             # LOCKED parameters (§5.1)
│   ├── calibration.json          # generated by scripts/calibrate.py (L_SLA, RPS_base, U_base)
│   ├── golden_live.json          # generated by scripts/snapshot_golden.py
│   ├── runs/*.yaml               # per-run settings only (replay, seed, episodes)
│   └── kube/                     # GITIGNORED credentials
├── k8s/
│   ├── k3s/install-server.sh     # [A] K3s install
│   ├── boutique/                 # upstream manifest, build_golden.py, golden.yaml, namespace.yaml
│   ├── rbac/                     # agent + controller SA/Role/Binding/token Secret, make-kubeconfig.sh
│   ├── prometheus/               # rbac, config (keep-list), deploy (NodePort 30090)
│   └── hpa/hpa-baseline.yaml     # used ONLY for the k8s_hpa evaluation baseline
├── chaos/
│   ├── install-chaos-mesh.sh
│   └── smoke/                    # smoke-test manifests only; episode faults are built in code
├── locust/
│   ├── locustfile.py             # OB task mix, FastHttpUser, ring buffer, /tick route
│   └── run_locust.sh             # taskset -c 0,1
├── env/                          # the real-cluster Gymnasium environment
│   ├── contract.py  clock.py  telemetry.py  k8s_actions.py  golden.py
│   ├── injector.py  reward.py  recorder.py  boutique_env.py
├── agents/
│   ├── runbook.py  run_policy.py
│   ├── masked_discrete_sac.py  per_buffer.py  warmstart.py  train.py  probe.py
├── eval/
│   ├── make_schedule.py  run_eval.py  metrics.py  stats.py  plots.py
│   └── schedules/                # committed, immutable schedules
├── scripts/
│   ├── preflight.py  snapshot_golden.py  promq.py  record_ticks.py
│   ├── calibrate.py  fault_smoke.py  gates.py  watchdog.sh
├── tests/                        # pure-function tests + recorded real fixtures
│   └── fixtures/
├── figures/
└── data/                         # GITIGNORED: transitions, checkpoints, logs, runs, eval
```

---

## 4. Hard Rules

### 4.1 Reality Rules

1. **Never build a mock simulator, synthetic environment, or fake dynamics model** for training, evaluation, or "quick testing" of the agent. The only environment is `env/boutique_env.py` against the real cluster.
2. **Never fabricate, interpolate, or "fill in" metrics** beyond the imputation rules in §5.5. Never fake a result, a figure, or a log line.
3. **Tests are allowed, with limits:**
   - Unit tests may exercise pure functions (normalization, imputation, reward, mask, runbook rules, SAC math, PER math) using fixtures **recorded from the real cluster** (`scripts/record_ticks.py`) or tiny hand-written tensors.
   - Tests that touch the cluster are marked `@pytest.mark.cluster` and are never auto-run in CI.
4. **The agent never sees ground truth.**
   - Fault identity, target, severity, and injection time go only into `info` and the logs, never into `obs` or `mask`.
   - The runbook is held to the same rule.

### 4.2 Cluster Safety

1. **Write scope.**
   - Env and agent code may only modify the four managed deployments in namespace `boutique`, through the twelve catalog actions.
   - **No new actions.**
   - No other namespaces.
   - No other resource kinds.
2. **Replica bounds are enforced twice.** Both the mask and the executor enforce `max_replicas`:
   - frontend 3,
   - cartservice 2,
   - currencyservice 2,
   - productcatalogservice 1.

   **Never scale past these limits.** Never change container requests or limits from code. Never add nodes, services, or sidecars.
3. **The agent credential never deletes pods.** RESTART is a rollout-restart annotation patch.
4. **Never use `admin.kubeconfig` in `env/`, `agents/`, or `eval/`.** The executor holds only the agent client. Only `injector.py` and `reset()` hold the controller client. Never import the controller client in `agents/`.
5. **Never `subprocess` `kubectl` from `env/`, `agents/`, or `eval/`.** Use the official `kubernetes` Python client. `kubectl` is allowed in bootstrap scripts and docs.
6. Never touch `kube-system`, `chaos-mesh`, or `monitoring` from runtime code.

### 4.3 Remote-Call Rules (Kube API, Prometheus, Locust)

1. **Every remote call has an explicit timeout:**
   - Kube API: `_request_timeout=(2, 5)`.
   - Prometheus: `requests` with `timeout=(1.0, 2.5)`.
   - Locust: `timeout=(0.5, 1.5)`.
2. **Disable library-level retries.** The tick protocol handles failures.
3. **Wrap every remote call in `try/except`, catching specific exceptions:**
   - `kubernetes.client.exceptions.ApiException`
   - `urllib3.exceptions.HTTPError` (covers `MaxRetryError`, `ReadTimeoutError`, `ProtocolError`)
   - `requests.exceptions.RequestException`
   - `concurrent.futures.TimeoutError`
   - `ValueError` / `KeyError` / `json.JSONDecodeError` when parsing

   **Never use bare `except:`, and never swallow an exception silently.**
4. **What a caught exception must do:**
   - log a structured event (`event`, `component`, `error_type`, `tick`),
   - increment a counter,
   - return a **typed failure result** (e.g. `TelemetryResult(ok=False, ...)`).

   It must never return `None`, and NaN must never propagate.
5. **Nothing remote may block `step()` beyond its deadline.**
   - Collection runs concurrently under a 3.0 s hard deadline.
   - Action dispatch is asynchronous on a single-worker executor.
   - **Never wait for a rollout inside `step()`.**
6. **Never `time.sleep(<fixed duration>)` inside `step()`.** Only `clock.sleep_until(boundary)`.

### 4.4 Locked Parameters

1. Every number lives in `config/contract.yaml` or `config/calibration.json`. **No magic numbers in code.**
2. Locked values may not be changed without explicit human approval, recorded in the commit message (`CONTRACT-CHANGE: <reason>`). This covers:
   - all of `contract.yaml`,
   - `calibration.json`,
   - `golden_live.json`,
   - `eval/schedules/*`.
3. A run records the SHA-256 of `contract.yaml`, `calibration.json`, and `golden_live.json`, plus the git SHA. `eval/run_eval.py` refuses a dirty working tree.

### 4.5 Claude Code Operating Rules

1. **Ask before running** anything that mutates the cluster or spends wall-clock budget:
   - `kubectl apply|delete|patch|scale|rollout`
   - `helm install|upgrade|uninstall`
   - anything with `k3s`, `ssh` to A
   - any training, warm-start, evaluation, or calibration run
   - deleting anything under `data/`

   Read-only commands are fine without asking: `get`, `describe`, `logs`, `top`, `auth can-i`, PromQL queries, `/tick` reads.
2. **Never run long jobs in the foreground.** Use `nohup bash scripts/watchdog.sh ... > data/logs/<run>.log 2>&1 &` and report the PID and log path.
3. Never edit generated files: `k8s/boutique/golden.yaml`, `config/golden_live.json`, `config/calibration.json`, `eval/schedules/*`.
4. **Never upgrade pinned dependencies mid-project.** Before using a Tianshou API, read the installed version's source; don't assume docs from another version apply.
5. If a validation gate fails, **fix the cause.** Never loosen a gate threshold. (Only exceptions: the human-approved environment overrides in §2.)

---

## 5. Contracts

### 5.1 `config/contract.yaml` (Initial Content)

```yaml
cluster:
  namespace: boutique
  managed: [frontend, cartservice, currencyservice, productcatalogservice]   # fixed order = obs order
clock:
  tick_s: 20
  collect_deadline_s: 3.0
  update_budget_s: 1.0
  inflight_timeout_s: 100
  late_frac: 0.5                    # step() entered after T_k + late_frac*tick_s → flag "late"
telemetry:
  scrape_interval_s: 5
  rate_window: "30s"
  prom_timeout_s: [1.0, 2.5]
  k8s_timeout_s: [2, 5]
  locust_timeout_s: [0.5, 1.5]
  locf_max_ticks: 2
  stale_truncate_ticks: 3
locust:
  wait_s: [0.5, 1.5]
  request_timeout_s: 5
  cpu_cores: [0, 1]
sla:
  e_sla: 0.01
  e_max: 0.20
  recovery_ticks: 3
  l_sla_factor: 1.5                 # L_SLA = ceil_10ms(1.5 * q95(steady tick-P99)) → calibration.json
episode:
  lead_in_ticks: [2, 5]
  fault_max_ticks: 18
  null_extra_ticks: 6
  fault_probs: {F1: 0.22, F2: 0.22, F3: 0.22, F4: 0.22, "NULL": 0.12}   # quoted: bare NULL is YAML null
  f1_latency: ["300ms", "600ms", "1s"]
  f2_targets: [currencyservice, cartservice]
  f2_workers: [1, 2]
  f3_targets: [cartservice, currencyservice]
  f4_multiplier: [2.5, 3.0]
replicas:
  base: {frontend: 1, cartservice: 1, currencyservice: 1, productcatalogservice: 1}
  max:  {frontend: 3, cartservice: 2, currencyservice: 2, productcatalogservice: 1}
golden_overrides: {}                # e.g. {currencyservice: {cpu_limit: "300m"}} only if F4 calibration requires it
reward:
  action_cost: {NOOP: 0.0, SCALE: 0.02, RESTART: 0.04, RESTORE: 0.04}
  w_replica: 0.05
  replica_denominator: 4            # Σ(max - base) = 2+1+1+0
rl:
  gamma: 0.93
  n_step: 3
  tau: 0.005
  hidden: [256, 256]
  lr_actor: 3.0e-4
  lr_critic: 3.0e-4
  lr_alpha: 3.0e-4
  batch_size: 128
  buffer_size: 50000
  updates_per_tick: 4
  alpha_init: 0.1
  target_entropy_frac: 0.4
  offline_warmstart_steps: 2000
  torch_threads: 2
per:
  alpha: 0.5
  beta_start: 0.4
  beta_end: 1.0
  beta_anneal_grad_steps: 15000
  eps: 1.0e-3
  priority_cap: 1.0
runbook:
  throttle_threshold: 0.40
  debounce_ticks: 2
  change_window_ticks: 9
  surge_rps_factor: 1.5
  scaleback_rps_factor: 1.2
  scaleback_healthy_ticks: 6
  restart_cooldown_ticks: 6
  warmstart_epsilon: 0.25
  warmstart_episodes: 120
```

`config/calibration.json` (generated) contains: `l_sla_ms`, `rps_base`, `u_base`, `calibrated_at`, `env_git_sha`.

`u_base` is the Locust user count that puts the **frontend at ~50–60 % CPU utilization relative to its limit** (`cpu_util[frontend]`, §5.3) in steady state with no fault (redefined 2026-10-02, human-approved; previously ≈ 50 % of Machine A's host CPU, which on a 16-core A would overload the 200–500m service limits). `rps_base` and `L_SLA` are measured at that `u_base`.

### 5.2 Action Catalog (|A| = 12) and Kubernetes Mechanics

| ID | Action | Kind | Target |
|---|---|---|---|
| 0 | NOOP | NOOP | — |
| 1 | RESTART frontend | RESTART | frontend |
| 2 | RESTART cartservice | RESTART | cartservice |
| 3 | RESTART currencyservice | RESTART | currencyservice |
| 4 | RESTART productcatalogservice | RESTART | productcatalogservice |
| 5 | SCALE_UP frontend | SCALE | frontend (+1) |
| 6 | SCALE_DOWN frontend | SCALE | frontend (−1) |
| 7 | SCALE_UP cartservice | SCALE | cartservice (+1) |
| 8 | SCALE_UP currencyservice | SCALE | currencyservice (+1) |
| 9 | RESTORE productcatalogservice | RESTORE | productcatalogservice |
| 10 | RESTORE cartservice | RESTORE | cartservice |
| 11 | RESTORE currencyservice | RESTORE | currencyservice |

**Mechanics (agent client, namespace `boutique`):**

- **RESTART**: strategic-merge patch. This is a zero-downtime rollout restart.
  ```python
  patch_namespaced_deployment(name, ns, {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": <ISO8601 now>}}}}})
  ```
- **SCALE**:
  ```python
  patch_namespaced_deployment_scale(name, ns, {"spec": {"replicas": n}})
  ```
  The executor re-checks `1 ≤ n ≤ max[d]` and refuses otherwise.
- **RESTORE**: a **JSON Patch** (list body, sent as `application/json-patch+json`):
  ```
  [{"op":"replace","path":"/spec/template","value":<golden template>},
   {"op":"replace","path":"/spec/replicas","value":base[d]}]
  ```
  - A strategic-merge patch is **wrong** here: it merges `env` lists by name and would not remove `EXTRA_LATENCY`.
  - If the live template hash (ignoring `restartedAt`) and the replica count already match golden, make **no API call**, but still charge the cost and log the action.
- **Golden template source.** `config/golden_live.json`, captured from the API right after a verified clean apply. Live objects contain API-defaulted fields, so comparing against the raw YAML always shows a difference.
- **Never mask RESTORE based on drift from golden.** That would leak the fault identity.

**Expected outcome grid** (C = cure, P = partial, W = wasteful, H = harmful, — = masked). This is used for documentation and interpretability plots, **never as agent input**:

| Action | F1 pc bad deploy | F2 hot pod X | F3 X scaled to 0 | F4 surge | NULL |
|---|---|---|---|---|---|
| 0 NOOP | no progress | no progress | no progress | no progress | C |
| RESTART X | W (bad env persists) | **C** | — | W | W |
| RESTART fe | W | W | W | H | W |
| SCALE_UP X | W | P | **C** | W | W |
| SCALE_UP fe | W | W | W | **C** | W |
| RESTORE pc | **C** | W | W | W | W |
| RESTORE X | W | W (template unchanged) | **C** | W | W |

### 5.3 Observation Contract

`observation_space = Dict({"obs": Box(-1.0, 1.0, (36,), float32), "mask": MultiBinary(12)})`

`action_space = Discrete(12)`

`d` iterates over `managed` in contract order; per-service features occupy indices 5–32.

| Index | Name | Source | Formula |
|---|---|---|---|
| 0 | p99 | Locust `/tick` | `clip(log2(1 + P99_ms/L_SLA) / log2(21), 0, 1)` |
| 1 | fail | Locust | `failures / total` |
| 2 | rps | Locust | `clip(RPS / (3·RPS_base), 0, 1)` |
| 3 | d_p99 | derived | `p99_t − p99_{t−1}` (0 on the first tick of an episode) |
| 4 | d_fail | derived | `fail_t − fail_{t−1}` |
| 5+7i+0 | cpu_util[d] | Q_cpu | `clip(cpu_cores / (limit_cores[d] · max(1, status.replicas)), 0, 1)` |
| 5+7i+1 | throttle[d] | Q_thr | `clip(value, 0, 1)` |
| 5+7i+2 | mem_util[d] | Q_mem | `clip(bytes / (limit_bytes[d] · max(1, status.replicas)), 0, 1)` |
| 5+7i+3 | ready[d] | deployment status | `clip(availableReplicas / base[d], 0, 1)` |
| 5+7i+4 | replicas[d] | deployment spec | `spec.replicas / max[d]` |
| 5+7i+5 | restarts[d] | pod list | `min(max(0, Σ restartCount_t − Σ restartCount_{t−1}), 3) / 3` |
| 5+7i+6 | rollout_recency[d] | `metadata.generation` | `exp(−ticks_since_generation_change[d] / 5)`; 0 if no change since episode start |
| 33 | in_flight | env | 1 if the action lock is held |
| 34 | ticks_since_action | env | `min(k, 10) / 10` (1.0 if no action yet this episode) |
| 35 | telemetry_stale | env | 1 if any imputation (rules 3–4 of §5.5) or failed source this tick |

`limit_cores[d]` and `limit_bytes[d]` are read once from `golden_live.json`.

### 5.4 PromQL (Verbatim; Evaluated at B's Boundary Time, Concurrently)

```
DEP(x) := label_replace(x, "deployment", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$")

Q_cpu = sum by (deployment) (DEP(rate(container_cpu_usage_seconds_total{namespace="boutique",container="server"}[30s])))

Q_thr = sum by (deployment) (DEP(rate(container_cpu_cfs_throttled_periods_total{namespace="boutique",container="server"}[30s])))
      / sum by (deployment) (DEP(rate(container_cpu_cfs_periods_total{namespace="boutique",container="server"}[30s])))

Q_mem = sum by (deployment) (DEP(container_memory_working_set_bytes{namespace="boutique",container="server"}))
```

- Use `GET $PROM_URL/api/v1/query` with `query=` and `time=<B unix float>`.
- Keep only the rows whose `deployment` is in `managed`.

**Prometheus keep-list** (`metric_relabel_configs`, cAdvisor job):
- keep `__name__` in the four metrics above;
- keep `namespace="boutique"`;
- drop everything else.

Node-level CPU for calibration comes from metrics-server (`metrics.k8s.io`) through the admin kubeconfig in `scripts/calibrate.py` only.

**Kubernetes reads per tick** (agent client):
- `list_namespaced_deployment`: `spec.replicas`, `status.{replicas, availableReplicas, updatedReplicas, observedGeneration}`, `metadata.generation`.
- `list_namespaced_pod`: per-deployment sum of `containerStatuses[].restartCount`.

**Locust `/tick?from=<unix>&to=<unix>`** returns JSON:

```json
{"n": int, "failures": int, "p50_ms": float, "p99_ms": float, "rps": float}
```

- The window is exactly `(wall(T_k), wall(T_{k+1})]`.
- Every request records `(t_B, response_ms, success)`.
- A timeout counts as a failure with `response_ms = 5000`.

### 5.5 Imputation (Applied in Order; Per Deployment d, Per Metric m)

1. **No pods** (`status.replicas == 0`): cpu = throttle = mem = 0. This is a true value, not imputation.
2. **Query returned a finite value:** use it.
3. **Missing, NaN, or 0/0, and the last valid value is ≤ `locf_max_ticks` old:** carry the last value forward; set `telemetry_stale = 1`.
4. **Otherwise:** 0; set `telemetry_stale = 1`.
5. **Restart counter decreased** (a pod disappeared): delta = 0.
6. **Locust source:**
   - Endpoint failed, or `n == 0` (Locust is dead): carry forward p99/fail/rps and set stale.
   - Requests completed as timeouts: these are real data, not imputation.
7. **Whole-source failure** (Prometheus or Kube API unreachable): apply rule 3/4 to everything from that source; set stale.
8. Assert `np.all(np.isfinite(obs))` before returning. On failure, log and raise. **Never return NaN.**

**Storage and validity:**
- A transition whose **s′** has `telemetry_stale = 1` is logged but **not inserted** into any replay buffer.
- `stale_truncate_ticks` (3) consecutive stale ticks → `truncated = True`, `info["valid"] = False`, and the episode is excluded from evaluation.

### 5.6 Reward, Health, Recovery

```
ℓ_t = clip( log2(P99_t / L_SLA) / 3, 0, 1 )
e_t = clip( (F_t − e_sla) / (e_max − e_sla), 0, 1 )
v_t = clip( ℓ_t + e_t, 0, 1 )
ρ_t = Σ_d max(0, spec_d − base_d) / replica_denominator
r_k = −( v_{k+1} + action_cost[kind(a_k^exec)] + w_replica · ρ_{k+1} )        # r ∈ [−1.09, 0]
```

**Reward rules:**
- `a_k^exec` is the **executed** action. A failed dispatch is stored and charged as NOOP.
- There is **no ΔMTTR term.** Do not add reward shaping beyond this formula.

**Health and recovery:**
- **Healthy tick:** `H_t := P99_t ≤ L_SLA ∧ F_t ≤ e_sla`.
- **Recovered at tick t:** `t > k_inject ∧ H_t ∧ H_{t+1} ∧ H_{t+2} ∧ cure_condition(fault)`. Termination happens at tick t+2.

**MTTR and episode endings:**
- **MTTR** = `wall(T_t) − t_inject_wall` (s), where `t` is the first of the three healthy ticks.
- In evaluation, also compute **fine MTTR** from Locust's per-second aggregates.
- **Terminated:** recovered (or never, for NULL episodes).
- **Truncated:** `fault_max_ticks` reached after injection; NULL episodes at `L + null_extra_ticks`; or the stale rule.
- **Bootstrapping:** the critic bootstraps on truncation and never on termination.

### 5.7 Tick Protocol

The schedule is `T_k = T_0 + k·tick_s`, using `time.monotonic()`. `wall(T_k)` is recorded when each boundary is reached. `T_0` is re-anchored in `reset()`.

```
step(a):
  t_enter = mono()
  if t_enter >= T_{k+1}:                       # missed the tick entirely
      skip dispatch; a_exec = NOOP; valid = False; realign to next boundary
  late = t_enter > T_k + late_frac * tick_s
  if not mask_k[a]: log("mask_violation"); a = NOOP
  if a != NOOP:
      lock.acquire(a, target, t_dispatch=wall_now(), gen_before)
      future = executor.submit(execute, a)     # agent client, timeout (2,5)
  injector.on_tick(k)                          # controller client; injects iff k == L; records t_inject_wall
  clock.sleep_until(T_{k+1})
  raw = collect(window=(wall(T_k), wall(T_{k+1})), deadline=collect_deadline_s)   # concurrent: Locust, Q_cpu, Q_thr, Q_mem, deployments, pods
  resolve(future): on error/timeout → exec_error=True, a_exec=NOOP, lock.release()
  resolve_inflight(raw)                        # §5.8
  injector.check_cure(raw)                     # F2: target pod UID gone → delete StressChaos CR immediately
  obs = build_obs(raw, history)                # §5.3 + §5.5
  r = reward(raw, a_exec)                      # §5.6
  terminated, truncated = done_flags(...)
  mask_next = compute_mask(raw, lock)          # §5.8
  recorder.append(transition)                  # flush every tick
  k += 1
  return {"obs": obs, "mask": mask_next}, r, terminated, truncated, info
```

**Learner interaction.** The learner runs `updates_per_tick` (4) gradient steps between `step()` calls, hard-capped at `update_budget_s` (1.0 s). Decision latency (window close → dispatch) is logged every tick, with a target of p95 ≤ 4 s.

**Required `info` keys:**
- `tick`, `phase` (`lead_in|fault|post`), `fault`, `target`, `severity` (ground truth, **never in obs**)
- `a_chosen`, `a_exec`, `exec_error`, `late`, `valid`, `stale`
- `decision_latency_s`, `inflight`, `cured`, `recovered`, `mttr_s` (at episode end)

### 5.8 In-Flight Lock & Mask

There is **one global lock** at a time. It clears when the target deployment is rollout-complete:

```
generation == observedGeneration ∧ updatedReplicas == spec.replicas
∧ availableReplicas == spec.replicas ∧ status.replicas == spec.replicas
```

It also clears:
- after `inflight_timeout_s` (log `stalled`),
- immediately on a dispatch error,
- at the next collection, for a no-call RESTORE.

**Mask rules:**
- NOOP is always valid.
- If the lock is held, only NOOP is valid.
- Otherwise:
  - RESTART d is invalid if `spec_d == 0`.
  - SCALE_UP d is invalid if `spec_d ≥ max[d]`.
  - SCALE_DOWN frontend is invalid if `spec ≤ 1`.
  - RESTORE is always valid.

### 5.9 `reset()` — Active Restore

1. **Remove faults (controller client).**
   - Delete all StressChaos CRs in `boutique`.
   - `POST /swarm` on Locust with `user_count = U_base`.
   - List CRs and confirm there are none.
2. **Restore golden state.** RESTORE all four managed deployments to `golden_live.json` (template + base replicas).
3. **Wait for convergence.** Poll every 5 s until every deployment in `boutique` is rollout-complete **and** Prometheus and Locust both answer. Deadline 180 s.
4. **Escalate if needed.**
   - Deadline missed → **hard reset:** delete all pods in `boutique`, then wait up to another 180 s.
   - Still failing → raise `EnvironmentDegraded`. The watchdog pauses and alerts. **Never continue training on a degraded cluster.**
5. **Settle.** Wait ≥ 30 s after the last pod change, then require 3 consecutive healthy ticks (`H_t`).
6. **Start the episode.**
   - Re-anchor `T_0`; clear the lock, the history, and the LOCF cache.
   - Sample the plan (fault, target, severity, `L`) from `episode.*` using the env RNG (seeded). In eval, read the plan from the schedule instead.
   - Arm the injector.
   - Return `({"obs": s_0, "mask": mask_0}, info)`.

### 5.10 Fault Injector (`env/injector.py`, Controller Client Only)

| Fault | Injection at tick L | Cure condition |
|---|---|---|
| F1 | Strategic-merge patch: add env `EXTRA_LATENCY=<severity>` to container `server` of `productcatalogservice` | Live template has no `EXTRA_LATENCY` ∧ rollout-complete |
| F2 | Assert exactly 1 running pod of X; record its UID; create the StressChaos below | Recorded UID no longer exists → **delete the CR immediately** |
| F3 | `patch_namespaced_deployment_scale(X, replicas=0)` | `availableReplicas(X) ≥ 1` |
| F4 | Locust `POST /swarm` with `user_count = round(m·U_base)` and a high spawn rate | Always true (the SLA predicate alone decides) |

The F2 StressChaos manifest:

```yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: StressChaos
metadata: {name: f2-<run>-<episode>, namespace: boutique}
spec:
  mode: one
  selector: {namespaces: [boutique], labelSelectors: {app: <X>}}
  containerNames: [server]
  stressors: {cpu: {workers: <w>, load: 100}}
  duration: "30m"
```

### 5.11 Scripted Runbook (`agents/runbook.py`)

- Inputs: the denormalized observation, plus the runbook's own memory of its past actions.
- It never reads `info`.
- Rules are evaluated in order every tick; the first match fires.
- `¬H(n)` means unhealthy for the last n ticks. θ = `throttle_threshold`.

| Rule | Condition | Action |
|---|---|---|
| R0 | `in_flight` | NOOP |
| R1 | ∃d: `spec_d < base_d` | RESTORE d |
| R2 | `¬H(2)` ∧ ∃d with a generation change within `change_window_ticks` that the runbook did not cause, and RESTORE d exists | RESTORE d (most recent change) |
| R3 | `¬H(2)` ∧ `RPS ≥ 1.5·RPS_base` ∧ `throttle_fe ≥ θ` ∧ `spec_fe < 3` | SCALE_UP frontend |
| R4 | `¬H(2)` ∧ `RPS < 1.5·RPS_base` ∧ ∃d: `throttle_d ≥ θ` for 2 ticks ∧ d not restarted by the runbook within `restart_cooldown_ticks` | RESTART argmax throttle |
| R5 | `H` for 6 ticks ∧ `RPS < 1.2·RPS_base` | SCALE_DOWN fe if `spec_fe > 1`; else RESTORE any d with `spec_d > base_d` |
| R6 | otherwise | NOOP (log `unknown_incident` if `¬H(6)` since last action) |

**Warm-start mode.**
- With probability `warmstart_epsilon`, take a uniformly random **valid** action instead.
- Transitions are tagged `source ∈ {runbook, eps}`.

### 5.12 Masked Discrete SAC + PER Implementation Rules

1. **Masking.**
   - `logits = where(mask, logits, -1e9)`.
   - Compute `log_probs` with `log_softmax`, then **zero out masked entries before any `π·logπ` product.** Otherwise 0 · (−inf) produces NaN.
2. **Critic target.**
   - `V(s′) = Σ_{a valid in mask′} π(a|s′) · [min(Q̄₁, Q̄₂)(s′, a) − α · log π(a|s′)]`, using **target** critics with Polyak τ.
   - n-step returns with `γ^n`.
   - The bootstrap mask uses `terminated` only.
3. **Critic loss.** IS-weighted squared TD error. IS weights go on the critic loss **only**.
4. **Actor loss.** `E_s[ Σ_{a valid} π(a|s) · (α · log π(a|s) − min Q(s, a)) ]`.
5. **Temperature α.**
   - Optimize `log α`.
   - Per-state target entropy `H̄(s) = target_entropy_frac · log(n_valid(s))`.
   - `loss_α = mean_{s: n_valid(s) ≥ 2}[ exp(log α) · (H(π(·|s)) − H̄(s)).detach() ]`.
   - **States with a single valid action are excluded.**
   - Compute this over the sampled batch and log the fraction of states excluded.
6. **Priorities.**
   - `p_i = min(mean(|δ₁|, |δ₂|), priority_cap) + eps`.
   - New transitions get the current max priority.
   - Update priorities after every gradient step.
7. **β.** Linear from `beta_start` to `beta_end` over **gradient steps** (not env steps). Persist the step counter in the checkpoint.
8. **Uniform ablation.** Identical code with prioritization disabled (uniform sampling, weights = 1). This is a config switch, not a fork.
9. **Evaluation and probes.** Greedy: `argmax` over masked logits.
10. **Checkpoints.**
    - Contents: actor, critics, target critics, `log α`, all optimizers, gradient step, episode count, RNG states, and the contract and calibration hashes.
    - Written every 25 episodes and on SIGTERM.
    - On resume, the buffer is rebuilt from the run's JSONL transitions, with priorities reset to max.
11. **Tianshou usage.**
    - Wrap envs with `DummyVectorEnv` with **exactly one** env (one real cluster). Never use `SubprocVectorEnv`.
    - If Tianshou's discrete SAC cannot support rules 1–5 cleanly, subclass it and override `learn`.
    - An in-house SAC that uses only Tianshou's buffers needs human approval.

---

## 6. Logging, Persistence, Crash Safety

**Transition log.** One JSONL line per tick at `data/transitions/<run>/episode_<n>.jsonl`, flushed every tick.

Fields:
- `run`, `episode`, `tick`, `t_wall`, `phase`, `fault`, `target`, `severity`, `source`
- `obs`, `mask`, `a_chosen`, `a_exec`, `reward`, `next_obs`, `next_mask`
- `terminated`, `truncated`, `valid`, `stale`
- `raw` (unnormalized P99/fail/RPS and per-service values)
- `decision_latency_s`, `exec_error`, `late`

**Event log.** Structured JSON logs (one event per line) at `data/logs/<run>.events.jsonl`. Never print-debug in runtime code.

**Watchdog.** `scripts/watchdog.sh`:
- restarts from the latest checkpoint (training) or the next episode (`run_policy` / `run_eval`),
- stops after 3 consecutive failures,
- writes `data/logs/ALERT`.

**Signals.** Every long-running entry point handles SIGTERM/SIGINT by:
1. finishing the current tick,
2. releasing nothing on the cluster mid-action,
3. checkpointing,
4. exiting 0.

---

## 7. Coding Conventions & Testing

- **Language and tooling.** Python 3.11, type hints everywhere, `@dataclass(frozen=True)` for config and results, `ruff` clean, `pytest` green.
- **Units in names:** `_ms`, `_s`, `_bytes`, `_cores`.
- **Observations** are `np.float32`, shape `(36,)`.
- **Masks** are `np.int8`, shape `(12,)`.
- **Purity.** Pure functions — normalization, imputation, reward, mask, runbook decision, SAC losses, PER math — live apart from I/O and have unit tests.
- **Seeds.** One seed per run, fed to Python, NumPy, and Torch, and to the env RNG that samples fault plans. Cluster physics is not seedable; never claim otherwise.
- **Startup.** On startup, every entry point loads and validates `contract.yaml` (schema and types) and refuses to run if `calibration.json` is missing or its `env_git_sha` is older than the latest commit touching `env/`. The exceptions are `scripts/calibrate.py` itself and `scripts/record_ticks.py`, which runs before calibration and records raw, unnormalized telemetry only (human-approved 2026-10-02). Both still load and validate `contract.yaml`.

---

## 8. Known Pitfalls (Read Before Debugging)

1. **Chaos Mesh install values on K3s.** The containerd socket is `/run/k3s/containerd/containerd.sock`. Install with:
   - `chaosDaemon.runtime=containerd`
   - `chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock`
   - `controllerManager.enableFilterNamespace=true`
   - `dashboard.create=false`

   Then annotate `boutique` with `chaos-mesh.org/inject=enabled`. Without the correct socket, the chaos daemon silently does nothing.
2. **Container names.** All Online Boutique app containers are named `server`. Aggregate by deployment through the pod-name regex, never by container.
3. **Freshness.** cAdvisor refreshes every 10 s by default (5 s on the deployed A, §2 "Cluster tuning"), so `rate(...[30s])` is the shortest reliable window. Fresh pods produce empty results for one or two ticks; this is expected and handled by §5.5.
4. **Patch type.** Strategic-merge patches cannot remove `EXTRA_LATENCY`. RESTORE must use JSON Patch `replace /spec/template`.
5. **Golden comparison.** Comparing the live template to the raw YAML always differs (API defaulting). Compare against `golden_live.json` and ignore `restartedAt`.
6. **Scale-to-zero.** A deployment at `replicas: 0` has no pods, so its Prometheus series vanish. That is imputation rule 1, not staleness.
7. **HPA and zero replicas.** HPA does not act on a deployment at zero replicas, so F3 stays unhealed in the `k8s_hpa` baseline. This is correct behavior, not a bug.
8. **Locust headless mode** has no web UI, and therefore no `/swarm` or `/tick` routes. Run with the web UI bound to 127.0.0.1 and autostart (or a one-time `/swarm` at launch), plus the custom `/tick` route.
9. **B CPU contention.** When gradient updates starve Locust's event loop, measured P99 inflates. Keep Locust on cores 0–1 and torch on the remaining cores with 2 threads.
10. **Clock skew.** Skew between A and B shifts Prometheus `time=` evaluation. Preflight fails if skew exceeds 200 ms (design gate; the earlier 1000 ms override was retired 2026-10-01). Under WSL2, B's clock is the Windows host clock; fix skew on Windows (w32time → A), not inside WSL (§2 "Clock-sync topology").
11. **Masking NaN.** The masked-entropy 0 · log 0 NaN (§5.12 rule 1) and an unfiltered α loss (§5.12 rule 5) are the two most likely causes of a "mysteriously diverging" agent.

---

## 9. Definition of Done (Any Change)

- [ ] Follows §4 hard rules; no new actions, namespaces, resource kinds, or magic numbers.
- [ ] Every remote call has a timeout and specific `try/except` with a structured log and a typed failure result.
- [ ] Unit tests added or updated for any pure logic touched; `pytest -q` and `ruff check .` pass.
- [ ] If it touches `env/`, the relevant `scripts/preflight.py` stage and an affected-fault `scripts/fault_smoke.py` run pass on the real cluster (ask before running).
- [ ] No locked file changed without a `CONTRACT-CHANGE:` commit and human approval.
