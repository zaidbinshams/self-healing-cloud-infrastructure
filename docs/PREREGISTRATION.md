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
