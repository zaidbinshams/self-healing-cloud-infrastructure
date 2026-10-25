# Self-Healing Cloud Infrastructure — Project Explainer

A single document for explaining the whole project: what we built, why, how it works, what we found, what is new, the decisions and trade-offs behind it, how to read the repository, and how to present it.

Formulas are written in LaTeX math. GitHub renders `$…$` and `$$…$$` blocks directly. If your viewer does not, copy the LaTeX into Overleaf or any LaTeX editor; it is valid as written.

**Sources of truth:**
- numbers: `paper_a/05_numbers.md`;
- design rules: `CLAUDE.md`;
- locked parameters: `config/contract.yaml`;
- experiment history and decisions: `docs/PREREGISTRATION.md`.

---

## Contents

1. The project in one paragraph
2. The problem
3. What we built: the testbed
4. The control loop
5. The decision problem: observations, actions, rewards
6. The agent: masked discrete SAC with PER and a runbook warm start
7. The baselines
8. Measuring the right signal (Paper B in brief)
9. How the experiment was run: pre-registration and protocol
10. Results
11. What is new, and what we do not claim
12. Decisions and trade-offs
13. Limitations
14. Reliability engineering (how a live experiment survived a week)
15. How to read the repository
16. How to present the project
17. Glossary

---

## 1. The project in one paragraph

We trained a reinforcement-learning agent to repair faults in a running microservice application. The application is Google's Online Boutique, on a single-node Kubernetes (K3s) cluster.
- **Real cluster, no simulator.** The agent learns and is tested on the live system.
- **Every 20 seconds** it reads the system's health and chooses one of 12 actions: do nothing, restart a service, scale it up or down, or restore it to a known-good configuration.
- **Safety guards:** hard action masks, one action at a time, and least-privilege credentials.
- **Edge setting:** the controller runs on a second machine connected over Wi-Fi.
- **Comparison:** default Kubernetes, Kubernetes with an autoscaler (HPA), and a hand-written SRE runbook, under a protocol written down before any training.
- **Results:**
  - the agent recovered 35 of 40 injected faults, against 0 for Kubernetes and 10 for HPA;
  - it matched the runbook's recovery time within the pre-set 20-second margin;
  - it never acted when nothing was wrong.
- **Warm start is essential:** without starting from runbook demonstrations, the same agent failed (15/40 recovered, actions in every fault-free episode).
- **Replication:** a second training seed gave the same result.

---

## 2. The problem

- **Faults happen.** Microservice applications fail in many ways: bad deployments, overloaded pods, lost replicas, traffic surges. What operators measure is **MTTR**, the mean time to recovery.
- **Repair today is manual or scripted.** On-call engineers follow **runbooks**: written if-this-then-that procedures, one per known fault class. Runbooks work, but they must be written and maintained by hand for each fault and system.
- **Kubernetes heals only some things.** It restarts crashed containers and, with HPA, adds pods when CPU is high. It does not roll back a bad configuration, notice that a service was scaled to zero, or tell a busy pod from a broken one.
- **RL could learn the repair policy from experience.** But most RL work for cloud systems:
  - trains in a simulator or from logged data, not on the live system;
  - controls continuous resource knobs (CPU shares, replica counts), not discrete repair actions;
  - is not compared against what operators actually use.
- **Our question:** can an RL agent learn discrete remediation **online on a live cluster**, safely, under real-time and edge-network constraints, and how does it compare with Kubernetes, HPA and a runbook?

---

## 3. What we built: the testbed

```
            Wi-Fi link (lossy; controller and cluster on different machines)
┌──────────────────────────────┐          ┌──────────────────────────────────────┐
│ Machine A — the cluster      │          │ Machine B — the controller           │
│ K3s (single node)            │◄─ API ───┤ executor (agent credential)          │
│  ├ Online Boutique           │◄─ API ───┤ fault injector + reset (controller   │
│  │  11 deployments, 4 managed│          │   credential)                         │
│  ├ Chaos Mesh (CPU stress)   │◄─ HTTP ──┤ Locust load generator (30 users)      │
│  ├ Prometheus (cAdvisor, 5 s)│── PromQL►│ telemetry + environment (Gymnasium)   │
│  └ metrics-server (for HPA)  │── logs ─►│ agent / runbook, evaluation           │
└──────────────────────────────┘          │ clock authority (A's NTP → B)         │
                                          └──────────────────────────────────────┘
```

