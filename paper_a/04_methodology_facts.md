# 04 — Methodology Facts (facts and numbers, not prose)

> For the Testbed, Method and Experimental Design sections. **The authors write all paper text.** Locked values: `config/contract.yaml` and `config/calibration.json`; pre-registered design: `docs/PREREGISTRATION.md` (Amendments 1–8). Results: `05_numbers.md`. Updated 25 Oct 2026 for Amendments 4–8 (marked **[A4]**…**[A8]**).

## Claim and positioning (from PLAN_OF_ACTION §2)

- **Claim:** online, model-free deep RL that learns a **discrete remediation** policy (restart, scale, rollback-to-golden, scale-from-zero) **entirely on a live cluster**, with no simulator or digital twin.
- **Operating conditions:** hard safety masks plus a global in-flight lock, a 20 s real-time loop, and edge-network constraints.
- **Evaluation:** MTTR, false-remediation rate and SLA penalty against default Kubernetes, Kubernetes + HPA and a scripted SRE runbook; runbook bootstrapping measured by a cold-start ablation; robustness under a degraded control-plane network.
- **Do not claim:** first RL on a real cluster (FIRM), first RL remediation (E2E-REME, risk-constrained remediation), a novel warm-start (AWARE), or a novel guarded action interface (ARBITER).

## Testbed

| Item | Value |
|---|---|
| Application | Google Online Boutique v0.10.7, 11 deployments (loadgenerator removed); 4 managed by the agent: frontend, cartservice, currencyservice, productcatalogservice |
| Cluster | single-node K3s v1.36.4 on Machine A (16 CPU, ~15 GB, Ubuntu 26.04 laptop, swap off) |
| Controller host | Machine B (WSL2): env, agent, Locust load generator, timestamp authority |
| Link | Windows mobile hotspot via a third machine; B→A RTT p99 well above the 2 ms design target (overrides documented); clock skew A–B ±6–16 ms (gate 200 ms) |
| Fault tooling | Chaos Mesh StressChaos (CPU), K8s API patches (bad config, scale-to-zero), Locust surge |
| Telemetry | Prometheus (cAdvisor, 5 s scrape, 4-metric keep-list, 30 s rate window); Kubernetes API; server-side request logs; Locust per-tick window |
| Cluster tuning (documented) | kubelet housekeeping 5 s; container logs 200Mi×2; CPU limits frontend 400m, currency 500m, productcatalog 500m, recommendation 500m; cartservice .NET thread-pool minimum 32; redis pinned by digest |
| Load model | Locust, U_base = 30 users (frontend at 50–60 % of its CPU limit), wait 0.5–1.5 s, one TCP connection per request (realistic short-lived clients; keep-alive pinned load to one pod) |

## Control loop (CLAUDE.md §5.7)

- **Tick:** 20 s. Fixed monotonic schedule on B; collection deadline 3.0 s; action dispatch asynchronous; learner budget 1.0 s per tick (4 gradient steps take ~0.02 s).
- **Decision latency** (window close → dispatch), main evaluation: median 0.33 s, p95 0.89 s, max 8.0 s (3851 ticks); under netem median 1.44 s, p95 2.02 s. Pre-registered target: p95 ≤ 4 s.
- **Missing data:** imputation and staleness rules (§5.5). A stale next state is never stored in replay; 3 consecutive stale ticks invalidate the episode.

## MDP

**Observation (36-dim, all in [0, 1] or [−1, 1]):**
- 5 global features: server-side p95 latency (log-normalized against L_SLA), failure ratio, RPS / (3·RPS_base), Δlatency, Δfailures.
- 7 per managed service (×4 = 28): CPU utilization vs limit, CFS throttle ratio, memory utilization, readiness, replicas / max, restarts Δ, rollout recency.
- 3 env features: in-flight lock, ticks since the last action, telemetry-stale flag.
- **No ground truth** (fault identity, target, severity, injection time) is ever in the observation or the mask.

**Actions (12, discrete):**
- NOOP;
- RESTART ×4 (one per managed service);
- SCALE_UP frontend / cartservice / currencyservice;
- SCALE_DOWN frontend;
- RESTORE to golden: productcatalogservice, cartservice, currencyservice.

**Mask and lock:**
- Only NOOP is valid while an action is in flight. The global lock clears on rollout-complete, a 100 s timeout, or a dispatch error.
- Replica bounds (frontend 3, cart 2, currency 2, productcatalog 1) are enforced by **both** the mask and the executor.
- RESTORE is never masked by drift, so the mask cannot leak the fault.

