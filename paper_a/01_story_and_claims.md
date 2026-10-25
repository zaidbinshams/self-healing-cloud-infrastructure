# 01 — Story, Claims, Abstract Points

> Facts and structure only. **The authors write every sentence.** Numbers come from `05_numbers.md` (IDs in brackets, e.g. [N12]).

## The story in five steps

1. **Problem.** Microservice faults cost availability, and recovery time (MTTR) is what operators measure. Today, recovery follows hand-written runbooks, one per fault class. RL could learn remediation from experience, but most RL-for-cloud work trains in simulators or on logs, or controls continuous resource knobs, not discrete repair actions.
2. **Gap.** We know of no work that learns a **discrete remediation policy online on the live system** (restart, scale, roll back, scale from zero), behind **hard safety guards**, with the controller across a **constrained edge link**, and compares it against Kubernetes, HPA and a scripted runbook under a **pre-registered** protocol.
3. **What we built.** A two-machine testbed (K3s cluster + controller over Wi-Fi), a 20 s real-time loop with a masked 12-action interface and a one-action-in-flight lock, and a masked discrete SAC agent with prioritized replay, warm-started from runbook data with behaviour cloning and a conservative (CQL) critic.
4. **What we found.**
   - The warm-started agent recovers 35/40 faults [N3] against 0/40 for Kubernetes and 10/40 for HPA [N1, N2].
   - It is non-inferior to the runbook on MTTR (pooled CI of the median difference −20.0 to +0.9 s, margin 20 s) [N10].
   - It never acted in a fault-free episode (0/10 in evaluation, 0/30 in training) [N14, N15].
   - It learned the runbook's fault-to-action mapping (Table IV). It is one tick faster on F1 and F4 [N5], but it never learned to act on F2-currencyservice [N7].
   - Without the warm start, the same agent recovers 15/40 and acts in 10/10 NULL episodes [N4, N16].
   - A second training seed replicates the result [N24–N27].
5. **Honest boundaries.**
   - HPA beats both the agent and the runbook on F4, the one fault it can see [N9].
   - The edge-network gap could not be shown to be within the margin with 16 episodes per policy (H5 not established) [N20].
   - We ran two seeds and one application.

## Research questions (use these, or turn them into the hypotheses H1–H5)

- **RQ1.** Can an RL agent trained online on a live cluster remediate faults faster than Kubernetes' built-in mechanisms? (H1)
- **RQ2.** Is it as good as a scripted runbook, and is it safe in fault-free periods? (H2, H4)
- **RQ3.** What does warm-starting from runbook data buy? (H3)
- **RQ4.** Does it hold up when the control link degrades, and across training seeds? (H5, seed-1 replication)

## Contributions (one sentence each, in your words)

1. A live-cluster remediation testbed with a guarded discrete action interface: masks, a global in-flight lock, least-privilege credentials, and replica bounds enforced twice. It runs with the controller across a wireless edge link.
2. A masked discrete SAC + PER agent with a runbook warm start (behaviour cloning + CQL), trained entirely online on the real system. Two documented failure modes of plain SAC (extrapolation error making the agent act in healthy states) and their fix.
3. A pre-registered evaluation: 5 policies × 50 interleaved episodes, an edge subset under netem, and a seed-1 replication with a same-block runbook anchor. Results include H1–H4 support and two negative/inconclusive findings (F4 vs HPA, H5).
4. Released artefacts: code, schedules, per-episode results and the analysis scripts (if the venue allows; anonymize as required).

## What we may claim (each backed by a number)

| Claim | Evidence |
|---|---|
| Learned online on the live cluster, with no simulator or digital twin | method; 200 training episodes per seed on the real cluster [N28] |
| Beats default Kubernetes on all fault types and HPA on F1–F3 | Table II; H1 p_Holm ≤ 0.03 [N8] |
| MTTR non-inferior to a scripted runbook | H2 CI [N10]; replicated on seed 1 [N25] |
| Safe in fault-free periods and under the guarded interface | H4 [N14, N15, N17] |
| Runbook warm start is necessary in this setting | H3 [N18, N19]; cold-start behaviour [N4, N16] |
| Learned behaviour is interpretable and matches the runbook's cures | Table IV (actions per fault) |
| Works under an emulated degraded edge link (descriptive) | edge medians [N20, N21] (but H5 not established) |
| Decisions are fast | decision latency median 0.33 s, p95 0.89 s [N22] |

