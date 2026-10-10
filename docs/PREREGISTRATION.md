# Pre-Registration — Paper A Experiments (M4/M5)

> Committed **before any M4 training run**. The git timestamp of this file is the registration time. Changes after training starts require an `UNFREEZE:` commit and must be reported in the paper. Frozen inputs: `config/contract.yaml`, `config/calibration.json` (U_base 33, L_SLA 60 ms server-side p95), `config/golden_live.json`.

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
