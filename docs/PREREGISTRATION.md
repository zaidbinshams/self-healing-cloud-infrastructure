# Pre-Registration — Paper A Experiments (M4/M5)

> Committed **before any M4 training run**. The git timestamp of this file is the registration time. Changes after training starts require an `UNFREEZE:` commit and must be reported in the paper. Frozen inputs: `config/contract.yaml`, `config/calibration.json` (U_base 30, rps_base 34.0, L_SLA 70 ms server-side p95; see Amendments 1–2), `config/golden_live.json`.

## 1. Hypotheses

| ID | Hypothesis | Primary comparison |
|---|---|---|
| H1 | PER-SAC (warm-started) achieves lower MTTR than default Kubernetes and HPA on F1–F4 | sac_per vs k8s_default, k8s_hpa |
| H2 | PER-SAC (warm-started) achieves MTTR no worse than the scripted runbook (non-inferiority, margin 20 s = one tick) and lower cumulative SLA penalty | sac_per vs runbook |
| H3 | Runbook bootstrapping improves early learning and final performance: higher mean return over the first 100 training episodes, and lower final MTTR | sac_per vs sac_per_cold |
| H4 | PER-SAC keeps zero safety violations; in evaluation it takes no action in any of the 10 NULL episodes (0/10), and its FRR over all NULL training episodes is ≤ 10 % | sac_per (all conditions) |
| H5 | Under degraded control-plane network (netem), the PER-SAC vs runbook MTTR gap does not widen beyond the non-inferiority margin | sac_per vs runbook, edge condition |

## 2. Policies and conditions

- **Main evaluation:** k8s_default (NOOP), k8s_hpa (NOOP + HPA objects), runbook (ε = 0), sac_per (seed 0, warm-started), sac_per_cold (seed 0, cold-start). Greedy (argmax over masked logits) for SAC.
- **Edge subset:** runbook and sac_per under `config/runs/edge_degraded.env`.
- **Training:** 200 episodes per SAC run, seed 0, fixed before training. Checkpoint used for evaluation = the final checkpoint (no checkpoint selection on evaluation data).

## 3. Schedule and protocol

- `eval/schedules/eval_v1.json`, seed 2026: 10 episodes each of F1–F4 (targets/severities balanced) + 10 NULL = 50 per policy; generated once, committed, immutable.
- **Interleaved:** schedule episode *i* is run for every policy (seeded random order) before *i + 1*.
- **Edge subset:** stratified, deterministic: the first 4 schedule entries of each of F1–F4 and NULL (20 per policy), interleaved between runbook and sac_per, under `config/runs/edge_degraded.env` (+150 ms, 1 % loss on control-plane traffic only).
- **Edge validity rule:** before the edge subset, a dry run (≥ 15 ticks, NULL) under the condition. If > 10 % of ticks are stale, loss is reduced to 0.5 % (environment measurability, decided before any edge results).

## 4. Metrics

| Metric | Definition | Role |
|---|---|---|
| **MTTR** | `wall(T_t) − t_inject` with t the first of 3 consecutive healthy ticks with the cure condition (CLAUDE.md §5.6); 20 s resolution; non-recovered episodes censored at `fault_max_ticks` (360 s) | **primary** |
| Recovery rate | share of fault episodes recovered within `fault_max_ticks` | secondary |
| Cumulative SLA penalty | Σ v_t over the episode | secondary |
| FRR | share of NULL episodes with ≥ 1 executed non-NOOP action; plus actions per NULL episode | safety |
| Safety violations | executed actions outside the mask, replica-bound breaches (executor refusals), credential errors | safety (must be 0) |
| Decision latency | p95 of window close → dispatch | context |
| Client-side p99 | logged end-to-end latency | reporting only (not an objective) |

## 5. Statistical analysis

- Per fault type: two-sided **Mann–Whitney U** on MTTR (censored values at 360 s), **Holm** correction across all pairwise comparisons in a family; effect size **Cliff's δ**; **bootstrap 95 % CIs** (10 000 resamples) of median MTTR differences.
- Pooled over F1–F4: **Kaplan–Meier** time-to-recovery curves; log-rank test.
- H2/H5 non-inferiority: the upper bound of the 95 % bootstrap CI of (median MTTR_sac − median MTTR_runbook) < 20 s.
- H3: Mann–Whitney on per-episode return of episodes 1–100 (training logs) and on evaluation MTTR.
- Claims are made only for comparisons significant after Holm correction or with CIs excluding zero; ties are reported as ties.