## What we must NOT claim

- First RL on a real cluster (FIRM), first RL for remediation (E2E-REME, Dai et al.), a novel warm-start idea (AWARE bootstraps from autoscalers), or a novel guarded action interface (ARBITER).
- That PER helps: the uniform-replay ablation was not run.
- That the agent beats the runbook: it is non-inferior, not superior (pooled p = 0.41).
- A lower SLA penalty than the runbook: that part of H2 was a tie (p = 0.66) [N11].
- Robustness under netem "within margin": H5 is **not established** (CI upper bound 20.15 s vs 20 s).
- Generality beyond Online Boutique on one node, or beyond two seeds.
- Anything about F2-currencyservice except that the agent never learned it.

## What the results mean (for the Discussion)

- **F2-currencyservice blind spot.** Fixed α plus the CQL penalty makes the policy near-deterministic (entropy ≈ 0.03 nats), so it does not explore. The penalty then reinforces NOOP in states where the warm-start data rarely showed RESTART currencyservice. The same thing happened in seeds 0 and 1 (0 % greedy cure in training) [N7]. The runbook cures it only 3/5, and its 2 misses are below-SLA episodes where no rule fires.
- **F4 and HPA.** HPA scales on CPU and does not wait for 2 unhealthy ticks, so 4/10 of its F4 episodes recover at 40 s [N9]. It cannot see any other fault (0/30).
- **Cold start.** Without warm data, the agent settled on repeated RESTORE cartservice. That action is safe (no API call when the template already matches golden) but charged, and it is counted as a false remediation in 10/10 NULL episodes [N16].
- **Edge.** Per-fault medians change by at most one tick for most faults, and decision latency rises from 0.33 to 1.44 s median [N22, N23]. With n = 4 per fault, the bootstrap CI is ±20 s, so the 20 s margin is not testable at this size.

## Limitations (all of these go in the paper)

- One single-node cluster on a laptop, one application, a wireless link (all documented overrides).
- Two training seeds; the warm-start data is shared between seeds.
- The cold start ran 100 episodes, not 200 (Amendment 7). Its final-MTTR comparison is therefore at half the budget.
- No uniform-replay ablation.
- MTTR resolution is 20 s (one tick).
- F2 with 1 worker is a near-SLA, "gray" fault: some episodes never breach the SLA, so no policy is triggered.
- Measurement gap: after scale-from-zero, a new pod's cAdvisor series can be missing for 3 ticks, which makes the episode invalid. This affected 1 edge episode and 4 seed-1 training episodes; all evaluation episodes were re-run.
- The pre-registration was amended 8 times; every amendment is dated and was made before the data it affects.

## Title ingredients (pick and combine; write your own title)

online / live-cluster · reinforcement learning · discrete remediation · self-healing · microservices · Kubernetes · guarded / masked actions · edge · warm start · pre-registered

## Abstract: content points in order (150–200 words; write last)

1. Problem: fault remediation in microservices; runbooks; RL usually in simulators or offline.
2. What: online discrete-action RL on a live K3s cluster, guarded by masks and an in-flight lock, 20 s loop, controller over a wireless link.
3. How: masked discrete SAC + PER, warm-started from runbook data (BC + CQL), pre-registered evaluation against Kubernetes, HPA and the runbook.
4. Results: 35/40 vs 0/40 and 10/40; median MTTR 79 s, non-inferior to the runbook; 0/10 false remediations; without the warm start 15/40 and 10/10 false remediations; replicated on a second seed.
5. Boundary: one fault/target pair never learned; HPA faster on load surges.