- **Application.** Online Boutique: 11 deployments (10 services plus Redis). The agent observes and acts on four: `frontend`, `cartservice`, `currencyservice`, `productcatalogservice`. The others run but are outside its scope.
- **Load.** Locust simulates 30 users (`U_base`), chosen so the frontend runs at 50–60 % of its CPU limit in steady state. Each request opens its own TCP connection, so new pods actually receive traffic (with keep-alive, users stayed pinned to old pods and scaling did nothing).
- **Telemetry, collected concurrently every tick:**
  - server-side latency from the frontend's request logs (p95 per tick);
  - failure ratio and request rate from Locust;
  - CPU usage, CPU throttling and memory per service from Prometheus;
  - replica counts, readiness, restarts and rollout status from the Kubernetes API.
- **Faults (one per episode, or none):**

| Fault | What we inject | Cured when | Curing action |
|---|---|---|---|
| F1 bad deploy | env var `EXTRA_LATENCY` (300 ms / 600 ms / 1 s) on productcatalogservice | template back to golden and rolled out | RESTORE productcatalogservice |
| F2 hot pod | CPU stress (1 or 2 workers) on one cart or currency pod | the stressed pod is gone | RESTART that service |
| F3 lost replicas | cart or currency scaled to 0 | ≥ 1 replica available | RESTORE (or SCALE_UP) that service |
| F4 traffic surge | load × 2.5 or × 3.0 | SLA alone | SCALE_UP frontend |
| NULL | nothing | — | do nothing |

- **Safety.**
  - The agent's credential can only patch or scale the four managed deployments in one namespace. It cannot delete pods or touch anything else.
  - Replica bounds (frontend 3, cart 2, currency 2, productcatalog 1) are enforced twice, by the action mask and by the executor.
  - Only one action can be in flight.
- **Two machines and a wireless link** is a constraint we kept, not one we hid. It makes this an edge-like setting: the controller sees the cluster through a lossy link, and some decisions arrive late.

---

## 4. The control loop

One **tick** every 20 s on a fixed monotonic schedule $T_k = T_0 + 20k$:

1. Choose an action from the current observation and mask. An invalid choice becomes NOOP.
2. If the action is not NOOP, take the global lock and dispatch the action asynchronously (it never blocks the loop).
3. Sleep until the next boundary.
4. Collect all telemetry concurrently, with a 3-second hard deadline.
5. Release the lock if the target finished rolling out, if 100 s passed, or if the dispatch failed.
6. Build the next observation (with imputation rules for missing data), the reward and the next mask.
7. Store the transition, unless the new state is "stale" (missing data).
8. The learner takes 4 gradient steps (budget 1 s).

- **Decision latency** (window close → action dispatched): median 0.33 s, p95 0.89 s. Under the degraded network: median 1.44 s.
- **Missing data** (late metrics, a pod that just started, a network drop) follows fixed rules:
  - carry the last value forward for up to 2 ticks, then 0;
  - mark the tick stale;
  - never insert a transition whose next state is stale into replay;
  - three stale ticks in a row invalidate the episode, which is then re-run.

---

## 5. The decision problem: observations, actions, rewards

### Observation (36 numbers, all scaled to [0, 1] or [−1, 1])

| Group | Features |
|---|---|
| Global (5) | latency, failure ratio, request rate, change in latency, change in failure ratio |
| Per managed service, ×4 (28) | CPU utilisation vs limit, CPU throttling ratio, memory utilisation, readiness, replicas / max, recent restarts, rollout recency |
| Controller (3) | action in flight, ticks since last action, telemetry stale |

Latency feature, with $L_t$ the server-side p95 latency and $L_{\mathrm{SLA}} = 70$ ms:

$$
o_t^{\mathrm{lat}} = \mathrm{clip}\!\left(\frac{\log_2\!\left(1 + L_t / L_{\mathrm{SLA}}\right)}{\log_2 21},\ 0,\ 1\right)
$$

The agent **never sees the fault**: its identity, target, severity and injection time are logged for analysis only.

### Actions (12)

`NOOP` · `RESTART` × 4 (one per managed service) · `SCALE_UP` frontend / cart / currency · `SCALE_DOWN` frontend · `RESTORE` productcatalog / cart / currency.

- **RESTART** is a rolling restart (no downtime).
- **SCALE** changes the replica count within bounds.
- **RESTORE** replaces the deployment's template and replica count with the snapshot taken after a verified clean install ("golden").

### Action mask

$$
m_t(a) =
\begin{cases}
1 & a = \textsc{noop} \\
0 & \text{an action is in flight (global lock)} \\
\mathbf{1}\left[\text{replica bounds and target state allow } a\right] & \text{otherwise}
\end{cases}
$$

RESTORE is **never** masked based on drift from golden. Masking it would tell the agent which service was tampered with, which leaks the fault.

### Reward

With failure ratio $F_t$, $e_{\mathrm{sla}} = 0.01$, $e_{\max} = 0.20$:

$$
\ell_t = \mathrm{clip}\!\left(\frac{\log_2(L_t / L_{\mathrm{SLA}})}{3},\ 0,\ 1\right),\qquad
e_t = \mathrm{clip}\!\left(\frac{F_t - e_{\mathrm{sla}}}{e_{\max} - e_{\mathrm{sla}}},\ 0,\ 1\right),\qquad
v_t = \mathrm{clip}(\ell_t + e_t,\ 0,\ 1)
$$

$$
\rho_t = \frac{1}{4}\sum_{d} \max\left(0,\ \mathit{spec}_d - \mathit{base}_d\right),\qquad
r_k = -\Big(v_{k+1} + c(a_k) + 0.05\,\rho_{k+1}\Big),\qquad r_k \in [-1.09,\ 0]
$$

Action costs: $c(\textsc{noop}) = 0$, $c(\textsc{scale}) = 0.02$, $c(\textsc{restart}) = c(\textsc{restore}) = 0.04$.

In words: penalise SLA violation (latency above the threshold, on a log scale, plus errors above 1 %), a small cost per action, and a small cost for extra replicas. There is deliberately **no** recovery-time bonus; the agent learns to be fast because violation hurts every tick.

### Health, recovery, MTTR

A tick is healthy when

$$
H_t \;\equiv\; L_t \le L_{\mathrm{SLA}} \;\wedge\; F_t \le e_{\mathrm{sla}}
$$

An episode recovers at the first tick $t$ after injection with $H_t \wedge H_{t+1} \wedge H_{t+2}$ **and** the fault's cure condition (table in §3). Then

$$
\mathrm{MTTR} = \mathrm{wall}(T_t) - t_{\mathrm{inject}},\qquad \text{censored at } 360\ \mathrm{s}\ (18\ \text{ticks}).
$$

The cure condition stops "lucky" recoveries from counting. For example, if latency happens to dip but the stressed pod is still there, the episode is not recovered.

### Calibration of the SLA threshold

$$
L_{\mathrm{SLA}} = \left\lceil 1.5 \cdot q_{95}\big(\text{steady-state tick latency}\big) \right\rceil_{10\,\mathrm{ms}} = 70\ \mathrm{ms}
$$

This was measured once at `U_base` = 30 users, then frozen (`config/calibration.json`). Every session after that started with a steady-state check against the frozen value.

---

## 6. The agent: masked discrete SAC with PER and a runbook warm start

### Soft Actor-Critic for discrete actions, with masking

The actor outputs logits $z_\phi(s)$. Invalid actions get $-10^9$ before the softmax:

$$
\pi_\phi(a \mid s) = \mathrm{softmax}\Big(z_\phi(s) \odot m + (-10^9)(1 - m)\Big)_a
$$

Soft value of the next state (over valid actions only, using target critics $\bar Q_j$):