## 6. Exclusions and validity

- Episodes with `valid = False` (≥ 3 consecutive stale ticks, failed injection, missed ticks) are excluded and **re-run with the same schedule entry**; the number of exclusions per policy and their causes are reported.
- `EnvironmentDegraded` resets (e.g. hotspot outages) are logged and reported; they do not count as episodes.
- No result-dependent changes: contract, calibration, schedule, training length, checkpoint choice and analysis are fixed by this document.
- An SLA (`L_SLA`) adjustment is allowed only **before** M4 training, only if the M3 gates show it necessary (e.g. false remediations in NULL episodes), as a documented UNFREEZE. Never after training or evaluation results are seen.
- MTTR is reported at 20 s tick resolution (per-second "fine" MTTR dropped: computing it server-side would change the calibration's measurement path; client-side it would reintroduce hotspot noise).

## 7. Stopping rules

- Training stops at 200 episodes (no early stopping on evaluation metrics). The CLAUDE.md / PLAN.md kill criteria (α divergence, FRR > 10 %, Q-gap growth) may abort a run; an aborted run is reported, fixed, and restarted from scratch.
- If wall-clock runs short, the pre-declared cut order applies: edge subset 20 → 10 episodes per policy; then evaluation 50 → 40 per policy.

## 8. Amendments (all before any M4 training run)

### Amendment 1 — 2026-10-10, before M4 (F4 smoke findings)

The M3 F4 smokes showed that F4 could not be cured by any policy, for three stacked reasons found and fixed in order (human-approved, each a documented CONTRACT-CHANGE or approved deviation):

1. **Load generator:** keep-alive Locust users stayed pinned to the frontend pod they first connected to, so scaled-out replicas received no load (0.00 cores). Locust now opens one connection per request (`4db5dab`).
2. **productcatalogservice** (max 1 replica) then saturated at 200m: limit raised to 500m (`2d1f678`).
3. **currencyservice** then saturated at 300m: limit raised to 500m (`a675b86`).

Re-calibration (`f5e4acb`): **U_base 30, rps_base 33.65, L_SLA 60 ms**, G6 CV 0.215 (passes the 0.25 design gate) with 0 breaches. At this state, F4 at 3.0× and at 2.5× recovers after SCALE_UP frontend (MTTR 80 s each), and F2 with one stress worker remains detectable on both targets (peaks 77 ms and 71 ms vs 60 ms).

Unchanged: hypotheses, policies, schedule design, metrics, statistics, exclusions, stopping rules. Consequences for reporting: client-side p99 now includes one TCP handshake per request; the M3 runbook validation runs as stage 0 of `scripts/chain_m4.sh` before warm-start collection, and training starts only after gates G1–G7 pass or a human has reviewed them.

### Amendment 2 — 2026-10-10, before M4 (telemetry transport)

Prometheus queries now reuse one keep-alive HTTP session (`cdc39a4`; library retries still disabled), because a fresh TCP connection per query made ticks stale under hotspot SYN loss (2 of 30 fixture ticks). The change touches the calibration measurement path, so the calibration was redone (`b8b95e4`): **U_base 30, rps_base 34.0, L_SLA 70 ms**, G6 CV 0.239, 0 breaches, 1 of 90 ticks stale. This calibration is final for M4 and M5. Against it, F2 with one stress worker is a marginal, near-SLA fault (smoke peaks 77 ms cartservice, 71 ms currencyservice); results are reported per severity. Nothing else changes.

### Amendment 3 — 2026-10-13, before M4 training (Machine A restart; HPA baseline)

- **Machine A restart.** A rebooted between 10 and 13 Oct with a kernel update (7.0.0-34 → 7.0.0-38) and a new hostname, so K3s registered it as a new node. The stale node object was deleted, and Prometheus was recreated with a fresh volume on the new node (its volume was pinned to the old name; no experiment data lives in Prometheus). Kubelet tuning survived unchanged.
- **Steady check against the frozen calibration**, with the rule fixed before looking at the data: median tick p95 within ±30 % of 24 ms, q95 ≤ 61 ms, breaches ≤ 2 %. The first check failed (median 69 ms) because A was on battery with other workloads running. With A on AC and the other workloads stopped, the second check passed (median 20 ms, q95 23 ms, 0 breaches, CV 0.058; `data/m3/steady_check_20261013b.jsonl`). The calibration (`b8b95e4`) stands. Run protocol: every session starts with A on AC, the same power mode, no other workloads, and a passing steady check.
- **HPA baseline (k8s_hpa)** is now specified: autoscaling/v2 HPAs for frontend, cartservice and currencyservice, `AverageValue` = 60 % of each pod's CPU limit (240m / 180m / 300m), min = base, max = contract max; none for productcatalogservice (max 1). They are present only during k8s_hpa episodes (created after the reset settles, deleted in every reset).
- **Schedule** `eval/schedules/eval_v1.json` was generated from seed 2026 (`ddcf7bd`) before any training.

### Amendment 4 — 2026-10-19, first training attempt discarded; measurement fix; reset settle

- **First sac_per_s0 attempt discarded** after 14 training episodes (started 15:51, stopped 17:30; archived under `data/*/_discarded/`).
  - All 21 stale ticks in it (14 % of 148) followed an agent RESTART of the frontend: the replaced pod's request log leaves with the pod, so the next server read fell short of the completeness rule and was stale.
  - Stale transitions are never stored, so the agent could not learn the consequences of that action, and repeated restarts made episodes invalid (which would also have invalidated evaluation episodes).
  - The decision was made on this data-quality observation alone; no learning results were assessed.
- **Measurement fix** (`362e445`): when the set of frontend pods changed since the previous tick, a short server read is accepted as the tick's latency if it holds ≥ 8 expected tail samples for the SLA quantile (n·(1−q) ≥ 8, new locked parameter `telemetry.server_churn_min_tail_samples`); the tick is flagged `server_partial`. Without pod churn the completeness rule is unchanged.
  - Replaying 90 recorded steady ticks gives identical features with 0 partial acceptances.
  - In a live test, a deliberate frontend rollout restart produced 0 stale ticks.
- **Calibration re-stamped, not re-measured** (`ebcb08c`): the same 90 recorded ticks give the same values (L_SLA 70 ms, rps_base 34.0, U_base 30, CV 0.239). Steady-state measurement is provably unaffected, and today's session steady check passed (median 22 ms, q95 28 ms, 0 breaches).
- **Reset settle** (`362e445`): 2 consecutive healthy ticks before an episode starts (was 3), saving ~20 s per episode. The recovery/MTTR definition (3 healthy ticks) is unchanged; every episode also has 2–5 lead-in ticks before any fault.
- **Training restarts from zero** with the same pre-registered settings: warm start from the same 120 runbook episodes, 200 episodes, probes at 0/75/150, seed 0.
- **Addendum (same day, before any training episode):** server-read logging (`f6b12c9`) showed the remaining stale ticks after frontend restarts at n = 131–143, just under the 160 needed for 8 tail samples. The threshold was lowered to 5 tail samples (≥ 100 requests, ~12 % sampling CV) and each episode is seeded with the frontend pod set of the last reset tick, so tick-0 churn is recognized (`c3f2b4c`). The calibration was re-stamped again (`f0e51a8`, values unchanged) and the fixture re-recorded. The second attempt had run only greedy probe episodes (no training episodes); it is archived, and training restarts from zero.

### Amendment 5 — 2026-10-19, offline warm-start method (before any reported training)

- **Finding.** The episode-0 greedy probe of attempt 3 (right after the offline phase) recovered 0 of 4 faults and acted in a fault-free episode on every tick. Inspection of the offline-trained agent on the 303 healthy runbook states where the runbook took NOOP:
  - the critic valued NOOP below actions the data barely contains (e.g. RESTORE currencyservice, seen 4 times);
  - the policy put 5 % on NOOP and 45 % on that RESTORE.

  This is the known extrapolation error of plain off-policy (SAC) updates on a fixed dataset. The warm-started agent therefore did not start runbook-like, which defeats the purpose of runbook bootstrapping (H3).
- **Change (offline phase only).**
  - The actor is trained by **behaviour cloning** (cross-entropy toward the data's actions).
  - The critic gets a **CQL penalty** (Kumar et al., 2020; weight 1.0, the standard default, new locked parameter `rl.offline_cql_weight`) added to the SAC TD loss.
  - α is not updated offline.
  - The **online phase is unchanged** (pre-registered masked SAC + PER), and the cold-start run has no offline phase, so it is unaffected.
- **Offline validation** (no cluster time; seeds 0 and 1):

  | | Plain offline SAC | BC + CQL (weight 1.0) |
  |---|---|---|
  | Greedy policy agrees with the runbook: healthy states | — | 100 % |
  | Greedy policy agrees with the runbook: all runbook decisions | — | 98 % |
  | Greedy policy agrees with the runbook: where the runbook acted | — | 84 % |
  | Critic argmax agrees with the runbook: healthy states | 7 % | 100 % |
  | Critic argmax agrees with the runbook: where the runbook acted | 16 % | 83–86 % |

  Weights 0.5 and 2.0 gave similar results; 1.0 was chosen as the default, not tuned on outcomes.
- Attempt 3 (3 training episodes) is archived. Training restarts from zero; probes at 0/75/150 and all other settings unchanged.

### Amendment 6 — 2026-10-20, online update: CQL penalty and fixed α (attempt 4 aborted)

- **Finding (attempt 4, warm start = Amendment 5, online phase = pre-registered masked SAC + PER).** All valid fault episodes recovered, but the policy drifted toward acting in healthy states before any fault (scale-up / restart / restore of currencyservice on the first tick).
  - On the clean episode-start state: π(NOOP) went from 0.75 (warm start) to 0.02 after 49 training episodes; the greedy policy chose NOOP on 6 % of 186 recorded start states (warm start: 100 %).
  - Training return per 10 episodes fell from −1.29 to −2.00; actions per episode rose from 3.5 to 6.2. Every NULL training episode after episode 14 contained actions (false remediations, the FRR > 10 % kill criterion of §7).
  - The drift also blocked F2 on currencyservice: a pre-scaled target fails the injector's "exactly 1 pod" precondition, so 2 such episodes were invalid.
  - The critic valued acting above NOOP on the start state (Q(SCALE_UP cur) − Q(NOOP) = +0.80 at episode 14), although realized returns of episodes that began with an action were not better (mean discounted return −1.12 vs −0.75).
- **Offline diagnosis (no cluster time).** From the attempt-4 warm-start checkpoint, the real recorded transitions (120 runbook episodes + attempt 4) were replayed through the online update for 8000 gradient steps (≈ one full training run), measuring the greedy action on 186 recorded clean start states:

  | Online update | greedy NOOP at start, at 2000 / 4000 / 6000 / 8000 steps | α at 8000 |
  |---|---|---|
  | pre-registered (no CQL, α learned) | 0–4 % throughout | 0.26 |
  | α fixed at 0.1, no CQL | 2 / 3 / 6 / 2 % | 0.10 |
  | CQL 1.0, α learned | 100 / 61 / 47 / 38 % | 0.92 (kill limit 1.0) |
  | CQL 0.1 / 0.3 / 0.5, α learned | ≤ 56 % at every point | 0.41–0.76 |
  | **CQL 1.0, α fixed at 0.1** (seeds 0, 1) | **100 / 100 / 83–86 / 85–88 %** | 0.10 |

  The critic's extrapolation error on actions the data rarely shows in healthy states (the Amendment 5 failure mode) returns online. With α learned, the per-state entropy target (0.4 · ln n_valid ≈ 1 nat in healthy states) drives α up whatever the critic does, which erodes the conservative critic and approaches the α kill limit. The replay is pessimistic for the chosen variant: it contains attempt 4's habit transitions, which a fresh run does not collect. On unhealthy fault states the greedy policy picked the curing action 29–30 % of the time with the chosen variant vs 32 % for the warm start (most such states are after the cure was dispatched, so this is a relative check only).
- **Change (online phase).**
  - The critic loss keeps the **CQL penalty** with the existing locked weight `rl.offline_cql_weight` = 1.0 (the key name is historical; no contract value changed).
  - **α is held at `rl.alpha_init` = 0.1** (no online α update; the α loss is still computed and logged). This is SAC with a fixed temperature (Haarnoja et al., 2018, first version); §5.12 rule 5's α update is not applied in training. The α kill criterion is therefore inert.
  - Everything else is unchanged: masked discrete SAC actor and critic targets, n-step 3, PER and β schedule, 4 updates per tick, 200 episodes, probes at 0/75/150, seed 0. The cold-start run and the uniform-replay ablation use the same online update, so comparisons stay like-for-like.
  - Chosen by the pre-declared rule "keeps the greedy policy at NOOP on clean start states and α within its kill range over a full run's worth of replayed updates", using recorded training data only; no evaluation data exists.
- **Expected cost.** Near-deterministic training policy (entropy ≈ 0.03 nats), so less exploration: the online phase refines the runbook-like warm start rather than exploring widely.
- **Restart.** Attempt 4 (58 training episodes) is archived under `data/*/_discarded/sac_per_s0_20261019_attempt4_online_drift`. The online phase restarts from the same offline warm-start checkpoint (`warm.pt`: the offline phase is unchanged by this amendment and seeded, so re-running it gives the same network). The episode-0 probe is re-run.
- **Monitoring (added).** Every 30-minute status check evaluates the current checkpoint's greedy action on the fixed set of recorded clean start states (and fault states); a fall of greedy NOOP below 90 % is reported immediately.
- **Final-run rule (agreed 2026-10-20 01:30, after 6 training episodes and the episode-0 probe, before any further results).** Attempt 5 is the final sac_per_s0 run. It is restarted only for a broken experiment: a measurement or infrastructure bug that invalidates data, a pre-registered kill criterion, or the drift monitor below 90 % greedy NOOP on two consecutive checks. Slow learning, or an agent no better than the runbook, is reported, not fixed. Interruptions (outages, operator pauses) resume from the last completed episode; an interrupted episode is re-run, and an episode truncated by an outage is logged invalid, excluded from analysis and counted toward the 200 (§7 counts episodes).
- **Interim observation and declared exploratory follow-up (2026-10-20 ~06:00, after 53 training episodes, before any evaluation episode).**
  - Observation: the agent stopped acting on F2 with target currencyservice (0 of 8 recovered, no actions). On recorded runbook F2-currencyservice fault states, greedy cure fell from 30 % (warm start) to 0 %, and Q(RESTART currencyservice) − Q(NOOP) fell from −0.70 to −1.58 while NOOP returns were −5 to −9 per severity-2 episode. Other fault types are unaffected. Interpretation: with α fixed and the CQL penalty, the agent does not explore, so the penalty reinforces its own NOOP choices in these states.
  - Handled under the final-run rule: training continues unchanged, the fault mix (contract `episode.*`) and the evaluation schedule `eval_v1` (5 of 50 episodes are F2-currencyservice) are not changed, and the confirmatory evaluation reports this fault like every other.
  - **Exploratory follow-up (not confirmatory; run only after the confirmatory evaluation, if time allows):** continue from the final sac_per_s0 checkpoint for 40 training episodes with the pre-registered α update re-enabled (§5.12 rule 5, CQL penalty kept), same fault distribution and seed stream. Outcomes: F2-currencyservice recovery in those episodes, the Q(RESTART cur) − Q(NOOP) trend and greedy cure on the fixed state set, greedy NOOP on clean start states, and actions in NULL episodes; then the eval_v1 schedule for the follow-up checkpoint. Reported separately and labelled exploratory.

### Amendment 7 — 2026-10-20, cold-start length and order (before any cold-start or evaluation episode)

- **Context.** sac_per_s0 (attempt 5) finished its 200 training episodes at 17:33. Machine B is available only in limited windows over the next days, and the guide meeting is on Thu 22 Oct.
- **Cold-start length (scenario S2).** sac_per_cold_s0 runs **100 training episodes** (not 200), seed 0, the Amendment 6 online update, no offline phase, probes at 0 and 75. Reason: wall-clock only; 200 cold-start episodes (~20–26 h at an expected 6–8 min per episode) do not fit before the evaluation.
- **Order.** The cold-start run comes first; the main evaluation then runs **all five policies interleaved** as in §3 (sac_per_cold from its episode-100 final checkpoint). No change to §3.
- **Effect on H3.** The early-learning test (mean return over training episodes 1–100) is unchanged. The final-MTTR comparison sets the cold agent at 100 episodes against sac_per at 200, i.e. half the training budget; it is reported with this caveat and is weaker evidence than the early-learning test.
- **Evaluation size.** Entries run in schedule order. The pre-declared cut (§7: main 50 → 40 per policy, edge 20 → 10) applies only if wall-clock runs short; it is decided on time, never on results. Completing all 50 entries and the full 20-episode edge subset is the pre-registered plan, not an extension.
- **Pre-declared optional extension (decided after the Thu 22 Oct meeting, on available time and the guide's advice).** sac_per_cold_s0 may be continued from episode 100 to 200 (same seed; resumed as after an interruption; `ep100.pt` is kept and remains the confirmatory checkpoint). Its episode-200 checkpoint is then evaluated on eval_v1, interleaved with a re-run of sac_per on the same entries (equal-budget comparison under the same cluster conditions). Because the decision may be made after interim evaluation results are visible, this comparison is **secondary**: it is reported in full whatever its outcome and never replaces the episode-100 confirmatory test. The F2-currencyservice exploratory follow-up (Amendment 6) competes for the same time and stays exploratory.
- **Stopping rule for the cold-start run (addendum, 2026-10-20 18:40, during the episode-0 probe, before any cold-start training episode).** For sac_per_cold_s0 the PLAN.md manual criteria (FRR > 10 %, probe no better than the warm start, Q-gap growth) are reported, not used to stop the run: high early FRR and weak probes are the expected outcome of no warm start and are part of what H3 measures. The run is stopped only for a data-invalidating bug or the automatic non-finite-loss criterion (the α-range criterion is inert under Amendment 6). Interruptions resume from the last completed episode as in Amendment 6's final-run rule.

### Amendment 8 — 2026-10-24, second training seed (after the main evaluation; before any seed-1 data)

- **Context.** The main evaluation (`eval_v1_main`, all five policies, 50/50 entries) finished at 08:50 on 24 Oct; the edge subset is running. Seeds 1–2 were in the original plan (PLAN.md M4) and were dropped for wall-clock (PLAN_OF_ACTION §2: "Dropped: … seeds 1–2"). Machine B is available until about Sun 25 Oct 18:00, which fits one more seed, not two. This amendment reinstates **seed 1 only**, to test whether the sac_per result replicates across an independent training run. It is written before any seed-1 training or evaluation episode exists.
- **Run `sac_per_s1`.** Identical to the final `sac_per_s0` (Amendment 6 online update: CQL penalty `rl.offline_cql_weight`, α fixed at `rl.alpha_init`; Amendment 5 offline BC+CQL warm start; 200 training episodes; all contract, calibration and golden values unchanged), with `--seed 1`.
  - The seed sets the network initialisation, the actor's action sampling, PER sampling and the env RNG that samples the training fault plans (`agents/train.py`, `agents/masked_discrete_sac.py`, `agents/per_buffer.py`, `env/boutique_env.py`).
  - The warm-start dataset is **shared** with seed 0: the one runbook collection `data/transitions/warmstart_runbook` (ε = `runbook.warmstart_epsilon`). Seed-to-seed variation therefore excludes variation in the warm-start data; this is reported.
  - **No in-training probes** (`--probe-every 0`), for wall-clock (~1 h). Probe episodes never enter replay, so this does not change training; seed 1 is characterised by its training log and its evaluation.
  - Amendment 6's final-run rule applies: the run is restarted only for a broken experiment (a measurement or infrastructure bug that invalidates data), never for its results; interruptions resume from the last completed episode.
- **Evaluation `eval_v1_seed1`.** The full `eval_v1` main schedule (50 entries, schedule order, order seed = schedule seed), **two policies interleaved**: `sac_per_s1` (final checkpoint) and `runbook` as a **same-block anchor**. The anchor measures drift between this block and the main evaluation (different days); the runbook is deterministic, so a change in its per-fault outcomes or MTTR indicates a changed cluster, not a changed policy.
- **Analysis.** The confirmatory tests of H1–H5 stay on the main evaluation (seed 0) and the edge subset, exactly as pre-registered. Seed 1 is reported as a **replication**:
  - sac_per_s1 per-fault recovery, MTTR and SLA penalty, NULL actions (H4);
  - H2's non-inferiority rule applied within the seed-1 block (sac_per_s1 vs the anchor runbook, same bootstrap);
  - anchor runbook vs main-evaluation runbook (drift check), reported descriptively;
  - sac_per seed 0 and seed 1 side by side (training curves and evaluation). No seed is dropped or replaced, whatever it shows.
- **Time rule.** If B's availability ends before the seed-1 evaluation completes, the completed entries (schedule order) are reported and the shortfall stated; decided on time, never on results.
- **Not run (limitations).** Seed 2, the uniform-replay ablation, the cold-start extension to 200 episodes (Amendment 7's optional extension, not taken), and the F2-currencyservice exploratory follow-up.
