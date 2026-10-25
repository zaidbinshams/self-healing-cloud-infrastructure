# Final Review: Examiner Questions and Answers

Preparation for the final capstone review with an external examiner. The examiner will judge goals and objectives, novelty, ideas, design trade-offs, results, application, tools and standards, and presentation. This document lists the questions we expect under each heading, with a detailed answer, the evidence behind it, and what to say if the examiner pushes further.

Read `docs/PROJECT_EXPLAINER.md` first (what the project is) and `docs/CODE_EXPLAINER.md` (how the code works). Numbers in brackets such as [N10] are IDs in `paper_a/05_numbers.md`; Paper B numbers are in `paper_b/05_numbers.md`. Quote numbers from those files, never from memory.

## Contents

0. [How to answer](#0-how-to-answer)
1. [The pitch, in 60 seconds](#1-the-pitch-in-60-seconds)
2. [Objectives: planned vs delivered](#2-objectives-planned-vs-delivered)
3. [A. Goals and objectives](#a-goals-and-objectives)
4. [B. Problem, motivation and application](#b-problem-motivation-and-application)
5. [C. Design decisions and trade-offs](#c-design-decisions-and-trade-offs)
6. [D. The reinforcement-learning method](#d-the-reinforcement-learning-method)
7. [E. Measurement and telemetry](#e-measurement-and-telemetry)
8. [F. Evaluation design and rigour](#f-evaluation-design-and-rigour)
9. [G. Results and their interpretation](#g-results-and-their-interpretation)
10. [H. Novelty and related work](#h-novelty-and-related-work)
11. [I. Tools, standards and engineering practice](#i-tools-standards-and-engineering-practice)
12. [J. Limitations, threats to validity, future work](#j-limitations-threats-to-validity-future-work)
13. [K. Process, team and the use of AI tools](#k-process-team-and-the-use-of-ai-tools)
14. [L. Presentation and demo](#l-presentation-and-demo)
15. [Weak spots in the repository an examiner may find](#15-weak-spots-in-the-repository-an-examiner-may-find)
16. [Numbers to know by heart](#16-numbers-to-know-by-heart)
17. [Never say](#17-never-say)

---

## 0. How to answer

1. **Answer the question first, in one sentence.** Then give the evidence. Then the limitation, if there is one.
2. **Use numbers.** "35 of 40 faults recovered, against 10 for HPA" is stronger than "much better".
3. **State negatives plainly.** We have three: HPA beat the agent on traffic surges; H5 (edge non-inferiority) was not established; the agent never learned F2 on currencyservice. Each has an explanation. Saying them before the examiner finds them builds trust.
4. **Point to the artefact.** "That is Amendment 6 in `docs/PREREGISTRATION.md`, dated 20 October, before any evaluation episode." Examiners can check git history; let them.
5. **Do not overclaim.** The agent *matches* the runbook; it does not beat it. Prioritized replay was not ablated, so we claim nothing about it.
6. **If you do not know, say so** and say where the answer would be found. Never guess a number.

---

## 1. The pitch, in 60 seconds

> Microservice systems fail in ways Kubernetes does not fix by itself: a bad deployment, a CPU-starved pod, a service scaled to zero by mistake, a traffic surge. Operators fix these with hand-written runbooks. We asked whether a reinforcement-learning agent can learn this remediation **online, on a real cluster**, safely enough to run there, and how it compares with Kubernetes, the Horizontal Pod Autoscaler and a scripted runbook.
>
> We built a two-machine testbed: Google's Online Boutique on K3s, with the controller on a second machine over Wi-Fi, like an edge site. Every 20 seconds the agent observes 36 metrics and picks one of 12 repair actions, behind hard safety guards. The agent is a masked discrete Soft Actor-Critic with prioritized replay, warm-started from runbook demonstrations.
>
> Under a pre-registered protocol, it recovered 35 of 40 injected faults, against 0 for Kubernetes and 10 for HPA. Its recovery time is statistically non-inferior to the runbook (median about 79 s). It never acted in a fault-free episode. Without the warm start it failed. A second training seed gave the same result. HPA was faster on traffic surges, and one fault/target pair was never learned; we report both.

---

## 2. Objectives: planned vs delivered

Examiners compare the plan with the outcome. `PLAN.md` is the original plan (written before the work); `docs/PREREGISTRATION.md` records every change. Know this table.

| Planned (PLAN.md) | Delivered | Status |
|---|---|---|
| M1: K3s cluster, golden Online Boutique, least-privilege credentials | Done; agent and controller credentials checked with access reviews (`kubectl auth can-i`) | ✅ |
| M2: Chaos Mesh, Prometheus (5 s cAdvisor), Locust with `/tick` | Done; plus kubelet housekeeping 5 s for metric freshness | ✅ |
| M3: real-cluster Gym environment, injector F1–F4, reset, runbook, calibration, gates G1–G7, warm-start data | Done; G1–G4, G7 passed; G5 and G6 failed only because of one network outage during validation, reviewed and recorded (see Q-F9) | ✅ (with review) |
| M4: masked discrete SAC **in Tianshou** | In-house PyTorch implementation instead (approved 10 Oct; Tianshou would have forced downgrading pinned packages) | ✅ (changed, documented) |
| M4: uniform-replay ablation, 3 seeds | **Not run** (time) | ❌ limitation |
| M4: full PER, 3 seeds | 2 seeds (s0, s1) | ⚠️ partial |
| M4: cold-start ablation, mandatory, seed 0 | Done (100 episodes, not 200; Amendment 7) | ✅ (half budget) |
| M5: 10 policies × 50 episodes, interleaved | 5 policies × 50 (main), + edge 2 × 20, + seed-1 block 2 × 50 = 390 episodes | ⚠️ fewer policies (no uniform runs, one fewer seed) |
| M5: statistics (Mann–Whitney, Holm, δ, bootstrap, KM) | Done exactly as pre-registered | ✅ |
| M5: 7 figures | 5 figures + 6 tables; the action heat map is a table (Table IV); recovery rates and SLA penalty are in tables | ✅ (format changed) |
| Phase-2 stretch: compound faults, Linkerd, PPO baseline | Not run (stretch only) | — |
| Not planned | Paper B (measurement study), edge netem subset, seed-1 replication with runbook anchor | ➕ extra |

**How to say it:** "We met every core objective: the live-cluster environment, the agent, the baselines and the pre-registered evaluation. We cut the uniform-replay ablation and the third seed for time, and we state that as a limitation. We added an edge-network study, a replication seed and a second paper that were not in the plan."

---

## A. Goals and objectives

### A1. What was the goal of the project, in one sentence?

**Answer.** To find out whether a reinforcement-learning agent can learn to remediate microservice faults online on a real Kubernetes cluster, safely, and how its recovery time compares with Kubernetes, HPA and a scripted runbook.

**Detail.** The goal has three parts, and each was tested:
- *learn online on a real cluster*: all training (500 episodes over three runs) happened on the live system [N28];
- *safely*: zero safety violations and zero actions in fault-free evaluation episodes [N14, N17];
- *compared with what operators use*: three baselines under a fixed, pre-registered schedule.

### A2. Did you meet your objectives?

**Answer.** The core objectives, yes; two secondary ones were cut for time.

**Detail.** See the table in §2. Met: testbed, agent, baselines, pre-registered evaluation, the warm-start ablation, safety. Cut: the uniform-replay ablation and the third seed. Added: the edge-network subset, a seed-1 replication and the measurement study (Paper B).

**If pushed ("so the PER part of PER-SAC was never evaluated?").** Correct. PER is part of the agent as designed and pre-registered, but without the ablation we cannot say it helped, and we do not claim it did. It is listed as a limitation in both the pre-registration and the paper pack.

### A3. What were your hypotheses, and which held?

| | Hypothesis | Outcome |
|---|---|---|
| H1 | Agent has lower MTTR than Kubernetes and HPA on F1–F4 | **Supported** for F1–F3 against both and for F4 against Kubernetes. **Not supported for F4 against HPA**: HPA was faster (δ = −0.58, p = 0.031) [N8, N9] |
| H2 | MTTR no worse than the runbook (margin 20 s) and lower SLA penalty | MTTR non-inferiority **supported** (CI −20.0 to +0.9 s) [N10]. Lower SLA penalty **not supported** (tie, p = 0.66) [N11] |
| H3 | Warm start helps early learning and final MTTR | **Supported** (p = 6.5×10⁻⁹ on early return; p = 4.4×10⁻⁵ on MTTR) [N18, N19] |
| H4 | Zero safety violations; no action in 10 NULL episodes; training FRR ≤ 10 % | **Supported**: 0/10, 0/30, 0 violations [N14, N15, N17] |
| H5 | Under a degraded link, the gap to the runbook stays within the margin | **Not established**: CI upper bound 20.15 s vs 20 s, with 16 episodes per policy [N20] |

**How to say it:** "Four of five hypotheses held, two of them in part. We report the parts that failed with the same detail as the parts that held."

### A4. Why MTTR as the headline metric?

**Answer.** It is the metric operators and SRE practice use for incident response, and it is what a remediation policy directly changes.

**Detail.**
- MTTR here = time from injection to the first of 3 consecutive healthy ticks with the fault's cure condition met.
- "Healthy" = server-side p95 ≤ 70 ms and failure ratio ≤ 1 %.
- Requiring 3 ticks and the cure stops a lucky quiet tick from counting as recovery.
- Episodes that never recover are counted as 360 s (the end of the fault window), not dropped, so a policy cannot look better by failing more.
- Secondary metrics: recovery rate, cumulative SLA penalty, false remediation rate, actions per episode, decision latency.

### A5. What does "self-healing" mean in your project, exactly?

**Answer.** The loop that detects that the service violates its SLA, chooses a repair action, applies it through the Kubernetes API and checks that the service has recovered, with no human in the loop.

**Detail.** In autonomic-computing terms (Kephart and Chess, 2003) this is the MAPE-K loop: Monitor (telemetry every 20 s), Analyze and Plan (the policy), Execute (the action executor), Knowledge (the golden state and the learned policy). Kubernetes already heals one class of fault, crashed containers, by restarting them. We address faults where the containers stay up but the service is broken.

---

## B. Problem, motivation and application

### B1. Why is this problem important?

**Answer.** Downtime and slow responses cost money and users, and in microservice systems most incidents are not crashes that Kubernetes repairs by itself.

**Detail.**
- Kubernetes restarts crashed containers and (with HPA) adds replicas under CPU load. It does not roll back a bad configuration, restart a pod that is alive but CPU-starved, or undo a mistaken scale-to-zero.
- Our default Kubernetes baseline recovered **0 of 40** such faults, and HPA only the 10 load surges [N1, N2].
- Today those cases are handled by people following runbooks, which means paging someone, and the time it takes them to respond.

### B2. Why would anyone use RL for this instead of writing rules?

**Answer.** Rules have to be written and tuned by an expert for each fault class and system; an RL agent can learn the mapping from experience. Our result shows the learned policy can reach the quality of carefully written rules while staying safe.

**Detail.**
- Our runbook needed calibrated thresholds (throttle 0.40, traffic 1.5× baseline, 2-tick debounce) chosen with full knowledge of the faults.
- The agent got only the observation and the reward. It learned from runbook demonstrations plus its own online experience, and it rediscovered the correct cure for each fault it learned (Table IV, [N13]).
- Be honest about the limits: in this setting, with known faults and 200 training episodes, the agent matches the runbook rather than beating it. The value shown is that learning online on a live system is feasible and safe, not that RL replaces runbooks today.

### B3. Where could this be applied in practice?

**Answer.** Small or remote Kubernetes clusters where no operator is on hand and the link to a central team is unreliable: edge sites such as retail stores, factories or telecom edge nodes.

**Detail.**
- That is why the controller runs across Wi-Fi in our testbed, and why we tested an emulated degraded link (150 ms delay, 1 % loss).
- Realistic deployment path:
  1. learn from existing incident logs and runbook actions (our warm start);
  2. run in "shadow mode", recommending actions to a human;
  3. allow automatic actions only inside the guarded interface (mask, lock, least-privilege credential).
- Not production-ready as is: one application, one node, a fixed set of faults.

### B4. What is the social or business value?

**Answer.** Faster recovery means less time when users get errors or slow pages, and fewer night-time pages for engineers.

**Detail.** Median MTTR was about 79 s for the agent and the runbook versus 360 s (never recovered) for Kubernetes on the same faults [N6]. Caution: we did not measure business cost; keep the claim to recovery time.

### B5. Why Online Boutique?

**Answer.** It is a widely used open-source microservice benchmark (Google Cloud's demo application) with realistic service-to-service calls in several languages, so results are comparable and reproducible.

**Detail.** 11 deployments (10 services plus Redis). Version pinned to v0.10.7. The agent manages the four services on the critical path of a page view: frontend, cartservice, currencyservice and productcatalogservice. The others run but are outside the agent's control. Alternative benchmarks (DeathStarBench, Train Ticket) are heavier than one laptop node can run alongside the monitoring stack.

---

## C. Design decisions and trade-offs

### C1. Why not a simulator?

**Answer.** Because the effects that decide whether remediation works only exist on a real system, and we found several that a simulator would have hidden. The price is speed: 6–8 minutes per episode.

**Detail.** Real-system behaviour we discovered only because we ran on a real cluster:
1. **Metric freshness.** With the kubelet's default settings, the newest CPU sample was a median 12 s old, so the 30 s rate query returned nothing for 8–46 % of evaluations. We had to set cAdvisor housekeeping to 5 s.
2. **Load balancing per connection.** With keep-alive connections, scaled-out frontend replicas received *no* traffic (0.00 cores), so scaling up could not cure a surge. A simulator that spreads load evenly would have shown a cure that does not exist. Fixed with one connection per request (Amendment 1).
3. **Runtime stalls.** The .NET thread pool in cartservice caused stalls of hundreds of milliseconds that looked like faults. Found by measurement (Paper B).
4. **Log rotation** truncated latency reads every ~13 ticks; **pod replacement** removed a pod's log mid-window (Amendment 4).
5. **Rollout timing**, scale-from-zero metric gaps (a new pod's metrics missing for up to 3 ticks), and Wi-Fi drops.

Each of these changes what an agent learns or what a measurement shows. A simulator built before we knew them would not have contained them.

**Trade-off.** Real-cluster training is slow (about one day per 200-episode run), so we have 2 seeds instead of 5 and no uniform-replay ablation. We accepted fewer runs in exchange for results that describe a real system.

**If pushed ("simulators let you train on millions of episodes").** True, and a simulator could be used for pre-training in future work. But a policy trained in simulation must still be validated on the real system, and our contribution is showing the live-system part can be done safely. The project rule (`CLAUDE.md` §4.1) bans simulators so that every result comes from real physics.

### C2. Why discrete actions (restart, scale, restore) instead of continuous resource control?

**Answer.** Those are the actions an operator actually takes during an incident, each one is easy to audit, and a small set can be made provably safe with a mask.

**Detail.**
- 12 actions: NOOP; RESTART ×4; SCALE_UP frontend/cart/currency; SCALE_DOWN frontend; RESTORE ×3 (back to the known-good template).
- Continuous control (CPU quotas, replicas as a number) is what autoscaling papers such as FIRM do. It answers a different question (resource efficiency), and its actions are harder to bound.
- **Cost:** the catalogue is fixed. The agent cannot invent a new remedy, and a fault with no matching action cannot be cured.

### C3. How do you guarantee the agent cannot damage the cluster while it learns?

**Answer.** Safety does not depend on what the agent learns. Five independent guards limit what it can do, and they held: zero violations across 390 evaluation episodes and about 500 training episodes.

**Detail.**
1. **Action mask.** Invalid actions get probability 0: no scaling past the maximum (frontend 3, cart 2, currency 2, productcatalog 1), no scale-down below 1, no restart of a service with no pods.
2. **Executor re-check.** The executor checks replica bounds again before calling the API, in case the state changed.
3. **One action in flight.** A global lock allows one action at a time until the rollout completes (or 100 s pass).
4. **Least-privilege credential.** The agent's Kubernetes account can only get, list, patch and scale deployments, and read pods and logs, in one namespace. It **cannot delete pods** or touch any other namespace. Restarts are done by a rolling-restart annotation, not by deleting pods.
5. **No new actions, no changing resource limits from code** (project rule).

**Evidence.** `env/k8s_actions.py` (`compute_mask`, `scale_target`, `lock_should_clear`), `k8s/rbac/`, [N17].

### C4. Why is RESTORE never masked, even when nothing has changed?

**Answer.** If RESTORE were allowed only when a deployment differs from its known-good state, the mask itself would tell the agent which service has been tampered with: that is the fault's identity, which the agent must not see.

**Detail.** The cost is that the agent can choose a useless RESTORE. The executor then makes no API call (the template already matches golden), but the action is still charged its cost, so it is not a free probe. The cold-start agent made exactly this mistake, repeatedly choosing RESTORE cartservice [N16].

### C5. Why a 20-second tick? Isn't that slow?

**Answer.** It is the fastest interval at which the measurements are reliable and actions have had time to take effect.

**Detail.**
- Prometheus scrapes every 5 s, and the CPU rate needs a 30 s window with enough samples.
- Rollouts take tens of seconds. Acting faster than they complete would mean deciding on stale effects.
- Within the tick: collection takes at most 3 s, the decision a median 0.33 s [N22], and learning at most 1 s.
- **Cost:** MTTR is measured in 20 s steps. That is why the non-inferiority margin is 20 s (one tick).

### C6. Why one action at a time?

**Answer.** It keeps cause and effect clear, for the agent learning and for us analysing, and it prevents two rollouts from interfering.

**Cost.** A second fault during a rollout must wait. We did not test compound faults.

### C7. Why two machines and Wi-Fi? Why not one powerful machine?

**Answer.** The controller and load generator must not compete with the cluster for CPU (otherwise measured latency includes our own load), and the Wi-Fi link is the edge condition we wanted to study.

**Detail.**
- The design called for a wired link with RTT p99 < 2 ms. The deployed testbed used a Wi-Fi hotspot with RTT p99 up to 187 ms. That deviation was approved, documented as an "environment override" with relaxed preflight thresholds, and kept as a feature: it is a realistic edge constraint.
- Everything that depends on timing was made robust to it: concurrent collection under a deadline, asynchronous actions, typed failures, imputation, a watchdog that waits out outages.

### C8. Why server-side p95 latency as the SLA signal instead of client-side p99?

**Answer.** Client-side latency measured across the Wi-Fi tracked the network (correlation 0.95 with ping RTT), not the application; and p99 per 20 s window is dominated by sampling noise. The server-side p95 measures what the agent can actually fix.

**Detail (this is Paper B).**
- Server p99 tick CV 0.14 vs client p99 CV 0.60 in the same run (Paper B N11–N13).
- p99 sampling CV ≈ 0.097 vs p95 ≈ 0.058 at 20 s windows (Paper B N24, N25).
- With the same calibration rule, the client-side threshold came out 3–6× its median and missed most latency-only faults: F2 detected 3/29 vs 27/29 server-side (Paper B N40, N52).
- The change was approved and pre-registered before training (UNFREEZE 2026-10-09). Client-side p99 is still logged and reported [N23b].

**If pushed ("but users experience client-side latency, including the network").** Yes, and we report it. But the network is not something a remediation action can change. Rewarding the agent on it would punish or reward it for Wi-Fi conditions.

### C9. Why K3s and a single node?

**Answer.** K3s is a lightweight, CNCF-certified Kubernetes distribution that fits on our hardware next to the application, Prometheus and Chaos Mesh. A single node was the hardware we had.

**Cost.** No multi-node effects (scheduling across nodes, network partitions between nodes). That is a limitation, stated in the paper.

### C10. Why an in-house SAC instead of a library (Tianshou, Stable-Baselines3)?

**Answer.** The libraries did not fit the real-time loop or our pinned dependencies, and we needed rules a library does not implement: masks inside the soft value, excluding stale next states from the buffer, a 1-second update budget per tick.

**Detail.**
- Tianshou 1.2.0 needed older gymnasium and numpy (a downgrade of pinned packages). Tianshou 2.0.1 added many dependencies, and its data collector does not fit a 20 s tick loop.
- The decision was approved on 10 Oct and recorded in `CLAUDE.md` §5.12.
- To compensate, every rule (masking, soft value, TD target, α exclusion, priorities, β schedule, n-step returns) has a unit test (`tests/test_sac_per.py`).

### C11. Why does the reward have no MTTR term?

**Answer.** It does not need one: the reward is a penalty on every unhealthy tick, so recovering sooner directly means fewer penalised ticks and a higher return. Adding an MTTR bonus would be reward shaping that could bias the policy.

**Detail.** r = −(v + c(a) + 0.05 ρ):
- v = SLA violation (log-scaled latency excess plus error-rate excess), in [0, 1];
- c(a) = action cost: NOOP 0, SCALE 0.02, RESTART or RESTORE 0.04 (discourages needless actions);
- ρ = extra replicas above baseline (discourages leaving the cluster scaled up).

### C12. What did each trade-off cost you? (the "trade-off" question)

Summarise from `docs/PROJECT_EXPLAINER.md` §12:

| Choice | Gained | Cost |
|---|---|---|
| live cluster | real physics, no sim-to-real gap | slow; 2 seeds |
| hard masks | guaranteed safety | no learning of sequences during an action |
| RESTORE never masked | no leak of the fault | useless restores possible |
| server-side p95 | signal the agent can act on | excludes network time |
| conservative update (CQL + fixed α) | no false remediations | no exploration → F2-currency never learned |
| one-action lock | clear credit, no interference | compound faults not handled |
| pre-registration | credibility | every change needs a written amendment |

**The central trade-off to explain well:** safety in healthy states versus exploration. The conservative update stops the agent from inventing actions the data does not support. That gives 0 false remediations. The same mechanism stops it from trying rarely-seen actions, so F2 on currencyservice was never learned.

---

## D. The reinforcement-learning method

### D1. Explain your agent in simple terms.

**Answer.** It is two networks. The actor gives a probability for each of the 12 actions; the critic estimates how much future penalty each action leads to. The actor is trained to prefer actions the critic rates well, and invalid actions are always given probability zero.

**Detail.**
- Soft Actor-Critic (SAC) in its discrete-action form (Christodoulou, 2019): the value of a state is computed exactly as a weighted sum over the 12 actions, rather than by sampling.
- "Soft" means the objective rewards a little randomness (entropy), weighted by the temperature α, so the policy does not lock onto one action too early.
- Two critics are used and the minimum taken, to reduce overestimation.
- Prioritized replay samples surprising transitions (large TD error) more often.

### D2. Why SAC and not DQN or PPO?

**Answer.** We needed an off-policy method (each real episode takes 6–8 minutes, so every transition must be reused many times) with a stochastic policy we could warm-start by imitation. Discrete SAC fits both.

**Detail.**
- **PPO** is on-policy: it throws data away after each update, which is too wasteful at 6–8 min per episode.
- **DQN** is off-policy and would be a reasonable alternative, but it has no explicit policy to initialise by behaviour cloning, and its ε-greedy exploration is not compatible with "never act needlessly in a healthy state".
- SAC gives an explicit, maskable policy and a critic, and both can be warm-started from demonstrations.
- **Honest note:** we did not compare against DQN or PPO (a PPO baseline was a stretch goal and was not run). We do not claim SAC is the best choice, only that it is a sound one.

### D3. How does masking work, and what is the NaN problem?

**Answer.** Invalid actions get a logit of −10⁹, so softmax gives them probability 0. Their log-probabilities are then set to exactly 0 before any product, because 0 × log 0 = 0 × (−∞) = NaN would otherwise poison every loss.

**Evidence.** `agents/masked_discrete_sac.py` `masked_policy`; test `test_masked_policy_zeroes_masked_entries_without_nan`. Masking invalid actions this way is a valid policy gradient (Huang and Ontañón, 2022).

### D4. What is the warm start, and why was it needed?

**Answer.** Before learning online, the agent is trained offline on 120 recorded runbook episodes: the actor copies the runbook's actions (behaviour cloning), and the critic is trained with a conservative penalty (CQL). Without it, the agent did not learn a usable policy.

**Detail.**
- Data: 120 runbook episodes with 25 % random valid actions mixed in, so the data also shows what happens after non-runbook actions; 1268 usable transitions; 2000 gradient steps [N32].
- Why BC + CQL and not plain offline SAC: plain offline SAC overestimated actions that appear rarely in the data. The resulting policy put 45 % probability on a restore seen only 4 times, and acted on every tick of a healthy episode (Amendment 5).
- Evidence it matters: the cold-start agent recovered 15/40 and acted in 10/10 fault-free episodes; the warm agent 35/40 and 0/10 [N3, N4, N14, N16]. Training return over the first 100 episodes was much higher with the warm start (p = 6.5×10⁻⁹) [N18].

**If pushed ("so the agent is just copying the runbook").** It starts from the runbook and then learns online. Afterwards it is one tick faster than the runbook on F1 and F4 (median 59 vs 80 s, 60 vs 80 s) [N5]. It also lost the runbook's partial cure for F2 on currencyservice. So it is not a copy: it changed in both directions. Honestly, though, most of its behaviour is the runbook's fault-to-cure mapping, learned from data rather than written by hand.

### D5. What is CQL and why is it also used online?

**Answer.** Conservative Q-Learning adds a penalty that pushes down the critic's values for actions the data rarely shows. We keep it online because without it the agent drifted into acting in healthy states.

**Detail.**
- Penalty = logsumexp of Q over valid actions − Q of the action in the data.
- Attempt 4 (plain online SAC after the warm start): the probability of NOOP in the clean start state fell from 0.75 to 0.02 within 49 episodes, and every fault-free training episode after episode 14 contained actions (Amendment 6).
- Chosen by a rule fixed in advance ("keep greedy NOOP on clean start states and α within range over a full run's worth of replayed updates"), tested on recorded training data only. No evaluation data existed then.

### D6. Why fix α at 0.1? Isn't that a hack?

**Answer.** Fixed-temperature SAC is the original version of SAC (Haarnoja et al., 2018). We fixed it because the automatic α tuning pushed α upward regardless of the critic and undid the conservative critic.

**Detail.** In the offline replay test, CQL with learned α reached α = 0.92 (kill limit 1.0) and greedy NOOP fell to 38 %. CQL with α fixed at 0.1 kept greedy NOOP at 83–100 % (Amendment 6 table). **Cost:** the training policy became nearly deterministic (entropy ≈ 0.03 nats), so it explores very little. This is the cause of the F2-currency blind spot.

### D7. Explain γ = 0.93 and n-step = 3.

**Answer.** γ = 0.93 gives an effective horizon of about 1/(1−γ) ≈ 14 ticks, close to the 18-tick fault window. n = 3 propagates the reward of a cure back 3 ticks per update, matching the 3 healthy ticks that define recovery.

**Detail.** The n-step return is r_t + γ r_{t+1} + γ² r_{t+2}, then bootstraps with γ³ V(s_{t+3}). Stale steps break the chain (they are never stored).

### D8. Terminated vs truncated: why does it matter?

**Answer.** When an episode ends because the service recovered (terminated), there is no future to bootstrap. When it ends because time ran out (truncated), the cluster would have continued, so the critic bootstraps. Mixing them up biases the value estimates.

**Evidence.** `td_target` in `agents/masked_discrete_sac.py`; test `test_nstep_truncation_bootstraps`.

### D9. How do you handle missing or late telemetry in learning?

**Answer.** A tick with any missing or imputed value is marked stale. Transitions into stale states are logged but never stored for learning, and three stale ticks in a row end the episode as invalid.

**Detail.** Missing values are carried forward for at most 2 ticks, otherwise set to 0, and always flagged. The agent never trains on made-up dynamics. Stale ticks were rare: 17 of 2306 training ticks for seed 0 [N30].

### D10. Does the agent see the fault type? Isn't that cheating?

**Answer.** No. Fault type, target, severity and injection time go only into logs and `info`, never into the observation or the mask. The runbook sees exactly the same observation.

**Detail.** The observation does include "rollout recency" (a deployment's spec changed recently). That is something an operator also sees (a deploy just happened), not the fault label. Both the agent and the runbook use it to recognise a bad deploy (F1).

### D11. How do you know the training converged?

**Answer.** The training return flattened and the greedy policy stopped changing on a fixed set of recorded states. But with 200 real episodes we do not claim full convergence; we evaluate the final checkpoint as pre-registered.

**Detail.** Figure a3 shows the 10-episode rolling return for both seeds. A drift monitor checked greedy NOOP on recorded clean start states every 30 minutes during training (Amendment 6). Training recovery: s0 149/170, s1 158/183 faults [N29].

### D12. How long does training take, and on what hardware?

**Answer.** About one day per 200-episode run (6–8 minutes per episode, mostly waiting for the cluster). The learning itself is tiny: 4 gradient steps per 20 s tick, under 1 s, on CPU only with 2 threads.

---

## E. Measurement and telemetry

### E1. What exactly does the agent observe?

**Answer.** 36 numbers every tick, all scaled to [0, 1] or [−1, 1]:
- 5 global: SLA latency, failure ratio, request rate, and the changes in latency and failures;
- 7 for each of the 4 managed services: CPU use relative to its limit, CPU throttling, memory relative to its limit, ready replicas, replica count, recent restarts, rollout recency;
- 3 about the loop: an action in flight, ticks since the last action, stale data.

**Evidence.** `CLAUDE.md` §5.3; `env/telemetry.py` `build_obs`.

### E2. How do you know your measurements are correct and synchronised?

**Answer.** All timestamps come from one clock (Machine B), Prometheus is queried at B's exact boundary time, and the clock skew between machines was measured at about ±6 ms against a 200 ms gate.

**Detail.**
- Every source covers the same window (wall(T_k), wall(T_{k+1})].
- A preflight check measures skew at the start of every session.
- The steady-state latency is re-checked against the frozen calibration before each session (`scripts/session_start.sh`).

### E3. How was the SLA threshold (70 ms) chosen?

**Answer.** By a rule fixed in advance: 1.5 × the 95th percentile of steady-state tick latency, rounded up to 10 ms, measured over 90 fault-free ticks at the baseline load.

**Detail.** Steady median 24 ms, 95th percentile 41 ms → 1.5 × 41 = 61.5 → 70 ms (Paper B N60, N61). Calibration was frozen before training, and the code refuses to run if the measurement code changed after calibration (`env/contract.py` `load_calibration`).

### E4. What is the baseline load, and why that value?

**Answer.** 30 Locust users (about 34 requests/s), the load at which the frontend uses 50–60 % of its CPU limit with no fault [N23c].

**Why.** High enough that a 2.5–3× surge saturates the frontend (so F4 is a real fault), low enough that the healthy baseline is not already throttled.

### E5. Didn't you change CPU limits of the application? Doesn't that bias the results?

**Answer.** We did, before any training, and only where the healthy baseline was itself broken or a fault was impossible to cure. The changes apply equally to every policy, and every one is documented with its evidence.

**Detail.**
- frontend 200m → 400m: ~99 % throttled with no fault at the planned load;
- currencyservice 200m → 300m → 500m: throttled at baseline above the runbook's fault threshold; later saturated during the F4 surge;
- productcatalogservice 200m → 500m: capped at 1 replica and saturated during surges, so F4 could not be cured by any action;
- recommendationservice (not managed) 200m → 500m: background noise in latency;
- cartservice .NET thread-pool minimum: removed process stalls.

All were made in M2–M3 (2–10 Oct). Training began 19 Oct, the evaluation schedule was committed 13 Oct. See `CLAUDE.md` §2 "Cluster tuning" and Amendment 1.

**If pushed ("you tuned the environment until your agent worked").** No agent existed when these changes were made; they were found with the runbook and fault smoke tests in M3. The criterion was "is the baseline healthy, and is each fault curable by its intended action", not any learning result.

### E6. What happens when the network drops?

**Answer.** Collection has a 3 s deadline, so a lost source becomes a typed failure and imputation marks the tick stale. Longer outages make the episode invalid; it is re-run on the same schedule entry, and the watchdog waits for the link before resuming.

**Evidence.** Main evaluation: 2 episodes re-run after Wi-Fi outages on 23 Oct (one sac_per, one cold) [Exclusions table in 05_numbers].

---

## F. Evaluation design and rigour

### F1. How do you know you didn't tune on the test data?

**Answer.** The test schedule, metrics, statistics and checkpoint rule were fixed and committed before any training. Every later change is a dated amendment in git, made before the data it affects existed, and none used evaluation results.

**Detail, in time order (all verifiable in `git log`):**

| Date | Event |
|---|---|
| 10 Oct | Pre-registration committed: hypotheses, policies, metrics, statistics, exclusion and stopping rules; Amendments 1–2 (F4 fixes, final calibration) |
| 13 Oct | Evaluation schedule `eval_v1.json` generated from seed 2026 and committed (immutable); HPA baseline defined (Amendment 3) |
| 19 Oct | Amendment 4 (measurement fix after a training attempt's data-quality problem) and 5 (offline warm-start method) |
| 20 Oct | Amendment 6 (online update), final-run rule, Amendment 7 (cold start 100 episodes) |
| 20–23 Oct | Training and main evaluation |
| 24 Oct | Amendment 8 (seed-1 replication), written after the main evaluation but before any seed-1 data |
| 25 Oct | Completion report (reporting only) |

Further safeguards:
- **Final checkpoint only.** No checkpoint was selected using evaluation data.
- **No early stopping** on evaluation metrics.
- Amendments 5 and 6 were decided on training data and offline replays of recorded training data. The CQL weight was the standard default (1.0); 0.5 and 2.0 gave similar results, so it was not tuned on outcomes.
- **Final-run rule** (20 Oct): the run is restarted only for a broken experiment, never because results look bad. "An agent no better than the runbook is reported, not fixed."
- The code refuses to evaluate from a dirty working tree and records the hashes of the locked files in every result.

**If pushed ("Amendment 8 came after you saw the main results").** Yes. It adds a *replication*; it cannot change the main results, and the confirmatory tests stayed on the main evaluation. It reinstated a seed that was in the original plan (dropped earlier for time), was written before any seed-1 data, and committed in advance to reporting the seed whatever it showed. It includes a runbook "anchor" to detect cluster drift between blocks.

### F2. You discarded four training attempts. Isn't that cherry-picking?

**Answer.** The attempts were stopped for documented technical failures, not bad scores, all before any evaluation; they are archived and reported in the pre-registration.

| Attempt | Why stopped | Fix |
|---|---|---|
| 1 | 14 % of ticks stale after frontend restarts (pod log lost) | measurement rule for pod churn (Amendment 4) |
| 2 | only probe episodes run when a remaining measurement gap was found | threshold adjusted (Amendment 4 addendum) |
| 3 | offline warm start produced a policy that acted on every healthy tick | BC + CQL warm start (Amendment 5) |
| 4 | online drift into acting in healthy states; FRR above the pre-registered kill criterion | CQL online + fixed α (Amendment 6) |
| 5 | final run, under the final-run rule | — |

### F3. Why interleave the policies?

**Answer.** The cluster and the Wi-Fi change over days. Running every policy on schedule entry i, in a random order, before entry i+1 spreads that drift equally over all policies, so no policy gets all the good days.

**Evidence.** `eval/run_eval.py` `policy_order`. The seed-1 runbook anchor matched the main runbook (p = 0.70), so no drift was detected between blocks [N26].

### F4. Why only 10 episodes per fault type? Is that enough?

**Answer.** It is what the wall-clock budget allowed (250 main episodes ≈ 30 hours). It is enough for the large effects (δ = 1.0 vs Kubernetes) but not to detect small differences, which is why we use confidence intervals and a non-inferiority margin instead of claiming "no difference".

**Detail.** For H5 (16 episodes per policy) the confidence interval was about ±20 s, as wide as the margin itself. That is why H5 is "not established" rather than "failed".

### F5. Why Mann–Whitney and not a t-test?

**Answer.** MTTR is skewed, discrete (20 s steps), has many ties, and is censored at 360 s. Mann–Whitney compares ranks and needs no normality assumption.

**Also:**
- **Holm correction** controls the chance of any false positive across a family of tests. It is uniformly more powerful than Bonferroni.
- **Cliff's δ** is the matching effect size: P(agent < other) − P(agent > other), from −1 to 1.
- **Bootstrap CIs** (10 000 resamples) for the difference in medians, which is what non-inferiority is decided on.
- **Kaplan–Meier and log-rank** handle censoring properly, as a curve of "not yet recovered" over time.

### F6. What does "non-inferior" mean, and why a 20 s margin?

**Answer.** The agent is non-inferior if the upper end of the 95 % CI of (agent median − runbook median) is below +20 s. 20 s is one tick, the resolution of the measurement, so a smaller margin could not be measured.

**Detail.** Main: CI [−20.0, +0.9] s → non-inferior [N10]. Seed-1 block: [−0.7, +19.9] s → non-inferior [N25]. Edge: [−20.1, +20.2] s → not established [N20].

### F7. Why use medians with censored values? Doesn't that hide failures?

**Answer.** The opposite: a failed episode is counted as 360 s, the worst value, so failures raise the median. The alternative, "median over recovered episodes only", hides failures, which is why it is reported only as a secondary number.

**Watch out:** the recovered-only medians (agent 59.7 s, runbook 79.6 s [N6b]) make the agent look 20 s faster. Do not quote that as a win: it ignores the agent's 5 misses.

### F8. Re-running invalid episodes: isn't that selection?

**Answer.** Validity is decided by data quality, never by outcome (3 consecutive stale ticks, a failed injection, or missed ticks). The rule was pre-registered, and every re-run is counted and reported. In total: main 2, edge 1, seed-1 0.

### F9. Did all your validation gates pass?

**Answer.** Five of seven passed outright. G5 (reset success) and G6 (no SLA breaches in fault-free ticks) failed in the runbook validation, both because of a single documented hotspot outage during the run. That was reviewed and recorded before training, with thresholds unchanged.

**Detail (`reports/rolling_project_log.md`).** G5: 19/20 resets clean; the one hard reset happened while the cluster was unreachable. G6: the only breaching NULL tick was the moment the link dropped. It was a stale tick, the server latency itself was a normal 24 ms, and the CV part passed (0.239 < 0.30). Caveat recorded: G6's breach share rests on only 2 NULL episodes.

### F10. Are your results reproducible?

**Answer.** The procedure is fully reproducible: code, configuration, schedule, analysis and figure scripts are public, and the per-episode results are in `paper_a/figure_data/`. The exact numbers are not, because a real cluster is not deterministic. A rerun should give results of the same shape.

**Detail.**
- Everything that can be seeded is: Python, NumPy, Torch, the fault-plan RNG, the evaluation order.
- Cluster physics cannot be seeded, and we say so.
- All figures can be redrawn from the CSVs alone (`python paper_a/make_figures.py`).
- The raw logs (203 MB) are kept off GitHub but archived.

---

## G. Results and their interpretation

### G1. What are your main results?

| Policy | Faults recovered | Median MTTR | Fault-free episodes with an action |
|---|---|---|---|
| Kubernetes | 0/40 | 360 s | 0/10 |
| Kubernetes + HPA | 10/40 (all surges) | 360 s | 0/10 |
| Runbook | 38/40 | 80 s | 0/10 |
| PER-SAC warm | 35/40 | 79 s | 0/10 |
| PER-SAC cold | 15/40 | 360 s | 10/10 |

### G2. Why did default Kubernetes recover nothing?

**Answer.** Because none of these faults look broken to Kubernetes. Its self-healing restarts crashed containers and keeps the replica count at the declared value. In all four faults the containers stay up and the declared state is being honoured.

| Fault | Why Kubernetes does nothing |
|---|---|
| F1 bad deploy (extra latency env var) | pods are running and pass their health checks; the bad template *is* the desired state |
| F2 CPU stress in one pod | the pod is alive, just slow |
| F3 scaled to 0 | 0 replicas is what the spec now says |
| F4 surge | no autoscaler in the default configuration |

**If pushed ("so Kubernetes is a strawman").** It is the floor, the "do nothing extra" control, and it shows these faults need more than default reconciliation. The real competitors are HPA and the runbook.

### G3. Why did HPA win on surges (F4)?

**Answer.** HPA watches CPU directly on its own ~15 s cycle and scales as soon as CPU passes its target. It does not wait for our 20 s tick, or for the SLA to be breached first. So 4 of its 10 surge episodes recovered at about 40 s, against the agent's 60 s.

**Detail.**
- Medians are close (59.2 vs 59.7 s), but the distributions differ: HPA has more 40 s recoveries. Cliff's δ = −0.58, p_Holm = 0.031 [N9].
- HPA cannot see any other fault: 0/30 on F1–F3.
- **What it means:** for pure load surges, a CPU-driven autoscaler is the right tool. The agent's value is on the faults HPA cannot see. Future work: run the agent alongside HPA, letting HPA handle load while the agent handles configuration and pod faults.

**Never** say HPA "only won by 0.5 s": the test is on the whole distribution, and it is significant.

### G4. Isn't the runbook better? It recovered 38 vs 35.

**Answer.** On recovery time the two are statistically equivalent within one tick, and on SLA penalty they tie. The 3-episode gap is one fault/target pair the agent never learned. And the runbook was written by people who knew the faults; the agent was never told them.

**Detail.**
- MTTR: CI of the difference [−20.0, +0.9] s, non-inferior; Mann–Whitney p = 0.41 [N10].
- SLA penalty: medians 2.00 vs 2.00, p = 0.66 [N11].
- Per fault: agent faster on F1 (59 vs 80 s) and F4 (60 vs 80 s); slower on F3 (60 vs 50 s); on F2 it cures cartservice but never currencyservice [N5, N7].
- All 5 agent misses are F2 on currencyservice. The runbook cured only 3 of those 5 itself; its 2 misses were episodes that stayed below the SLA, so no rule fired [N7].
- Seed 1 shows the same pattern [N24, N25].

**Conclusion to state:** "The agent matches a hand-written expert runbook without being given the fault knowledge the runbook encodes. It does not beat it."

### G5. Why did the agent never learn F2 on currencyservice?

**Answer.** It is the price of the conservative update. With α fixed and the CQL penalty, the policy is nearly deterministic and does not explore. The warm-start data rarely showed RESTART currencyservice in these states, so the penalty kept that action's value down and NOOP's up.

**Detail.**
- Observed during training (20 Oct, after 53 episodes, before any evaluation): 0 of 8 such episodes recovered; on recorded F2-currency states, Q(RESTART cur) − Q(NOOP) fell from −0.70 to −1.58 (Amendment 6, interim observation).
- It was handled under the final-run rule: no change to training, the fault mix or the schedule, and reported like every other fault.
- F2 with one stress worker is a marginal fault: smoke peaks of 71 ms against a 70 ms threshold on currencyservice. The signal is barely above the threshold.
- Same in both seeds: 0/5 [N7, N24].
- A follow-up with learned α was declared as exploratory, but not run (time).

### G6. What did the agent actually learn?

**Answer.** The correct cure for each fault, without being told the fault: RESTORE productcatalogservice for a bad deploy, RESTART the hot service for CPU stress on cartservice, RESTORE the service scaled to zero, and SCALE_UP frontend (twice) for a surge. And NOOP when nothing is wrong.

**Evidence.** Table IV / `actions_main.csv`:
- F1: RESTORE pc in 10/10 (twice in 2 episodes);
- F2-cart: RESTART cart 5/5;
- F2-curr: no action 5/5;
- F3: RESTORE the target 10/10;
- F4: SCALE_UP fe (×2 in 8, ×1 in 2);
- NULL: none.

Actions per fault episode: 1.13 for the agent, 1.23 for the runbook [N13].

### G7. Why did the cold-start agent do so badly?

**Answer.** Without demonstrations, within 100 episodes it settled on one habit, RESTORE cartservice, every tick. That action is harmless (no API call when the template already matches golden) but useless for most faults.

**Detail.**
- It recovered all 10 F4 episodes (it also scaled the frontend) and the 5 F3-cartservice ones, where RESTORE cartservice is the cure, so 15/40 [N4].
- It acted in 10/10 fault-free episodes, about 10.9 actions each [N16].
- This is the H3 evidence that the warm start is needed. Caveat: the cold run had 100 episodes, not 200 (Amendment 7), so its final-MTTR comparison is at half the budget. The early-learning test (episodes 1–100 for both) is the stronger evidence.

### G8. Is two seeds enough?

**Answer.** It is weak evidence of robustness across seeds, and we say so. But both seeds give the same picture, and the runbook anchor shows the second block ran on an unchanged cluster.

**Detail.**
- Seed 1: 35/40, same misses (F2-curr), non-inferior within its block, 0/10 fault-free actions [N24–N27b].
- Pooled MTTR s0 vs s1: 79.1 vs 79.6 s, p = 0.11.
- On F1, seed 1 was slower (90 vs 59 s, p = 0.031 uncorrected): report it as descriptive variation, not a finding.
- Both seeds used the same warm-start data, so seed variation excludes data variation.

### G9. What happened under the degraded network (edge subset)?

**Answer.** With 150 ms added delay and 1 % loss, the agent and the runbook kept almost the same median recovery times (78.2 vs 78.5 s pooled), and decision latency rose from a median 0.33 s to 1.44 s, still well inside the tick. But with 16 episodes per policy the confidence interval was too wide to prove non-inferiority within 20 s.

**Detail.** CI [−20.1, +20.2] s; the upper bound 20.15 s is just above the 20 s margin. Recovered: agent 14/16, runbook 15/16. No actions in fault-free episodes [N20–N23].

**Never** say "H5 nearly passed" or "passed within rounding". It is not established; the sample was too small to decide.

### G10. How fast are decisions?

**Answer.** From the close of the measurement window to dispatching the action: median 0.33 s, 95th percentile 0.89 s (target ≤ 4 s); under netem, median 1.44 s [N22, N23].

### G11. Were there any safety incidents?

**Answer.** None. Zero mask violations, zero executor refusals and zero execution errors for the agent in evaluation [N17]. No action in any fault-free evaluation episode, and a 0/30 false-remediation rate in seed-0 training [N14, N15].

### G12. What surprised you most?

Pick one and tell it as a story, with the evidence:
1. **The keep-alive finding:** scaling out did nothing, because every load-generator user was pinned to the old pod. A whole fault class was uncurable until we found it.
2. **Plain SAC drifting into acting in healthy states:** the critic overestimated actions it had rarely seen. The textbook method did not survive contact with a real system without the conservative fix.
3. **The client-side signal measuring Wi-Fi instead of the application** (Paper B).

---

## H. Novelty and related work

### H1. What is new in your work?

**Answer.** Not any single component, but the combination, done and measured on a real system:
1. a discrete remediation policy learned **online on a live cluster**, with no simulator or digital twin;
2. behind a **guarded action interface** (mask that does not leak the fault, one action in flight, least privilege, bounds checked twice), with zero violations;
3. with the controller across a **constrained wireless link**;
4. evaluated under a **pre-registered, interleaved, replicated** protocol against the tools operators actually use (Kubernetes, HPA, runbook), with negative results reported;
5. evidence of how standard SAC fails on a live system (extrapolation error, drift into acting) and what fixes it;
6. a separate **measurement finding** (Paper B): where and how the SLA signal is measured decides what a controller can see.

### H2. What do you not claim?

**Answer.** We are not the first to use RL on a real cluster (FIRM, OSDI 2020, did resource management) or for remediation (E2E-REME; Dai et al., 2026). Warm-starting RL from an existing controller is not new (AWARE, USENIX ATC 2023, bootstraps from autoscalers). Guarded action interfaces exist (ARBITER, 2026). And we do not claim the agent beats the runbook, or that prioritized replay helped.

### H3. How is this different from closest work?

| Work | What it does | Difference |
|---|---|---|
| FIRM (Qiu et al., OSDI'20) | RL for fine-grained resource management of microservices | continuous resource knobs; we use discrete repair actions |
| AWARE (Qiu et al., ATC'23) | RL autoscaling bootstrapped from HPA/VPA in production | autoscaling; we apply bootstrapping to remediation and measure it with a cold-start ablation |
| Dai et al. (2026) | remediation as a constrained MDP learned **offline from incident logs** | we learn **online** on the live system |
| E2E-REME (FSE'26 industry) | LLM fine-tuned with RL in simulated experience, writes remediation playbooks | we learn a closed-loop policy with fixed actions, sub-second decisions, no LLM |
| ARBITER (2026) | guarded agentic control (deterministic or LLM planners) for Kubernetes remediation | we put a **learned** policy inside the guard; same finding that autoscaling alone misses non-load faults |
| GuardedAct (Cai et al., 2026) | AI-suggested fixes tested on a sandboxed digital twin first | no twin: learning and evaluation on the live cluster, made safe by masks and least privilege |

Details and verification status for each: `paper_a/06_references.md`. Read the papers you will mention.

### H4. Why not use an LLM agent? Everyone is doing that.

**Answer.** A different trade-off. Our controller decides every 20 s in a median 0.33 s, on a CPU, with no external service, a fixed and auditable action set, and a learning signal from the system itself. An LLM call across a lossy edge link adds seconds of latency and an external dependency, and its outputs are harder to bound.

**Detail.** LLM approaches (E2E-REME, ARBITER's LLM planner) are strong at diagnosis and at writing new remediations from text. A sensible combination: an LLM proposes or explains, and a learned, guarded policy acts. We did not test LLMs, so claim nothing about their performance.

### H5. What is the novelty of Paper B?

**Answer.** An empirical account of what makes a per-tick SLA latency signal noisy in an edge self-healing loop (runtime stalls, wireless network, quantile sampling), a rule for sizing the quantile window (about 8 tail samples per window give ~10 % sampling CV), and evidence that the measurement point changes which faults the controller can detect.

**Not claimed:** being the first to study tail-latency measurement (Treadmill, Lancet, Tales of the Tail exist). Those study benchmarking, not a controller's per-tick signal.

### H6. Is this publishable, or just a student project?

**Answer.** We have prepared two papers, and the venues are for our guide to choose. Paper A: the live-cluster RL remediation study. Paper B: the measurement study. Both have complete data packs and LaTeX skeletons. Whether they are accepted is for reviewers to decide.

---

## I. Tools, standards and engineering practice

### I1. Which tools and technologies did you use, and why?

| Layer | Tool (pinned version) | Why |
|---|---|---|
| Orchestration | K3s v1.36.4+k3s1 | CNCF-certified lightweight Kubernetes; fits on one laptop node |
| Application | Online Boutique v0.10.7 | standard open microservice benchmark |
| Fault injection | Chaos Mesh 2.8.4 (StressChaos) + Kubernetes API | CNCF chaos-engineering tool; CPU stress inside one pod |
| Monitoring | Prometheus (cAdvisor, 5 s scrape, 4-metric keep-list) | standard; queried by HTTP API at exact times |
| Autoscaler baseline | Kubernetes HPA (autoscaling/v2) + metrics-server | the standard autoscaler |
| Load | Locust 2.46.6 with a custom `/tick` endpoint | programmable, tick-aligned load and client metrics |
| RL environment | Gymnasium 1.4.0 API | standard RL environment interface (successor of OpenAI Gym) |
| Learning | PyTorch 2.14.1 (CPU), NumPy 2.4.6 | in-house masked SAC + PER |
| Kubernetes access | official `kubernetes` Python client 36.0.3 | typed API calls with timeouts; no shelling out to kubectl |
| Analysis | SciPy 1.17.1, Matplotlib 3.11.2 | statistics and figures |
| Quality | pytest 9.1.1 (196 tests), ruff 0.16.9 | unit tests and linting |
| Network emulation | Linux netem (tc) | degraded-link condition |
| Clock | chrony / Windows w32time | ±6 ms skew between machines |
| Writing | LaTeX, IEEEtran | IEEE conference format |

### I2. What standards and good practices did you follow?

**Answer.**
- **Kubernetes:** standard APIs and patch types (strategic merge, JSON Patch RFC 6902), the rollout-restart mechanism, RBAC least privilege (separate agent, controller and admin credentials).
- **SRE:** SLA/SLO thresholds, MTTR, runbooks, error budgets (the failure-ratio limit).
- **Autonomic computing:** the MAPE-K reference loop.
- **RL environment:** the Gymnasium API (`reset`/`step`, terminated vs truncated).
- **Statistics:** pre-registration, non-parametric tests with multiple-comparison correction, effect sizes, bootstrap CIs, survival analysis.
- **Software engineering:** pinned dependencies, type hints, frozen config objects, unit tests, linting, Conventional Commits, an MIT licence, locked parameters with recorded change approvals.
- **Publishing:** IEEE template, IEEE's AI-disclosure policy, reference verification.

Do not claim compliance with any formal certification standard (ISO etc.); we did not pursue one.

### I3. How did you make the system reliable enough to run for a week?

**Answer.**
- A watchdog restarts failed runs, waits out network outages without counting them as failures, re-runs the session checks before resuming, and stops with an alert after 3 failures.
- Every remote call has a timeout and returns a typed failure, never a crash or a NaN.
- Logs are flushed every tick; checkpoints every 25 episodes and on shutdown.
- Every run and evaluation is resumable.

**Evidence.** `scripts/watchdog.sh`, `scripts/session_start.sh`; operational events listed in the completion report.

### I4. How did you test code that controls a real cluster?

**Answer.** Pure logic is separated from I/O and unit-tested on fixtures recorded from the real cluster: normalisation, imputation, reward, mask, runbook rules, SAC and PER math, statistics. 196 tests run in about 8 seconds with no cluster. Code that touches the cluster was validated with preflight checks and fault smoke tests on the real system.

### I5. How is configuration managed?

**Answer.** Every number lives in `config/contract.yaml` or the generated `calibration.json`; there are no magic numbers in code. Locked files can change only with a `CONTRACT-CHANGE:` commit and human approval. Each run records the hashes of these files plus the git SHA.

### I6. What about security?

**Answer.** Three separate credentials with least privilege: the agent can patch four deployments in one namespace and cannot delete pods; the fault injector has its own controller credential; the admin credential is used only by humans and bootstrap scripts. Credentials are gitignored. Runtime code never touches system namespaces.

### I7. Is your code and data available?

**Answer.** The code, configuration, evaluation schedule, analysis scripts and per-episode results are public on GitHub under the MIT licence. The raw logs (203 MB) are archived offline because of their size; the derived per-episode CSVs are enough to recompute every table and figure.

### I8. How much code is there, and how was quality checked?

**Answer.** Five Python packages (`env`, `agents`, `eval`, `scripts`, `tests`) plus Kubernetes manifests and shell scripts; about 130 commits from 3 Aug to late Oct 2026; 196 unit tests; ruff clean. Point to `docs/CODE_EXPLAINER.md` for a walkthrough.

---

## J. Limitations, threats to validity, future work

### J1. What are the main limitations?

Say them before you are asked:
1. one application, one single-node cluster, one wireless link;
2. two seeds for the warm agent, sharing the same warm-start data; one cold seed at 100 episodes;
3. no uniform-replay ablation, so no claim about PER;
4. MTTR resolution of 20 s;
5. F2 with one worker is a marginal fault (some episodes never breach the SLA);
6. the agent cannot explore enough to learn rare faults (F2-currency);
7. the method changed twice before the reported training (Amendments 5 and 6), each documented with its evidence;
8. cluster physics is not deterministic.

### J2. Threats to validity

| Type | Threat | Mitigation |
|---|---|---|
| Internal | cluster drift over days | interleaving; runbook anchor (p = 0.70) |
| Internal | measurement artefacts | Paper B diagnosis; server-side p95; stale-tick rules |
| Internal | experimenter choices after seeing data | pre-registration, dated amendments, final-run rule |
| Construct | MTTR at 20 s resolution | margin = one tick; Kaplan–Meier in addition |
| Construct | faults are injected, not real incidents | four common incident classes, each with a ground-truth cure |
| External | one app, one node, Wi-Fi | stated; the testbed and protocol are reusable |
| Statistical | small n per fault | CIs and effect sizes; non-inferiority instead of "no difference"; H5 reported as not established |

### J3. Would this scale to a large production system?

**Answer.** Not shown. The state grows by 7 features per managed service and the action set by a few actions per service, so the design scales linearly. But more services mean more combinations to learn, and multi-node effects are untested. A production version would also need shadow mode and human approval, as described in B3.

### J4. What happens with a fault you did not train on?

**Answer.** Not tested. The runbook has an explicit "unknown incident" rule that does nothing and logs. The agent would choose by its learned values, and the mask keeps that safe but not necessarily useful. Generalisation to unseen faults is future work.

### J5. What would you do next?

1. Controlled exploration that keeps safety: a learned α with a floor, or exploration only during detected incidents, to learn rare faults like F2-currency.
2. Combine the agent with HPA (HPA for load, the agent for everything else).
3. Multi-node clusters, more applications (DeathStarBench, Train Ticket), more seeds, the uniform-replay ablation.
4. Compound faults (a planned stretch goal).
5. Learning from real incident logs; shadow-mode deployment.

### J6. If you started again, what would you change?

Honest options:
- Budget wall-clock time for 3+ seeds and the ablation from the start.
- Build the measurement study (Paper B) first: half of M3 was spent discovering that the signal was noisy.
- Collect warm-start data with more coverage of rare actions in the states where they cure, so the conservative update has evidence for them.

---

## K. Process, team and the use of AI tools

### K1. How was the work organised?

**Answer.** In five milestones with explicit exit gates (`PLAN.md`): cluster (M1), telemetry and faults (M2), environment and runbook (M3), agent and training (M4), evaluation (M5). Then two papers. Decisions were recorded in the pre-registration, the rolling project log (`reports/rolling_project_log.md`) and commit messages.

**Team roles.** Fill this in yourselves before the review. Each member should be able to say what they did, and should be ready to explain any file in the repo using `docs/CODE_EXPLAINER.md`.

| Member | Main responsibilities |
|---|---|
| | |
| | |
| | |
| | |

### K2. Did you use AI tools? Who wrote the code?

**Answer, honestly:** yes. We used Claude Code (Anthropic) as an AI coding assistant throughout. It wrote much of the implementation, the analysis scripts, the figures and the documentation, under our direction. We set the goals, made the design and method decisions, approved every change to the cluster and to locked parameters, ran and operated the hardware, and reviewed the outputs. The paper text is written by us, and the papers disclose the AI assistance as IEEE requires.

**Why this answer matters:**
- The git history shows it. Commits carry a `Co-Authored-By: Claude` trailer, so denying or downplaying it would be found.
- Check your institution's and examiner's rules on AI use before the review, and follow them.
- Expect follow-up questions that test understanding ("explain this line", "why JSON Patch here?"). Prepare with `docs/CODE_EXPLAINER.md` and `docs/PROJECT_EXPLAINER.md` until every member can answer them without notes.

**If pushed ("then what did *you* contribute?").** Point to decisions that were the team's: the research question and the real-cluster rule; the choice of faults and baselines; the approval of the environment overrides, CPU-limit changes and measurement change; the pre-registration and the final-run rule; running the testbed for a week, including handling outages and power loss; interpreting the results and writing the papers. Give concrete examples you were involved in.

### K3. How did you make decisions when something went wrong?

**Answer.** Find the cause, fix it, never loosen a gate threshold. Record the evidence and the decision before collecting the data it affects.

**Example to tell:** F4 could not be cured. We found three stacked causes in order (keep-alive pinning, productcatalog CPU, currency CPU), fixed each with evidence, recalibrated, and recorded all of it as Amendment 1 before training.

### K4. What was the hardest problem?

Good candidates, each with evidence: the latency signal's noise (G6, a week of diagnosis, Paper B); the F4 fault being uncurable (keep-alive pinning); online SAC drifting into acting (Amendments 5–6).

### K5. How much time and compute did the experiments take?

**Answer.** About 500 training episodes and 390 evaluation episodes at 6–8 minutes each, plus 140 runbook episodes for validation and the warm start. That is roughly a week of continuous cluster time, 20–25 Oct for the final runs. All of it on two consumer machines, CPU only.

### K6. What did you learn?

Pick personal, specific answers. Shared ones:
- real systems have measurement problems that dominate the algorithm;
- safety is easier to guarantee by construction (masks, credentials) than to learn;
- pre-registration forces honesty and makes negative results publishable rather than embarrassing.

---

## L. Presentation and demo

### L1. Can you show it working?

**Answer.** The cluster is no longer running (experiments finished 25 Oct), so the demo uses recorded data:
- the per-episode results (`paper_a/figure_data/episodes_main.csv`) and the figures;
- one episode's tick-by-tick log, showing fault injection, the action and recovery;
- `pytest -q`, which runs 196 tests in about 8 seconds without a cluster;
- regenerating all paper figures from the CSVs (`python paper_a/make_figures.py`).

Bringing the testbed back up live is possible but takes setup time (`README.md`, `PLAN.md` M1–M2) and both machines.

### L2. Why should we believe your figures?

**Answer.** They are generated by committed scripts from the recorded data. Anyone can regenerate them from the CSVs in the repo, and every number in the text has an ID in `05_numbers.md` pointing to its source file.

### L3. What is the one figure that summarises the project?

**Answer.** The Kaplan–Meier curve (Fig. a1): the fraction of faults not yet recovered over time, per policy. The agent and the runbook drop fast and level at 0.125 and 0.05; HPA levels at 0.75 and Kubernetes stays at 1.0. It shows recovery speed and failures in one picture.

### L4. Presentation tips

- Slide figures R01–R17, a suggested slide order, slide titles, talking points and check-values: `presentation/README.md`.
- One message per slide. Lead with the result table and the Kaplan–Meier figure.
- Show the architecture diagram early (`docs/PROJECT_EXPLAINER.md` §3).
- Give one formula at most on a slide (the masked softmax or the reward), with a sentence of intuition.
- Put the negative findings on their own slide, titled "Honest findings".
- Each member should present the part they know best, and be ready to answer on any part.
- Rehearse the questions in this document out loud, with one person playing the examiner.

---

## 15. Weak spots in the repository an examiner may find

Know these before an examiner points at them.

| What they may see | The answer |
|---|---|
| Paper B's measurement runs use 40–50 users, but the experiments use 30 | Paper B's runs (U40, U50) were the diagnosis before calibration; U_base = 30 was calibrated afterwards (`config/calibration.json`). Final values: server-side p95 SLA, 30 users, HPA AverageValue at 60 % of each pod's CPU limit (Amendment 3, `k8s/hpa/hpa-baseline.yaml`) |
| `CLAUDE.md` §3 and `PLAN.md` name files that do not exist (`agents/probe.py`, `config/runs/*.yaml`, `tests/test_masked_sac.py`, `tests/test_per_buffer.py`, `scripts/run_eval_interleaved.py`) | They show the layout as designed. As built: greedy probes are part of `agents/train.py` (`--probe-every`); the one run setting is `config/runs/edge_degraded.env`; SAC and PER tests are in `tests/test_sac_per.py`; interleaving is in `eval/run_eval.py`. The as-built map is `docs/REPO_GUIDE.md` |
| `PLAN.md` mentions Tianshou, 10 policies, 3 seeds, HPA "70% CPU" | The original plan. Changes are recorded in `CLAUDE.md` (in-house SAC, 10 Oct) and `docs/PREREGISTRATION.md` (HPA definition, seeds, amendments). See §2 above |
| `CLAUDE.md` §5.1 shows `golden_overrides: {}` and `l_sla_factor` comments | That block is the *initial* content; the live values are in `config/contract.yaml`, and the tuning table in `CLAUDE.md` §2 lists every override |
| The agent is called "PER-iSAC" in some files and "PER-SAC" in others | Same agent. "PER-iSAC" was the working name; the papers use PER-SAC |
| Commits co-authored by Claude | See K2: disclose it plainly |
| Four discarded training attempts | See F2: technical failures, archived, documented |
| Amendment 8 after the main evaluation | See F1: a replication, written before its data, confirmatory tests unchanged |
| Gates G5 and G6 failed in validation | See F9: one documented outage, reviewed, thresholds unchanged |
| "No uniform ablation, yet it is called PER-SAC" | See A2: PER is part of the design; no claim that it helped |
| Early notes and working files mentioned in old commits (`docs/CAPSTONE_DEFENSE_NOTES.md`, `docs/paperA_methods_pack.md`, `docs/paperB_outline.md`, `docs/paperB_results.md`) | Removed on 25 Oct after the paper packs and this document replaced them (every number was checked to be in the packs). They remain in the git history |

---

## 16. Numbers to know by heart

| What | Number |
|---|---|
| Faults recovered (Kubernetes / HPA / runbook / agent / cold) | 0 / 10 / 38 / 35 / 15 of 40 |
| Median MTTR, agent vs runbook (censored) | 79.1 vs 79.6 s |
| Non-inferiority CI (agent − runbook) | [−20.0, +0.9] s, margin 20 s |
| Per-fault medians, agent / runbook | F1 59/80, F2 240/89, F3 60/50, F4 60/80 s |
| HPA vs agent on F4 | δ = −0.58, p = 0.031, HPA faster |
| Fault-free episodes with an action (agent / cold) | 0/10 / 10/10 |
| Training false-remediation rate (s0) | 0/30 |
| Safety violations | 0 |
| Warm vs cold early return | p = 6.5×10⁻⁹ |
| Seed 1 | 35/40; CI [−0.7, +19.9] s; anchor drift p = 0.70 |
| Edge | 78.2 vs 78.5 s; CI upper 20.15 s → not established |
| Decision latency | median 0.33 s, p95 0.89 s; netem median 1.44 s |
| L_SLA / baseline load | 70 ms server-side p95 / 30 users, 34 req/s |
| Episodes | ~500 training, 390 evaluation, 140 runbook |
| Tests | 196 unit tests, ~8 s |
| Paper B headline | client p99 vs ping r = 0.95; F2 detected 27/29 server vs 3/29 client |

---

## 17. Never say

- "The agent beats the runbook." (It is non-inferior; p = 0.41.)
- "PER improved learning." (Not tested.)
- "H5 passed" or "nearly passed". (Not established.)
- "HPA only won by half a second." (The distribution test is significant.)
- "It works on any Kubernetes system." (One app, one node.)
- "We are the first to use RL for self-healing." (We are not.)
- "The results are exactly reproducible." (The procedure is; the numbers are not.)
- "We did not use AI." (We did; it is disclosed.)
- Any number not in `paper_a/05_numbers.md` or `paper_b/05_numbers.md`.
