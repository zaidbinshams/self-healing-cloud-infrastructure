# Capstone Defense Notes — Self-Healing Microservices with Masked Discrete SAC (PER-iSAC) on K3s

> **Purpose.** Backbone for the defense presentation and a future paper. Every number here comes from a commit, a preflight run, or a file under `data/diag/` (git-ignored raw data; the scripts that produce it are committed). Claims are separated into **established**, **in progress**, and **planned**. Status as of **2026-10-02**, M3 in progress.
>
> **Sources of truth:** `CLAUDE.md` (technical contract), `PLAN.md` (milestones), `config/contract.yaml` (locked numbers), `reports/m1_report.md`, `reports/m2_report.md`, `reports/rolling_project_log.md`.

---

## Contents

1. [Overall Project Objective](#1-overall-project-objective)
2. [Project Novelty & the Constrained Edge-Like Environment](#2-project-novelty--the-constrained-edge-like-environment)
3. [Architectural Decisions — Lean by Design](#3-architectural-decisions--lean-by-design)
4. [Preserving Fault Physics — Load vs. Limits](#4-preserving-fault-physics--load-vs-limits)
5. [Metrics & Baseline Definition](#5-metrics--baseline-definition)
6. [The RL Agent — Masked Discrete Soft Actor-Critic with PER](#6-the-rl-agent--masked-discrete-soft-actor-critic-with-per)
7. [The M3 Debugging Investigation (Gate G6)](#7-the-m3-debugging-investigation-gate-g6)
8. [Stage-by-Stage Execution Plan](#8-stage-by-stage-execution-plan)
9. [Current Status, Open Items & Threats to Validity](#9-current-status-open-items--threats-to-validity)

---

## 1. Overall Project Objective

**Goal.** Engineer an autonomous, reinforcement-learning-driven self-healing controller for a microservice application. The controller detects and remediates injected faults in real time, minimizing **time to recovery** while respecting a latency/error **SLA** and avoiding wasteful actions and surplus replicas. It runs on a **real** cluster in a constrained, noisy, edge-like environment.

| Element | Choice |
|---|---|
| System under test | Google **Online Boutique** `v0.10.7` (11 deployments) on single-node **K3s** |
| Agent | **Masked discrete Soft Actor-Critic** with **Prioritized Experience Replay** (PER-iSAC) |
| Managed services | `frontend`, `cartservice`, `currencyservice`, `productcatalogservice` |
| Faults | F1 bad deploy (latency in productcatalog), F2 CPU-hot pod (Chaos Mesh StressChaos), F3 service scaled to zero, F4 traffic surge |
| Baselines | default Kubernetes, Kubernetes + HPA (70% CPU), scripted SRE runbook |
| **Headline metric** | **MTTR** under one fixed, committed fault schedule |

**Core principle — real system physics only.** No simulator, synthetic environment, or learned dynamics model is ever used for training or evaluation (`CLAUDE.md` §4.1). The scientific value of the project depends on this.

---

## 2. Project Novelty & the Constrained Edge-Like Environment

### 2.1 Two contributions

1. **Method.** A safety-masked discrete SAC with prioritized replay that acts on a live Kubernetes cluster through a small, auditable action catalog. It is warm-started from a scripted runbook and evaluated against standard Kubernetes self-healing (default reconciliation and HPA).
2. **Setting.** The agent runs and is evaluated on real hardware under conditions typical of the **edge**, not a clean data center.

> **Claim discipline.** That the agent *succeeds* in this setting is the **hypothesis under test** in M4–M5, not a finding yet. What is established so far is the setting itself (M1–M2) and its measured noise characteristics (M3).

### 2.2 The deployed environment, as measured

| Property | Design (clean lab) | Deployed (measured) | Edge analogy |
|---|---|---|---|
| A ↔ B link | wired, RTT p99 < 2 ms | **Windows mobile hotspot**, RTT p99 **187 ms** (n = 200), occasional single-sample timeouts | wireless backhaul; variable last-mile latency |
| Machine B | Linux host | **WSL2 behind NAT** on Windows; its clock is owned by the Windows host | heterogeneous, partially managed nodes |
| Clock | B is the authority | A serves NTP → Windows w32time → WSL2 (PHC0); skew **+6.8 ms mean, max 13.8 ms** over 20 min | no reliable data-center time source |
| Machine A | dedicated, 12 GB | 16-core laptop, **shared with unrelated workloads**, about 8.8–10.7 GiB in use at idle | multi-tenant edge box |

All results are reported **as obtained under this environment** (`CLAUDE.md` §2, Environment Overrides). Three preflight thresholds were relaxed **with human approval** (RTT < 800 ms, A idle memory < 10 GiB, A loaded memory < 12 GiB). No other gate was loosened, and the clock-skew gate is back at its 200 ms design value.

### 2.3 Why this matters for RL

Edge-like noise affects learning in three places:
- the **observation** (stale or delayed telemetry);
- the **transition** (variable action latency);
- the **reward** (a jittery p99 baseline).

The project treats these as first-class:
- typed telemetry failures and bounded imputation (§5.3);
- a fixed wall-clock tick with a hard collection deadline;
- stability gates (G2, G6) that must pass *before* any learning starts.

---

## 3. Architectural Decisions — Lean by Design

```
Machine A (K3s, 16 vCPU, shared)                      Machine B (WSL2, clock authority)
┌──────────────────────────────────────┐              ┌─────────────────────────────────────┐
│ boutique: Online Boutique (11 deps)  │◄── :30080 ───┤ Locust (cores 0–1) + /tick window API│
│ chaos-mesh: controller + daemon      │◄── :6443 ────┤ env/ telemetry + action executor    │
│ monitoring: Prometheus (keep-list)   │◄── :30090 ───┤ fault injector / reset (controller)  │
│ metrics-server (for HPA baseline)    │              │ runbook / PER-iSAC (CPU torch)       │
└──────────────────────────────────────┘              └─────────────────────────────────────┘
```

| Decision | Rationale |
|---|---|
| **No Grafana** | Grafana is a human visualization layer. The agent needs *programmatic*, time-exact access, so it queries the Prometheus HTTP API directly with an explicit `time=` from B's clock and contract timeouts (`env/telemetry.py`). Dashboards add CPU and memory on A while giving the agent nothing. |
| **Minimal Prometheus, not a monitoring stack** | One Prometheus pod scraping only kubelet cAdvisor every 5 s, with a **keep-list of 4 metrics** in namespace `boutique` (CPU usage, CFS throttled periods, CFS periods, working-set memory). Head series **125** (< 1000), memory **44 MiB**. Orphaned `kube-prometheus` services from an earlier install were removed in M1. |
| **No sidecars, no service mesh** | Sidecars would add per-pod CPU and latency, and would change the very physics we measure. `CLAUDE.md` forbids adding sidecars. |
| **Chaos Mesh without dashboard; Traefik disabled** | The same lean principle; namespace filtering limits chaos to `boutique`. |
| **Locust on B, pinned to cores 0–1** | Load generation never competes with the cluster for CPU, and learner compute on B cannot starve Locust's event loop. |
| **Least-privilege credentials** | Three kubeconfigs (admin / agent / controller). The agent can patch four deployments but **cannot delete pods**; verified by 18 + 19 SelfSubjectAccessReview cases. |
| **Kubelet housekeeping 10 s → 5 s** | Fixes live-edge staleness of `rate(...[30s])` (missing CPU data 33–50% → **0/24**). See `reports/m2_report.md`. |

---

## 4. Preserving Fault Physics — Load vs. Limits

### 4.1 What "fault physics" means here

The agent learns which remedy cures which fault from *observed consequences*. Those consequences are produced by real CPU limits and real load:

| Fault | Physical mechanism the agent must observe | Correct remedy |
|---|---|---|
| F2 hot pod | StressChaos consumes the target's CPU **quota** → CFS throttle ratio ≥ 0.40 | RESTART the target (new pod, no stressor) |
| F4 surge | 2.5–3× users saturate the **frontend's quota** | SCALE_UP frontend |
| F1 bad deploy | productcatalog's template carries `EXTRA_LATENCY` | RESTORE productcatalog |
| F3 scaled to zero | target has no pods | SCALE_UP / RESTORE the target |

CPU limits are part of the **definition of the faults**, and they are also the denominators of the agent's `cpu_util` features.

### 4.2 Two kinds of limit change — and when each is justified

**Raised (M2–M3, human-approved `CONTRACT-CHANGE`s) — because the *baseline* was broken:**

| Service | Change | Evidence of a broken baseline |
|---|---|---|
| `frontend` | 200m → **400m** | ~99% CFS-throttled at 50 users with *no fault* |
| `currencyservice` | 200m → **300m** | throttle 0.41–0.46 at baseline, above the runbook's 0.40 fault threshold |
| `recommendationservice` (not managed) | 200m → **500m** | 57% throttled, CPU-pressure wait 9% (G6 investigation) |

**Not raised — because the remaining problem was load, not a broken baseline.** After the recommendationservice change, `frontend` and `productcatalogservice` rose to about 25% throttled at 50 users. Raising their limits again was rejected for two reasons:
- **F4 would lose its signal.** With more frontend headroom, a 2.5–3× surge may no longer saturate frontend. SCALE_UP frontend would then stop being the uniquely correct action, and the agent could not learn F4 from the data.
- **Fault calibration would drift.** F2's severity (1–2 stress workers against the quota) and the throttle ratios behind the runbook's thresholds are all relative to the limits. Changing limits re-defines the faults.

**Instead, the load was lowered: 50 → 40 users.** This led to the redefinition of `U_base` (human-approved, `PLAN.md` M3 step 3):

> `U_base` is the Locust user count at which **frontend runs at ≈ 50–60% CPU relative to its 400m limit**, in steady state with no fault.

The original definition, ≈ 50% of A's host CPU, would be about 8 of 16 cores and would crush the 200–500m service limits.

| Load | frontend CPU / limit | frontend throttle | productcatalog throttle |
|---|---|---|---|
| 50 users | ~70% | 0.27 | 0.25 |
| **40 users** | **60% median (49–64%)** | **0.11** | **0.12** |

40 users sits at the **upper edge** of the target band. `scripts/calibrate.py` (M3, not yet written) will fix `U_base` formally.

---

## 5. Metrics & Baseline Definition

### 5.1 p99 latency — why tails, not averages

For each 20 s tick, Locust reports the 99th-percentile response time of all requests completed in the window $(T_k, T_{k+1}]$, about 900–1100 requests at 40–50 users. Averages hide the experience of the slowest users. One slow dependency on a page render shows up in p99 long before it moves the mean: our median p50 was 32–57 ms while p99 swung between about 100 ms and about 1.2 s. The SLA, the reward, and the health predicate are all defined on p99:

$$H_t \;=\; \big[\,P99_t \le L_{SLA}\,\big] \;\wedge\; \big[\,F_t \le e_{sla}\,\big], \qquad e_{sla} = 0.01$$

$L_{SLA}$ is calibrated as $\lceil 1.5 \cdot q_{0.95}(\text{steady tick-}P99)\rceil_{10\,ms}$.

### 5.2 Coefficient of variation and Gate G6

$$CV \;=\; \frac{\sigma(P99_{tick})}{\mu(P99_{tick})}$$

computed over steady-state ticks with no fault. **G6 requires $CV < 0.25$** (plus < 2% SLA breaches on NULL ticks). The reason: $L_{SLA}$ is set from the steady p99 distribution. If that distribution is wide, either $L_{SLA}$ becomes so loose that real faults hide under it, or so tight that healthy ticks look like faults. Both corrupt the reward and the recovery predicate the agent is trained on.

### 5.3 Stale-tick rate and Gate G2

A tick is **stale** if any telemetry source failed, or any value had to be carried forward or zero-filled (`CLAUDE.md` §5.5). Stale transitions never enter the replay buffer, and 3 consecutive stale ticks truncate the episode. **G2 requires stale ticks on < 1% of ticks.**

| Recording (30 ticks each) | Stale | Note |
|---|---|---|
| 2026-10-02, 50 users | 0 / 30 | collection p95 0.69 s (deadline 3.0 s) |
| re-record, 50 users | 0 / 30 | |
| re-record, 50 users | 0 / 30 | |
| re-record, 40 users | **1 / 30** | Prometheus 1.0 s connect timeout over the hotspot — handled as a typed failure |

Statistical honesty: 0/30 gives a 95% upper bound of about 9.5%. Showing < 1% needs about 300 clean ticks, so G2 is formally measured over the M3 runbook runs.

### 5.4 Container throttling (CFS quotas)

$$\text{throttle}_d \;=\; \frac{\Delta\,\text{throttled\_periods}_d}{\Delta\,\text{periods}_d}\quad\text{over a 30 s rate window}$$

This is the share of 100 ms scheduler periods in which a container hit its CPU quota and was paused. It measures real CPU starvation better than utilization, because a service can be heavily throttled at an *average* CPU below its limit when demand is bursty. It is feature `throttle[d]` in the observation, and the runbook's θ = 0.40 is the F2 detection threshold.

### 5.5 Evaluation metrics (M5)

| Metric | Definition |
|---|---|
| **MTTR** (headline) | time from fault injection to the first of 3 consecutive healthy ticks with the fault's cure condition met. Median/IQR, censored at 360 s, plus Kaplan–Meier curves |
| Recovery rate | fraction of fault episodes recovered within 18 ticks |
| Cumulative SLA penalty | $\sum_t v_t$ over an episode (§6.4): how bad the SLA breach was, and for how long |
| FRR (false remediation rate) | actions taken in NULL (no-fault) episodes; must be ≈ 0 |
| Actions / episode, wasted-action rate | resource and operational overhead of the policy |
| Replica surplus | $\rho_t$, extra replicas above baseline (§6.4) |

Statistics: Mann–Whitney U per fault type, bootstrap 95% CIs.

### 5.6 $U_{base}$ and the steady baseline

$U_{base}$ anchors every steady-state number: $L_{SLA}$, $RPS_{base}$, the F4 surge ($2.5$–$3\times U_{base}$), and reset. The baseline must be **healthy and stable** before chaos is introduced. This is a **target, not yet achieved**: G6 is still failing (§7, §9).

---

## 6. The RL Agent — Masked Discrete Soft Actor-Critic with PER

### 6.1 Intuition

Soft Actor-Critic learns a policy that maximizes reward **plus the policy's own entropy**. It prefers acting as *randomly as it can afford to* while still collecting reward. In practice:
- it keeps exploring alternatives instead of collapsing early onto one remedy;
- it is robust to noisy rewards, which matters with a jittery p99;
- it is sample-efficient off-policy, so every real-cluster transition can be replayed many times.

Real-cluster time is our scarcest resource (about 6 days per 3×300-episode configuration).

### 6.2 Objective and updates

**Maximum-entropy objective**

$$J(\pi) \;=\; \sum_{t} \mathbb{E}_{(s_t,a_t)\sim\rho_\pi}\Big[\, r(s_t,a_t) \;+\; \alpha\,\mathcal{H}\big(\pi(\cdot\vert s_t)\big) \Big]$$

**Discrete, masked form** (actions are a finite catalog, so expectations over actions are exact sums over *valid* actions; invalid logits are set to $-10^9$ and their $\pi\log\pi$ terms are zeroed to avoid $0\cdot(-\infty)$):

$$V(s) \;=\; \sum_{a\,\in\,\mathcal{A}_{valid}(s)} \pi(a\vert s)\,\Big[\min_{j=1,2}\bar Q_j(s,a) \;-\; \alpha\log\pi(a\vert s)\Big]$$

**Critic target** ($n$-step, bootstrapping only on truncation, never on termination):

$$y \;=\; \sum_{i=0}^{n-1}\gamma^i r_{t+i} \;+\; \gamma^n (1-d_{term})\, V(s_{t+n}), \qquad L_Q = \mathbb{E}\big[\,w_i\,(Q_j(s,a)-y)^2\big]$$

**Actor loss**

$$L_\pi \;=\; \mathbb{E}_s\Big[\sum_{a\,\in\,\mathcal{A}_{valid}(s)}\pi(a\vert s)\big(\alpha\log\pi(a\vert s) - \min_j Q_j(s,a)\big)\Big]$$

**Temperature, with a per-state target entropy** (states with a single valid action are excluded):

$$\bar{\mathcal H}(s) = 0.4\cdot\log\lvert\mathcal{A}_{valid}(s)\rvert,\qquad L_\alpha = \mathbb{E}_{s:\,\lvert\mathcal A_{valid}\rvert\ge2}\Big[\alpha\,\big(\mathcal H(\pi(\cdot\vert s)) - \bar{\mathcal H}(s)\big)\Big]$$

**Prioritized Experience Replay**

$$p_i = \min\!\Big(\tfrac{\lvert\delta_{1,i}\rvert + \lvert\delta_{2,i}\rvert}{2},\,1\Big) + \epsilon,\qquad P(i) = \frac{p_i^{\,0.5}}{\sum_k p_k^{\,0.5}},\qquad w_i = \frac{(N\,P(i))^{-\beta}}{\max_k w_k}$$

$\beta$ is annealed linearly from $0.4$ to $1.0$ over $15{,}000$ gradient steps. A **uniform-replay ablation** uses identical code with prioritization off.

Locked hyper-parameters (`contract.yaml`): $\gamma=0.93$, $n=3$, $\tau=0.005$, MLP $[256,256]$, learning rates $3\times10^{-4}$, batch 128, buffer 50k, 4 updates per tick (≤ 1.0 s), $\alpha_0=0.1$.

### 6.3 Observation space — 36 features in $[-1,1]$ (`env/telemetry.py`, built and tested)

| Index | Features |
|---|---|
| 0–4 | normalized p99 ($\log_2(1+P99/L_{SLA})/\log_2 21$), failure ratio, RPS / $3RPS_{base}$, and the tick-to-tick deltas of p99 and failure ratio |
| 5–32 | 7 features × 4 managed services: CPU / limit, **throttle ratio**, memory / limit, ready / base, replicas / max, restart delta, rollout recency $e^{-k/5}$ |
| 33–35 | action lock in flight, ticks since last action, **telemetry stale** flag |

**Ground truth never enters the observation**: fault identity, target, severity and injection time go only to `info` and logs.

### 6.4 Action space — 12 discrete, safety-masked actions

| ID | Action |
|---|---|
| 0 | NOOP |
| 1–4 | RESTART frontend / cartservice / currencyservice / productcatalogservice (zero-downtime rollout restart) |
| 5–6 | SCALE_UP / SCALE_DOWN frontend |
| 7–8 | SCALE_UP cartservice / currencyservice |
| 9–11 | RESTORE productcatalog / cartservice / currencyservice to the golden template |

> **The agent never changes CPU limits or requests.** Code that does so is forbidden (`CLAUDE.md` §4.2). Replica bounds (frontend ≤ 3, cart ≤ 2, currency ≤ 2, productcatalog = 1) are enforced twice, by the action mask and by the executor. A global action lock masks everything except NOOP while a rollout is in progress.

### 6.5 Reward

$$\ell_t = \operatorname{clip}\!\Big(\tfrac{\log_2(P99_t/L_{SLA})}{3},0,1\Big),\quad e_t = \operatorname{clip}\!\Big(\tfrac{F_t-0.01}{0.20-0.01},0,1\Big),\quad v_t=\operatorname{clip}(\ell_t+e_t,0,1)$$

$$\rho_t = \frac{\sum_d \max(0,\ spec_d - base_d)}{4},\qquad r_k = -\Big(v_{k+1} \;+\; c\big(\text{kind}(a_k^{exec})\big) \;+\; 0.05\,\rho_{k+1}\Big)\ \in [-1.09,\,0]$$

Action costs $c$: NOOP 0, SCALE 0.02, RESTART/RESTORE 0.04. The three terms trade off **SLA preservation** ($v$), **operational churn** ($c$), and **resource overhead** ($\rho$). There is deliberately no MTTR-shaping term.

### 6.6 Control loop

A fixed **20 s wall-clock tick** on B's monotonic clock:
- telemetry is collected concurrently under a **3.0 s hard deadline**;
- actions are dispatched asynchronously;
- the learner gets ≤ 1.0 s per tick;
- decision latency is logged with a target of p95 ≤ 4 s.

Before training, the agent is warm-started offline from **120 runbook episodes with ε = 0.25** exploration.

---

## 7. The M3 Debugging Investigation (Gate G6)

### 7.1 The problem

The first 30 real ticks (50 users) showed tick-p99 **CV = 0.52** (median 813 ms, range 170–1231 ms) against the G6 target of < 0.25. Calibrating on that baseline would have produced a meaningless SLA.

### 7.2 Method

One variable at a time, with a decision rule fixed *before* each run, read-only measurement wherever possible, and every tool committed for reproducibility:

| Tool | What it measures |
|---|---|
| `scripts/latency_diag.py` (tick mode) | per 20 s tick: client p99 (Locust) vs **server p99** (frontend `took_ms` logs), ping RTT, Locust CPU, A's non-Kubernetes CPU and node CPU pressure (PSI) from the kubelet, throttle of all 11 services |
| `scripts/latency_diag.py --stalls` | sub-second: groups slow requests into **stall events** and checks each against ping RTT (a host freeze delays pings) and per-service CFS counters |
| `scripts/grpc_probe.py` | direct gRPC/RESP probes every 50 ms via port-forward, with a **control** service, aligned to stall events; cAdvisor thread counts |

### 7.3 Chronology

| # | Hypothesis | Test | Result | Verdict |
|---|---|---|---|---|
| H1 | Network (WSL NAT + hotspot) | client vs server p99, ping RTT per tick | client↔server r = **+0.98 / +0.99**; client↔RTT r = +0.14 / +0.19 | ❌ ruled out |
| H2 | B-side CPU (Locust event loop) | Locust process CPU | ~5% of one core; r ≈ 0 | ❌ ruled out |
| H3 | Machine A host interference | non-Kubernetes CPU (root − kubepods cgroup), node PSI; ping during stalls | r = +0.05; PSI flat 0.06–0.10; **0/47 stalls** with a ping > 150 ms | ❌ ruled out |
| — | CPU-starved recommendationservice | raise 200m → 500m (A/B) | its throttle 0.57 → 0.004, median p99 −30–40%, **but CV 0.60** (fast ticks faster, slow ticks unchanged) | partial: real improvement, not the cause of the variance |
| — | Load too high | 50 → 40 users | throttle halved; **slow-request share unchanged (~1.9%)**; CV 0.62 | load-independent tail |
| — | *Key reframe* | `--stalls` | slow requests arrive as **stall events**: 2.7–7/min, ~0.8 s each, every request started in a ~0.1–0.5 s window affected, uniform across product IDs | a brief **pause**, not slow requests |
| — | CPU-quota stalls | CFS counters bracketing stalls | throttle during stalls = run average (productcatalog 0.120 vs 0.110) | ❌ not CPU quota |
| H4 | Go GC / GOMAXPROCS > quota | Go buildinfo parsed from the public image layers | **go1.27.0**, container-aware GOMAXPROCS active | ❌ ruled out (no change made) |
| H5 | Node.js `currencyservice` event loop | request-type mix (renders stall, `POST /setCurrency` never); **direct gRPC probe + control** | currency probe spiked in **0/14 and 0/21** stalls | ❌ ruled out |
| — | cartservice `GetCart` | direct gRPC probe | spiked in **20/21** and **19/19** stalls (max ~1.0–1.1 s) | ✅ stall source |
| H6 | redis pauses (persistence/fork) | `INFO`: 38 forks in 19.7 h, 0.3 ms each; **RESP PING probe** | PING spiked in **1/19** stalls (shared background blip) | ❌ ruled out |
| — | .NET thread-pool starvation (step-up) | cAdvisor `container_threads` vs stalls | flat **24–26**; enrichment **1.03** | ❌ not confirmed (no change made) |
| — | Whole process vs redis client path | cartservice **gRPC health check** (no redis) | spiked in **14/14** stalls, identical to GetCart | ✅ **whole cartservice process pauses** |

### 7.4 Diagnosis (as established)

> The steady-state tail that breaks G6 is caused by **aperiodic ~0.2–1.1 s pauses of the entire `cartservice` (.NET 10) process**: inter-pause gaps median ~9–10 s, CV ≈ 1. Every page render calls `GetCart` for the cart badge, so each pause stalls roughly 1–3% of requests and makes tick-p99 bimodal (~100–300 ms vs ~0.8–1.2 s).

**Not yet established:** the *mechanism inside the runtime*. The two remaining candidates both block every request, health checks included:
- **thread-pool starvation**, which is consistent with near-zero CPU throttling during pauses;
- a **stop-the-world GC**.

The image sets `DOTNET_EnableDiagnostics=0` and ships as a single file, and sidecars are forbidden, so runtime tracing is unavailable. The mechanism will be identified by **treatment**:
- **Treatment A:** `DOTNET_ThreadPool_ForceMinWorkerThreads` on cartservice.
- **Treatment B (only if A fails):** a GC setting.

### 7.5 Engineering by-products

- **Reproducibility:** `redis:alpine` (floating) pinned to `redis:8.10.2-alpine@sha256:3811…e5a0`, the digest already running. `golden_overrides` gained a digest-required `image` key.
- **Safety lesson:** running `/src/server -version` via `kubectl exec` started a duplicate server process. It exited on the port conflict, and cAdvisor confirmed one process per container. The procedure now is to read versions from image layers, never by executing binaries.
- **Data integrity:** a `NaN → 0` masking bug (Python `max(0.0, nan) == 0.0`) was caught by tests before any training, and the observation builder now rejects non-finite inputs.

---

## 8. Stage-by-Stage Execution Plan

| Milestone | Objective | Status |
|---|---|---|
| **M1 — Cluster baseline** | Two-machine topology, least-privilege RBAC, deterministic golden state, clock sync | ✅ `preflight m1` 18/18 |
| **M2 — Telemetry & adversary** | Minimal Prometheus, Locust `/tick`, Chaos Mesh; freshness fix (5 s housekeeping); limit tuning | ✅ `preflight m2` 15/15; smoke throttle 0.035 → 0.52 within 15 s |
| **M3 — Runbook & Gym bridge** | Real-cluster Gymnasium env (tick clock, telemetry, imputation, mask, lock, injector F1–F4, active-restore `reset()`); **baseline stabilization (G6)**; calibration of $L_{SLA}$, $RPS_{base}$, $U_{base}$; fault smoke tests; scripted runbook; gates G1–G7; 120 warm-start episodes | 🔄 telemetry layer done (66 tests); **G6 diagnosis in progress (Treatment A next)**; env core, calibration, runbook pending |
| **M4 — PER-iSAC integration** | Masked discrete SAC (Tianshou), offline warm-start from the runbook buffer, online training on the real cluster: **uniform vs PER × 3 seeds × 300 episodes**, checkpoints, greedy probes. Faults are injected by `env/injector.py` via Chaos Mesh StressChaos and the Kubernetes API | ⏳ planned |
| **M5 — Evaluation & defense** | One fixed, seeded schedule (6× each of F1–F4 + 6 NULL = 30 episodes) for 9 policies: default K8s, K8s + HPA, runbook, SAC-uniform ×3, SAC-PER ×3. MTTR, recovery rate, Σv, FRR, wasted actions; Mann–Whitney U, bootstrap CIs, Kaplan–Meier | ⏳ planned |

---

## 9. Current Status, Open Items & Threats to Validity

**Open items before calibration:**
1. **G6:** apply Treatment A (cartservice thread-pool minimum, CONTRACT-CHANGE) and re-measure with `grpc_probe.py` and a 30-tick diagnostic at 40 users. If the pauses persist, try Treatment B (GC).
2. Write `scripts/calibrate.py` and lock the `U_base` band (50–60% of frontend's limit) in `contract.yaml`.
3. G2 must be shown over at least ~300 ticks. One hotspot-induced stale tick has already been seen.

**Threats to validity (to state in the defense):**
- **Shared host and wireless link:** absolute latencies and recovery times are specific to this environment, and comparisons between policies run on the same machines and schedule are the valid unit of evidence.
- **Single node:** no cross-node failure modes, and SCALE actions place replicas on the same host.
- **Baseline tuning:** limits were raised for frontend, currencyservice and recommendationservice, and Online Boutique is not exactly upstream. All changes are human-approved, versioned (`CONTRACT-CHANGE`) and documented in `CLAUDE.md` §2.
- **Cluster physics is not seedable:** seeds control the agent and the fault plan, not the cluster, so repeated seeds and non-parametric statistics are used.