**Reward:**
- `r = −(v + c(a) + 0.05·ρ)`, range [−1.09, 0].
  - `v = clip(ℓ + e)` is the SLA violation: `ℓ = clip(log2(Lat/L_SLA)/3)` and `e` is the failure excess over 1 % (saturating at 20 %).
  - Action cost c: NOOP 0, SCALE 0.02, RESTART/RESTORE 0.04.
  - ρ: normalized extra replicas.
- No MTTR shaping.

**Health and recovery:**
- Healthy tick: Lat ≤ L_SLA and fail ≤ 1 %.
- Recovered: 3 consecutive healthy ticks **and** the fault's cure condition holds.
- MTTR = time from injection to the first of those ticks.

## Faults (per episode: one fault or NULL)

| Fault | Injection | Cure condition | Severities | Probability |
|---|---|---|---|---|
| F1 bad deploy | env `EXTRA_LATENCY` on productcatalogservice | template clean + rollout complete | 300 ms / 600 ms / 1 s | 0.22 |
| F2 hot pod | StressChaos CPU on cart or currency | stressed pod gone | 1 or 2 workers | 0.22 |
| F3 scaled to zero | cart or currency → 0 replicas | ≥ 1 available | — | 0.22 |
| F4 traffic surge | Locust users ×2.5 / ×3.0 | SLA alone | — | 0.22 |
| NULL | none | — | — | 0.12 |

- **Episode timing:** lead-in 2–5 ticks; fault window 18 ticks (360 s, the MTTR censoring point); NULL episodes end at L + 6 ticks.
- **Reset:** active restore to the golden snapshot (delete StressChaos, restore templates and replicas, delete any HPA), wait for convergence, then settle ≥ 30 s plus **2** healthy ticks **[A4]** (recovery still needs 3).
- **Oracle smoke tests at the final state:** F1 600 ms 80 s; F2 cart w1/w2 80–100 s; F2 currency 40–60 s; F3 59–100 s; F4 80 s (all MTTR).

## Agent: masked discrete SAC + PER (in-house, PyTorch)

| Hyper-parameter | Value |
|---|---|
| Networks | actor and twin critics, MLP 36→256→256→12 (ReLU) |
| γ, n-step, τ | 0.93, 3, 0.005 |
| Learning rates | 3e-4 (actor, critic, α) |
| Batch, buffer | 128, 50,000 |
| Updates per tick | 4 (budget 1.0 s) |
| α | **fixed at 0.1 online [A6]** (the per-state entropy target 0.4·log(n_valid) is computed and logged, not applied) |
| Conservative critic | **CQL penalty, weight 1.0, offline and online [A5, A6]**: mean[logsumexp over valid actions of Q − Q(s, a_data)] added to each twin critic's loss |
| PER | proportional, α 0.5, β 0.4 → 1.0 over 15,000 gradient steps, priority = min(mean twin \|TD\|, 1) + 1e-3, IS weights on the critic only |
| Masking | invalid logits → −1e9; masked log-probs zeroed before π·logπ |
| Warm start | 120 runbook episodes with ε = 0.25 (random valid action; 1268 non-stale transitions), then 2,000 offline gradient steps (~9 s): **actor by behaviour cloning, critic by TD + CQL [A5]**, then online |
| Training | 200 online episodes per run, seeds 0 and 1 **[A8]** (seed 1: no probes); seed 0 greedy probes at 0, 75 and 150 episodes; final checkpoint used (no selection on evaluation data) |
| Ablation | cold-start run: same online update, empty buffer, no offline phase, **100 episodes [A7]** |

## Baselines

- **Kubernetes default:** NOOP; only Kubernetes' own controllers act (e.g. restart crashed containers).
- **Kubernetes + HPA:**
  - autoscaling/v2 on frontend, cart and currency; target AverageValue = 60 % of each pod's CPU limit (240m / 180m / 300m);
  - min = base, max = the agent's bounds;
  - present only during its own episodes.