$$
V(s') = \sum_{a:\,m'(a)=1} \pi(a \mid s')\Big[\min_{j=1,2} \bar Q_j(s', a) - \alpha \log \pi(a \mid s')\Big]
$$

n-step target ($n = 3$, $\gamma = 0.93$; $d$ = terminated, so the critic still bootstraps when an episode is cut off by time):

$$
y_t = \sum_{i=0}^{n-1} \gamma^i r_{t+i} + \gamma^n (1 - d_{t+n})\, V(s_{t+n})
$$

Actor loss:

$$
\mathcal{L}_\pi = \mathbb{E}_s \sum_{a:\,m(a)=1} \pi(a \mid s)\Big[\alpha \log \pi(a \mid s) - \min_j Q_j(s, a)\Big]
$$

Implementation detail that matters: masked log-probabilities are set to zero before any $\pi \log \pi$ product. Otherwise $0 \cdot (-\infty)$ gives NaN and training silently diverges.

### Conservative critic (CQL) and prioritized replay (PER)

Critic loss: an importance-weighted TD error plus the CQL penalty, which pushes down Q-values of actions the data does not support:

$$
\mathcal{L}_Q = \mathbb{E}_i\Big[w_i\,\big(Q_j(s_i, a_i) - y_i\big)^2\Big] + \lambda\, \mathbb{E}_s\Big[\log \sum_{a:\,m(a)=1} e^{Q_j(s, a)} - Q_j(s, a_{\mathcal{D}})\Big],\qquad \lambda = 1
$$

Replay sampling and importance weights. The PER exponent is written $\beta_p$ here to avoid a clash with SAC's $\alpha$; the contract calls it `per.alpha`.

$$
P(i) = \frac{p_i^{\beta_p}}{\sum_k p_k^{\beta_p}},\qquad
p_i = \min\!\Big(\tfrac{|\delta_{1,i}| + |\delta_{2,i}|}{2},\ 1\Big) + \epsilon,\qquad
w_i = \frac{\big(N\, P(i)\big)^{-\beta}}{\max_k w_k}
$$

$\beta_p = 0.5$; $\beta$ grows from 0.4 to 1.0 over 15 000 gradient steps; $\epsilon = 10^{-3}$.

### Warm start from the runbook

1. Run the scripted runbook for 120 episodes. With probability 0.25 it takes a random valid action instead, so the data also shows what happens after "wrong" actions (1268 usable transitions).
2. Offline phase, 2000 gradient steps (about 9 seconds):
   - the **actor** is trained by behaviour cloning (copy the data's actions);
   - the **critic** by the TD loss plus CQL.
3. Online phase, 200 episodes on the live cluster: the update above with **α fixed at 0.1** and CQL kept on.

Why this design and not plain SAC: see §12, "Conservative update".

### Hyper-parameters (all locked in `config/contract.yaml`)

| | |
|---|---|
| Networks | actor and twin critics: MLP 36 → 256 → 256 → 12, ReLU |
| γ, n-step, τ | 0.93, 3, 0.005 |
| Learning rate | 3 × 10⁻⁴ |
| Batch, buffer | 128, 50 000 |
| Updates per tick | 4 (budget 1 s) |
| α | fixed 0.1 online |
| CQL weight λ | 1.0 |
| Training | 200 episodes per seed (seeds 0 and 1); cold start 100 episodes |

The agent is written in plain PyTorch and NumPy (no RL library), and every rule above has a unit test. We tried Tianshou first: one version required downgrading pinned packages, and the other's data collector did not fit a 20-second real-time loop.

---

## 7. The baselines

- **Kubernetes (default):** no remediation actions. Only Kubernetes' own controllers act (e.g. restarting crashed containers).
- **Kubernetes + HPA:** a CPU-based Horizontal Pod Autoscaler on frontend, cart and currency (target 60 % of each pod's CPU limit, min = base, max = the agent's bounds). It is present only during its own episodes.
- **Scripted runbook:** sees the same observation as the agent, never the fault. Its rules are evaluated in order every tick, and the first match fires:

| Rule | Condition | Action |
|---|---|---|
| R0 | an action is in flight | NOOP |
| R1 | a service has fewer replicas than its base | RESTORE it |
| R2 | unhealthy for 2 ticks and a service changed recently (not by the runbook) | RESTORE the most recently changed |
| R3 | unhealthy for 2 ticks, request rate ≥ 1.5 × base, frontend throttled ≥ 0.40 | SCALE_UP frontend |
| R4 | unhealthy for 2 ticks, normal request rate, a service throttled ≥ 0.40 for 2 ticks | RESTART the most throttled (with a cooldown) |
| R5 | healthy for 6 ticks and request rate back below 1.2 × base | SCALE_DOWN frontend, or RESTORE an over-scaled service |
| R6 | otherwise | NOOP |

The runbook is a strong baseline because we wrote it knowing the four fault types. An RL agent that matches it, without being told the fault types, is the interesting result.

---

## 8. Measuring the right signal (Paper B in brief)

Before any agent could work, we had to decide what latency it should watch. This became a second paper.

- **Client-side p99** (measured by Locust on B) tracked the Wi-Fi round-trip time (correlation 0.95). It moved with the network, not the application, so no remediation action could affect it.
- **Server-side** latency, from the frontend's own request logs, was stable once an in-cluster cause was fixed (cartservice's .NET thread pool). Its tick-to-tick variation (CV) was 0.14–0.24.
- **p99 vs p95.** With about 900 requests per tick, p99 is decided by roughly 9 requests and carries about twice the sampling noise of p95. We use **server-side p95**. Rule of thumb from the bootstrap analysis: sampling CV falls roughly as $k^{-0.4}$ in the expected number of tail samples $k = n(1 - q)$.
- **Decision-level effect.** With the same calibration rule, a client-side threshold would be 3–6× looser. It would detect none of the four fault smoke tests, where server-side detected all four.

The paper reports both signals: client-side p99 is logged and shown, but never drives the agent. Details: `paper_b/`.

---

## 9. How the experiment was run: pre-registration and protocol

- **Pre-registration.** `docs/PREREGISTRATION.md` was committed before any training. It fixes:
  - the hypotheses, metrics and statistical tests;
  - the evaluation schedule and exclusion rules;
  - a rule that nothing may change based on results.

  Every later change is a dated **amendment**, written before the data it affects:

| # | Date | Change | Why |
|---|---|---|---|
| 1 | 10 Oct | one connection per request; CPU limits 500m for productcatalog and currency | F4 was uncurable: new pods got no traffic, then other services saturated |
| 2 | 10 Oct | Prometheus keep-alive; re-calibration (L_SLA 70 ms) | new TCP connections per query lost packets on Wi-Fi |
| 3 | 13 Oct | steady check after Machine A restarted; HPA baseline specified; schedule generated | environment changed; baseline needed a precise definition |
| 4 | 19 Oct | first training attempt discarded; rule for reading latency after frontend pod changes; 2 settle ticks | every frontend restart made the next tick stale, so the agent could never learn its effect |
| 5 | 19 Oct | offline warm start = behaviour cloning + CQL | plain offline SAC did not start runbook-like (acted in healthy states) |
| 6 | 20 Oct | online: keep CQL, fix α = 0.1 | plain online SAC drifted to acting in healthy states |
| 7 | 20 Oct | cold start 100 episodes | wall-clock time |
| 8 | 24 Oct | seed-1 replication with a runbook anchor | time allowed one more seed |

- **Evaluation schedule.** `eval/schedules/eval_v1.json`, generated once (seed 2026): 10 episodes each of F1–F4 and 10 NULL = 50 entries, with targets and severities balanced.
- **Interleaving.** For each entry, every policy runs it, in a random order, before the next entry starts. Slow drift in the cluster (time of day, Wi-Fi quality) therefore affects all policies equally.
- **Three evaluation blocks:**
  1. **Main:** 5 policies × 50 entries.
  2. **Edge subset:** the first 4 entries per fault type (20), runbook and agent, with the control link degraded by `netem` (+150 ms delay, 1 % loss on Kubernetes API and Prometheus traffic only).
  3. **Seed-1 block:** the second seed's agent and the runbook, all 50 entries. The runbook is deterministic, so if its results match the main block, the cluster did not drift between days.
- **Statistics:**
  - **Mann–Whitney U** on MTTR, with **Holm** correction across the comparisons in a family;
  - **Cliff's δ** as effect size;
  - **bootstrap 95 % confidence intervals** (10 000 resamples) of median differences;
  - **Kaplan–Meier** recovery curves with a **log-rank** test.

  "Non-inferior to the runbook" means the upper end of the CI of $\mathrm{median}(\mathrm{MTTR}_{\mathrm{agent}}) - \mathrm{median}(\mathrm{MTTR}_{\mathrm{runbook}})$ is below 20 s, one tick.
- **Invalid episodes** (3 stale ticks in a row) were re-run on the same entry and counted: 2 in the main block (Wi-Fi outages) and 1 in the edge block (metrics missing after a scale-from-zero).

---

## 10. Results

### Main evaluation (10 episodes per fault and policy)

| | Kubernetes | K8s + HPA | Runbook | PER-SAC warm | PER-SAC cold |
|---|---|---|---|---|---|
| F1 recovered | 0 | 0 | 10 | 10 | 0 |
| F2 recovered | 0 | 0 | 8 | 5 | 0 |
| F3 recovered | 0 | 0 | 10 | 10 | 5 |
| F4 recovered | 0 | 10 | 10 | 10 | 10 |
| **Total** | **0/40** | **10/40** | **38/40** | **35/40** | **15/40** |
| Median MTTR (unrecovered = 360 s) | 360 | 360 | 80 | 79 | 360 |
| Median MTTR, recovered only | — | 59 | 80 | 60 | 60 |
| Fault-free episodes with an action | 0/10 | 0/10 | 0/10 | 0/10 | 10/10 |

### Hypotheses

| | Hypothesis | Outcome |
|---|---|---|
| H1 | lower MTTR than Kubernetes and HPA | yes on F1–F3 against both; **not on F4 against HPA** (HPA recovered 4/10 surges at 40 s; it scales on CPU and does not wait 2 unhealthy ticks) |
| H2 | as good as the runbook (20 s margin), lower SLA penalty | MTTR **non-inferior** (CI −20.0 to +0.9 s); SLA penalty **a tie** |
| H3 | warm start helps | **yes** (training return p = 6.5 × 10⁻⁹; evaluation MTTR p = 4.4 × 10⁻⁵) |
| H4 | safe | **yes**: 0/10 fault-free episodes with an action, 0/30 in training, 0 safety violations |
| H5 | holds under the degraded link | **not established**: medians nearly identical (78.2 vs 78.5 s), but with 16 episodes the CI upper end is 20.15 s, just over the 20 s margin |

### What the agent learned (actions it actually took)

| Fault | Agent (warm) | Runbook |
|---|---|---|
| F1 (bad deploy) | RESTORE productcatalog | RESTORE productcatalog |
| F2 on cart | RESTART cart | RESTART cart |
| F2 on currency | **nothing** (0/5) | RESTART currency (3/5) |
| F3 | RESTORE the scaled-down service | same |
| F4 | SCALE_UP frontend (once or twice) | SCALE_UP frontend twice |
| NULL | nothing | nothing |

The agent rediscovered the runbook's cure for each fault without being told which fault it was. It was one tick faster on F1 and F4. It never learned F2 on currencyservice, and seed 1 shows the same blind spot (explanation in §12).

### Other results
- **Cold start:** the agent locked into repeating "RESTORE cartservice" on almost every tick. This is safe but useless: it is a no-op when nothing changed, but it counts as an action, so it shows up in all 10 fault-free episodes.
- **Edge (netem):** the agent recovered 14/16 and the runbook 15/16, and median times barely moved. Decision latency rose from 0.33 s to 1.44 s.
- **Seed 1:** 35/40 recovered, non-inferior to the runbook within its block (CI −0.7 to +19.9 s), 0/10 fault-free actions. The runbook anchor matched the main block (37 vs 38/40; pooled medians 79.7 vs 79.6 s), so the cluster did not drift.

All numbers, with sources: `paper_a/05_numbers.md`. Figures: `figures/paperA/` and `paper_a/latex/figures/`.

---

## 11. What is new, and what we do not claim

**What we contribute:**
1. **Learning remediation online on a live cluster**, with no simulator or digital twin. The agent uses discrete repair actions (restart, scale, roll back, scale from zero), not resource knobs.
2. **A guarded action interface for live learning:**
   - hard masks;
   - one action in flight;
   - replica bounds enforced twice;
   - least-privilege credentials;
   - a mask designed not to leak the fault.

   The result was zero safety violations across the 390 evaluation episodes, with about 500 training episodes run on the live cluster under the same guards.
3. **An edge-constrained setting** (controller over Wi-Fi), tested with an emulated degraded link.
4. **Evidence about how the RL method behaves on a live system:**
   - plain SAC drifts into acting in healthy states;
   - a behaviour-cloning + CQL warm start with a fixed temperature fixes that;
   - without the warm start the agent fails;
   - the price of the fix is lost exploration (the F2-currency blind spot).
5. **A pre-registered, replicated evaluation** against the tools operators actually use, reporting negative findings (HPA on F4, H5) as clearly as positive ones.
6. **A measurement finding** (Paper B): where and how the SLA signal is measured decides which faults a controller can see.
7. **Open artefacts:** code, configuration, evaluation schedule, per-episode results, analysis scripts and figure scripts.

**What we do not claim:**
- the first RL on a real cluster (FIRM did resource management);
- the first RL for remediation (E2E-REME, Dai et al.);
- a new warm-start idea (AWARE bootstraps RL from autoscalers);
- a new guarded interface (ARBITER);
- that prioritized replay helped (the ablation was not run);
- that the agent beats the runbook (it matches it);
- that results hold beyond one application on one node.

---

## 12. Decisions and trade-offs

| Decision | Alternative | Why we chose it | What it costs |
|---|---|---|---|
| Train on the live cluster | simulator or digital twin | the result is about real system behaviour; no sim-to-real gap | slow (6–8 min per episode), so only 2 seeds |
| Discrete repair actions | continuous resource control | matches what operators do; interpretable | fixed catalogue; no new actions can be learned |
| Hard masks + lock | penalise invalid actions in the reward | safety is guaranteed, not learned; masking is a valid policy gradient | the agent cannot learn sequences while an action is in flight |
| RESTORE never masked by drift | mask it when nothing drifted | masking would reveal which service is broken | the agent can choose useless restores (the cold agent did) |
| Server-side p95 as the SLA signal | client-side p99 | client p99 tracked Wi-Fi, not the app; p95 has half the sampling noise | does not include network time; client p99 is reported separately |
| 20 s tick | faster ticks | cAdvisor refreshes every 5–10 s and the 30 s rate window needs data; rollouts take tens of seconds | MTTR resolution is 20 s |
| Warm start (BC + CQL) | plain offline SAC | plain SAC valued rare actions in healthy states (extrapolation error) | the agent starts close to the runbook, so it inherits its biases |
| **Conservative update** online: CQL + fixed α | plain SAC with learned α | plain SAC drifted to acting in healthy states (π(NOOP) fell from 0.75 to 0.02 in 49 episodes) | near-deterministic policy, little exploration: F2-currency, rarely acted on in the data, was never learned |
| One-action global lock | parallel actions | avoids interacting rollouts and unclear credit | a second fault cannot be handled during a rollout |
| Interleaved evaluation | one policy after another | cluster drift affects all policies equally | more resets |
| Pre-registration with dated amendments | decide analysis afterwards | protects against choosing results; makes changes visible | every fix needs a written justification before the data |
| In-house SAC (PyTorch) | Tianshou | Tianshou required downgrading pinned packages or did not fit the real-time loop | more code to test (every rule is unit-tested) |
| Wi-Fi link kept as is | wired LAN | it is the edge condition we study; documented overrides | noisier measurements and network outages (handled by the watchdog) |
| Two seeds | five seeds | wall-clock time | weaker evidence of seed robustness |

**The central trade-off**, worth explaining on its own: safety in healthy states versus exploration.
- The CQL penalty and the fixed temperature keep the agent from inventing actions the data does not support. That is what makes it safe: 0 false remediations.
- The same mechanism stops it from trying actions it rarely saw, so a fault the warm-start data under-represented (F2 on currencyservice) is never learned.
- A future version could allow controlled exploration (a learned α with a floor, or exploration only during detected incidents) while keeping the NULL-safety guarantee.

---

## 13. Limitations

- One single-node cluster, one application (Online Boutique), one wireless link.
- Two training seeds for the warm-started agent, both from the same warm-start data; one seed for the cold start, run for 100 episodes instead of 200.
- No uniform-replay ablation, so no claim about prioritized replay itself.
- MTTR is measured in 20 s steps.
- F2 with one stress worker is borderline: some episodes never breach the SLA, so no policy is triggered.
- After scaling a service from zero, its new pod's metrics can be missing for up to a minute. This invalidated a few episodes, all re-run in evaluation.
- The method changed twice before the reported training (Amendments 5 and 6). Both are documented with the evidence that led to them.
- Cluster behaviour is not deterministic: a rerun elsewhere gives results of the same shape, not identical numbers.

---

## 14. Reliability engineering (how a live experiment survived a week)

- **Watchdog** (`scripts/watchdog.sh`):
  - restarts a crashed run from its last checkpoint or episode;
  - waits out network outages;
  - runs the session checks before resuming;
  - stops after 3 consecutive failures.
- **Session start** (`scripts/session_start.sh`), at the start of every session:
  - cluster health;
  - leftover faults removed;
  - clock skew between machines (< 200 ms);
  - steady-state latency against the frozen calibration.
- **Clock sync.** Machine B is the timestamp authority but follows Machine A's NTP server (through Windows, because B is WSL2). Measured skew: about ±6 ms.
- **Every remote call** has a timeout and returns a typed failure, never a crash and never a NaN. Data is written every tick; checkpoints every 25 episodes and on shutdown.
- **Chains** (`scripts/chain_m4.sh`, `scripts/chain_seed1.sh`) run training and evaluation stages unattended and can be resumed.
- **Operational events** (Wi-Fi drops, power cuts, Machine A on battery) are logged and listed in the pre-registration's completion report (§9 of `docs/PREREGISTRATION.md`).

---

## 15. How to read the repository

Suggested order. About two hours for a full read; 30 minutes for the first four.

1. **`README.md`**: summary, results table, layout.
2. **This file.**
3. **`docs/PREREGISTRATION.md`**: hypotheses, then the amendments in order. This is the project's decision history. The completion report at the end lists what ran and what went wrong.
4. **`paper_a/README.md`, then `paper_a/05_numbers.md`**: every result with its source.
5. **`CLAUDE.md`**: the technical specification (tick protocol §5.7, observation §5.3, reward §5.6, mask §5.8, imputation §5.5, agent rules §5.12).
6. **`config/contract.yaml`**: every locked number in one place.
7. **The code, in this order** (`docs/CODE_EXPLAINER.md` explains the key lines of each file):
   - `env/boutique_env.py` (the loop: `reset()` and `step()`);
   - `env/telemetry.py` (what is measured);
   - `env/reward.py` and the mask in `env/`;
   - `env/k8s_actions.py` (how actions are executed safely);
   - `env/injector.py` (faults);
   - `agents/runbook.py` (the baseline rules);
   - `agents/masked_discrete_sac.py` and `agents/per_buffer.py` (the agent);
   - `agents/train.py` (warm start and online training).
8. **`eval/`:**
   - `run_eval.py` (interleaved evaluation);
   - `metrics.py` and `stats.py` (pre-registered tests);
   - `analyze.py` (H1–H5 and the replication);
   - `plots.py` and `papera_export.py`.
9. **`tests/`**: one test file per module; reading them is a quick way to see what each function must guarantee.
10. **`paper_b/`**: the measurement study.
11. **`scripts/`**: operations (preflight, calibration, fault smoke tests, session start, watchdog, chains).

Git history (`git log --oneline`) shows how the project developed; amendment commits start with `docs(prereg)`.

---

## 16. How to present the project

### A 15-minute talk (one slide per bullet)

1. **Title:** one sentence: online RL remediation on a live cluster.
2. **Problem:** faults, MTTR, runbooks are manual, Kubernetes heals little. Show the fault table.
3. **Question and setting:** live cluster, discrete actions, safety, Wi-Fi edge link. Show the architecture diagram (§3).
4. **The loop:** the 20 s tick, observe → act → measure; masks and the lock.
5. **The agent:** SAC with masks (one formula: the masked softmax), warm start from the runbook, conservative update. Say *why* in one sentence each.
6. **Measuring correctly:** client vs server latency (one figure from Paper B, or the CV numbers).
7. **Protocol:** pre-registered, interleaved, 5 policies × 50 episodes, edge and seed-1 blocks.
8. **Result 1:** the recovery table (35/40 vs 0/40, 10/40, 38/40) and the Kaplan–Meier figure.
9. **Result 2:** what the agent learned (the actions table). It rediscovered the cures without being told the fault.
10. **Result 3:** the warm start is essential (training-curve figure; cold agent 15/40 and 10/10 false actions).
11. **Honest findings:** HPA faster on surges; F2-currency never learned (the safety vs exploration trade-off); H5 not established.
12. **Contributions and limitations.**
13. **Future work:** controlled exploration, multi-node, more seeds and applications, combining with HPA.

### Live demo options

- Walk through the repo and one recorded episode's transitions (`paper_a/figure_data/episodes_main.csv` for the summary).
- If built: a replay dashboard of recorded episodes (latency over time, fault injection, the action taken, recovery).
- `pytest -q` running about 200 unit tests in a few seconds shows the engineering quality without needing the cluster.

### Questions to expect, and short answers

Short versions below. Detailed answers, with evidence and follow-ups, for every area an external examiner may cover: `docs/FINAL_REVIEW_QA.md`.

- **Why not a simulator?** The point is real behaviour (rollout times, metric delays, network loss). Simulators hide exactly the effects that made this hard.
- **Isn't the runbook better? It recovered 38 vs 35.** On MTTR the two are statistically equivalent within one tick. The runbook was written knowing the faults; the agent was not told them. The agent's 5 misses are one fault/target pair, explained by the exploration trade-off.
- **Why did HPA win on F4?** It reacts to CPU directly and does not wait for two unhealthy ticks, so it scales sooner. It cannot fix any other fault (0/30).
- **Why fix α?** With α learned, the agent's entropy target pushed α up and the policy drifted into acting in healthy states. We showed this on recorded data before changing it (Amendment 6).
- **Is two seeds enough?** It is weak evidence of robustness. We say so. The runbook anchor shows the second block ran on an unchanged cluster.
- **How do you know you didn't tune on the test data?**
  - The schedule, metrics and tests were fixed before training.
  - The final checkpoint is always used.
  - Every change is a dated amendment made before the data it affects.
- **Could the agent do something dangerous?** Not outside the mask. Its credential can only patch or scale four deployments, replica bounds are checked twice, and it cannot delete pods.
- **What would you do next?** Controlled exploration for rare faults, a multi-node cluster, more applications and seeds, and an agent that works alongside HPA.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| MTTR | time from fault injection to the first of 3 healthy ticks with the fault cured |
| SLA, L_SLA | service-level agreement; the latency threshold (70 ms server-side p95) |
| p95 / p99 | 95th / 99th percentile of request latency in a tick |
| tick | one 20 s control step |
| golden | the deployment templates captured right after a verified clean install |
| RESTORE | replace a deployment's template and replicas with golden |
| HPA | Kubernetes Horizontal Pod Autoscaler |
| SAC | Soft Actor-Critic, an RL algorithm that maximises reward plus policy entropy |
| α (SAC) | temperature: how much entropy (randomness) is rewarded |
| PER | prioritized experience replay: sample surprising transitions more often |
| CQL | conservative Q-learning: penalise Q-values of actions the data does not support |
| BC | behaviour cloning: train the policy to copy demonstrated actions |
| warm / cold start | with / without runbook demonstrations before online training |
| mask | which actions are allowed in the current state |
| stale tick | a tick with missing or imputed telemetry; its transition is not stored |
| NULL episode | an episode without a fault (tests false remediation) |
| FRR | false-remediation rate: share of NULL episodes with any action |
| netem | Linux network emulator, used to add delay and loss |
| pre-registration | writing down hypotheses and analysis before collecting data |
| non-inferiority | "not worse than by more than a margin" (here 20 s) |
| Cliff's δ | effect size: P(a < b) − P(a > b), from −1 to 1 |
| Kaplan–Meier | curve of the fraction not yet recovered over time, handling unrecovered episodes |