- **Scripted SRE runbook:**
  - 7 ordered rules (in-flight wait; restore scaled-down; roll back recent changes it didn't make; scale the frontend on surge; restart the most throttled service; scale back when healthy; else NOOP);
  - same observation, never ground truth.

## Evaluation protocol (pre-registered)

- **Schedule `eval_v1`** (seed 2026): 10 episodes each of F1–F4 + 10 NULL = 50 per policy, with balanced targets and severities.
- **Interleaving:** every policy runs entry i, in a seeded random order, before entry i+1.
- **Policies:** k8s_default, k8s_hpa, runbook, PER-SAC warm, PER-SAC cold. Greedy for SAC.
- **Seed-1 block [A8]:** the full 50-entry schedule again with two policies interleaved: PER-SAC seed 1 and the runbook as a same-block anchor (drift check across days).
- **Metrics:**
  - MTTR (primary; censored at 360 s);
  - recovery rate;
  - cumulative SLA penalty;
  - FRR (NULL episodes with any action);
  - safety violations;
  - decision latency;
  - client-side p99 (reporting only).
- **Statistics:**
  - Mann–Whitney U with Holm correction per family; Cliff's δ; bootstrap 95 % CIs;
  - Kaplan–Meier with log-rank;
  - non-inferiority margin vs the runbook = 20 s (one tick).
- **Edge subset:** the first 4 entries per fault type (20 per policy), runbook vs PER-SAC, with netem +150 ms and 1 % loss on control-plane traffic only (Kube API and Prometheus; Locust untouched).
- **Exclusions:** invalid episodes are re-run on the same entry and counted. Each session starts with A on AC power, the same power mode, no other workloads, and a passing 10-min steady check.

## Measurement design facts worth one paragraph (details in Paper B)

- The SLA latency is measured **server-side** (frontend request logs) as **p95**, because:
  - client-side p99 tracked wireless RTT (r = 0.95);
  - p99 carries about twice the sampling noise of p95 at ~800 requests per tick.
- Client-side p99 is still logged and reported.
- Calibration: L_SLA = 70 ms, U_base 30, RPS_base 34.0; steady CV 0.24, 0 breaches.

## Threats to validity (facts to cover)

- Single-node cluster on a laptop; the wireless link; one application; two training seeds for the warm-started agent (shared warm-start data), one for the cold start; seed 2 and the uniform-replay ablation not run (time).
- Severity 1 of F2 is near the SLA (smoke peaks 1.01–1.10× threshold): a mild "gray" fault.
- The environment changed once (Machine A restart): handled by a steady check against the frozen calibration (Amendment 3).
- MTTR resolution is 20 s (tick).
- gRPC backends may pin connections, so scale-out of cart/currency may not spread load. Not tested: neither the agent nor the runbook used SCALE_UP cart/currency in evaluation (Table IV, `actions_main.csv`).
- After scale-from-zero (F3), a new pod's cAdvisor series can be missing for 3 ticks, which invalidates the episode (1 edge episode, re-run; 3 seed-1 training episodes).
- **Frontend pod churn rule [A4]:** after a frontend restart or scale, a short server-log read is accepted if it holds ≥ 5 expected tail samples (n·(1−0.95) ≥ 5, i.e. ≥ 100 requests); otherwise the tick is stale.

## Pre-registration amendments (one line each, for the Experimental Design section)

| # | Date | Before | What changed |
|---|---|---|---|
| 1 | 10 Oct | M4 | Load generator one connection per request; productcatalog and currency CPU limits 500m (F4 was uncurable) |
| 2 | 10 Oct | M4 | Prometheus keep-alive session; re-calibration (L_SLA 70 ms, U_base 30, RPS_base 34.0) |
| 3 | 13 Oct | M4 training | Machine A restart; steady check against the frozen calibration; HPA baseline specified; schedule generated |
| 4 | 19 Oct | reported training | First attempt discarded (stale ticks after frontend restarts); frontend churn measurement rule; reset settle 2 ticks |
| 5 | 19 Oct | reported training | Offline warm start = behaviour cloning + CQL critic (plain offline SAC did not start runbook-like) |
| 6 | 20 Oct | reported training | Online: CQL penalty kept, α fixed at 0.1 (plain online SAC drifted to acting in healthy states) |
| 7 | 20 Oct | cold-start/eval | Cold start 100 episodes (time); cold run before the evaluation |
| 8 | 24 Oct | seed-1 data | Seed-1 replication with a runbook anchor; confirmatory tests stay on seed 0 |

## Software and hardware versions

- K3s v1.36.4+k3s1; Online Boutique v0.10.7; Locust 2.46.6; Chaos Mesh (helm, containerd socket of K3s); Prometheus (cAdvisor job, keep-list of 4 metrics).
- Python 3.11; PyTorch CPU build, 2 threads; agent and PER sum-tree are in-house (`agents/masked_discrete_sac.py`, `agents/per_buffer.py`), unit-tested.
- Machine A: 16 CPU laptop, ~15 GB, Ubuntu, swap off, AC power for all reported sessions except a 49-min battery hold on 25 Oct (between episodes; one runbook episode partly on battery).
