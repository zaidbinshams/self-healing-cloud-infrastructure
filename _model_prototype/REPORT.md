# PER-iSAC Prototype — Expected Results and Two Simulated Test Runs

> **Read this first.** Every number from a test run in this bundle comes from a **synthetic toy simulator**
> (`prototype/toy_env.py`), not from the K3s cluster. The simulator is hand-written, its constants are
> guesses loosely anchored to the M2–M3 measurements, and the calibration values (L_SLA = 450 ms,
> RPS_base = 30 req/s) are placeholders. These results are illustrative only. They must not be presented
> as cluster measurements, and CLAUDE.md §4.1 forbids using them as the project's training or evaluation
> evidence. Every data file carries `data_origin: SYNTHETIC_TOY_SIMULATOR` and every figure carries a red
> watermark saying so.

Prepared 2026-10-03. Status of the real project: M3 in progress (G6 open), so no real M4/M5 numbers exist yet.

---

## 1. Headline

- **The prototype agent works end to end.** Both SAC runs learn to recover 23 of 24 scheduled faults within
  18 ticks, from a starting point of 0–20 % right after the warm start.
- **It is not yet usable as a controller.** Both runs act in every NULL episode (FRR = 1.00) and take 7–8
  actions per episode, about 80 % of them wasted. PLAN.md's kill criterion (FRR > 10 %) would stop both runs.
- **The likely cause is structural, not a coding bug.** The action costs (0.02–0.04) are smaller than the
  critic's estimation error, and every healthy state is, from the agent's view, a lead-in to a fault that
  arrives 88 % of the time. This is the most useful thing the prototype tells you before you spend cluster days.
- **PER vs uniform: no difference** at this sample size (overall MTTR p = 0.88).

## 2. What is in the bundle

| Path | What it is | Origin |
|---|---|---|
| `prototype/contract.py` | every locked number from CLAUDE.md §5.1, action catalog, placeholder calibration | copied from contract |
| `prototype/core.py` | pure functions: observation (§5.3), reward and health (§5.6), mask (§5.8) | reusable on the cluster |
| `prototype/masked_sac.py` | masked discrete SAC, rules 1–5 and 9 of §5.12 | reusable as reference |
| `prototype/per_buffer.py` | PER / uniform buffer, n-step builder, rules 6–8 | reusable as reference |
| `prototype/runbook.py` | runbook rules R0–R6 (§5.11), observation-only | reusable |
| `prototype/toy_env.py` | **synthetic** fault simulator with the env interface | prototype only |
| `prototype/train.py`, `evaluate.py`, `collect_warmstart.py`, `make_schedule.py`, `analyze.py` | pipeline | — |
| `tests/test_math.py` | 6 unit tests on hand-written tensors (mask, NaN, α filter, soft V, PER, n-step, reward bounds) | — |
| `test_data/eval_schedule_v1.json` | **test data**: the 30-episode evaluation schedule, seed 2026, sha256 `ac886196cd35…` | real plan, reusable |
| `data/warmstart/` | warm-start dataset: 120 runbook episodes, ε = 0.25 (JSONL transitions + episode summary) | synthetic |
| `runs/sac_uniform_s0/`, `runs/sac_per_s0/` | **the two test runs**: per-episode training log CSV, all training transitions (JSONL), greedy probes, checkpoints | synthetic |
| `results/eval/` | evaluation transitions + per-episode results for all 5 policies on the schedule | synthetic |
| `results/summary.csv`, `results/stats.md`, `results/figures/` | metrics, statistics, the 7 PLAN.md figures | synthetic |

The mathematical formulation is in a separate document.

## 3. The prototype model

Masked discrete SAC with twin critics and PER, exactly as specified in CLAUDE.md §5.12 and with every
hyper-parameter from `contract.yaml` (γ = 0.93, n = 3, τ = 0.005, MLP [256, 256], lr 3e-4, batch 128,
buffer 50k, 4 updates per tick, α₀ = 0.1, target entropy 0.4·log|A_valid|, PER α = 0.5, β 0.4 → 1.0 over
15,000 gradient steps). Training follows PLAN.md M4: 120 warm-start episodes, 2,000 offline gradient steps,
300 online episodes, probes every 75 episodes, greedy evaluation of the final checkpoint.

Known prototype shortcuts, all to be fixed before M4:

1. Plain PyTorch, not Tianshou. CLAUDE.md §5.12 rule 11 says an in-house SAC needs human approval.
2. n-step transitions are inserted at episode end, not as soon as their window closes.
3. The toy simulator replaces `env/boutique_env.py`. Its dynamics are invented (see the math document, Section 8).
4. HPA is modelled against 70 % of the CPU **limit**. With upstream Online Boutique *requests* (frontend
   100m, if unchanged) a 70 % *Utilization* target would scale frontend at baseline. Decide which you mean
   before writing `k8s/hpa/hpa-baseline.yaml`.

## 4. Test runs — results (synthetic)

Fixed schedule: 6 episodes each of F1–F4 (targets and severities balanced) + 6 NULL, seed 2026, identical
for every policy. MTTR censored at 360 s.

| Policy | Faults recovered | Median MTTR (95 % CI) | F1 / F2 / F3 / F4 median MTTR | Σv per fault episode | Actions per fault episode | FRR |
|---|---|---|---|---|---|---|
| K8s default | 0 / 24 | 360 s (censored) | 360 / 360 / 360 / 360 | 11.00 | 0 | 0.00 |
| K8s + HPA | 6 / 24 | 360 s (censored) | 360 / 360 / 360 / **60** | 7.61 | 0 (HPA acts) | 0.00 |
| Runbook | **24 / 24** | 60 s [60, 60] | 80 / 60 / **40** / 70 | 1.59 | **1.25** | **0.00** |
| SAC uniform s0 | 23 / 24 | 50 s [40, 60] | 60 / 60 / 50 / **20** | 1.43 | 7.00 | 1.00 |
| SAC PER s0 | 23 / 24 | 60 s [40, 60] | **50** / 70 / 60 / 30 | 1.43 | 7.62 | 1.00 |

Source: `results/summary.csv`, `results/stats.md`. Statistical tests (Mann–Whitney U, n = 6 per fault):

| Comparison | Result |
|---|---|
| SAC PER vs runbook | PER faster on F1 (50 vs 80 s, p = 0.050) and F4 (30 vs 70 s, p = 0.032); runbook faster on F3 (40 vs 60 s, p = 0.11, n.s.); overall p = 0.10, a tie |
| SAC PER vs SAC uniform | no difference on any fault (overall p = 0.88) |
| SAC PER vs K8s + HPA | PER better on every fault type (p ≤ 0.02) |

Figures (`results/figures/`): fig1 MTTR box plots · fig2 Kaplan–Meier · fig3 recovery rate · fig4 Σv ·
fig5 learning curves · fig6 fault × action heat map vs the §5.2 outcome grid · fig7 FRR and wasted actions.

Training: greedy probes after the warm start recover 0 % (uniform) and 20 % (PER) of fault episodes, then
100 % from episode 75 on. α stayed in [0.090, 0.140] and policy entropy ended near 0.98 nats, so no α
divergence (PLAN.md kill criterion not triggered on that axis).

## 5. What the prototype reveals

**Finding 1 — the agent learns most of the cures.** In the heat map (fig6) the most frequent action is the
curative cell of the §5.2 grid for F1 (RESTORE pc), F3 cart (SCALE_UP cart), F3 cur (RESTORE cur) and F4
(SCALE_UP fe). Both F2 rows are dominated by no-call RESTORE cur, with the cure (RESTART of the target)
second; that is where the one unrecovered episode comes from. Learning plateaus after about 60–75 episodes (fig5).

**Finding 2 — it can beat the runbook's debounce.** The runbook waits for two unhealthy ticks (R2–R4), so its
best MTTR on F1, F2 and F4 is 60 s. The agent sometimes acts after one, which is where its 20–40 s F1 and F4
wins come from. The same eagerness is what makes it misfire on noise spikes.

**Finding 3 — it over-acts, and the reward does not stop it.** In NULL episodes the critic rates the greedy
action 0.32 (uniform) and 0.35 (PER) *above* NOOP on average, when the truth is the reverse by 0.02–0.04.
Two causes, both likely to carry over to the real cluster:

- *Cost below critic resolution.* A pointless no-call RESTORE costs 0.04 per tick. Returns contain p99
  spikes worth up to ~0.3, so the critic cannot resolve a 0.04 gap from about 3,200 online transitions.
- *Predictable faults.* The observation has no clock and 88 % of episodes inject a fault 2–5 ticks after
  reset. Pre-emptive scaling is therefore genuinely rewarded: across the two runs, all 7 F4 episodes
  "recovered" in 20 s had frontend scaled up at or before the injection tick, before any symptom was visible. That is a property of the episode design, not of
  good remediation, and FRR will count it.

**Finding 4 — warm start is not behaviour cloning.** 2,000 offline SAC steps on runbook data leave a near-random
greedy policy (0–20 % recovery). The warm start helps the critic, not the actor, so do not expect episode-0
probes to match the runbook.

## 6. Expected results on the real cluster (hypotheses for M4–M5)

These are predictions to test, reasoned from the contract, the runbook rules and the prototype. They are not results.

| Policy | Expected recovery (F1–F4) | Expected MTTR | Reasoning |
|---|---|---|---|
| K8s default | ~0 % | censored | No fault in the set self-heals: F1's template persists, StressChaos runs 30 min, nothing scales from 0, the surge persists |
| K8s + HPA | ~25 % (F4 only) | F4 60–120 s | HPA lag (metrics window + sync + pod start); it never acts at 0 replicas (pitfall 7); F2 scale-up is partial and fails the UID cure condition |
| Runbook | ≥ 95 % (gate G3) | F3 ≈ 40–60 s; F1, F2, F4 ≈ 60–100 s | R1 fires on the first tick for F3; the 2-tick debounce sets a 60 s floor elsewhere; .NET cartservice rollouts add a tick |
| SAC (uniform, PER) | ≥ 90 % | ties the runbook overall; possible 20 s gain on F1/F4 | Same action set and rollout physics, so the only lever is earlier detection |
| SAC FRR | **high unless mitigated** | — | Finding 3; the real cluster's p99 spikes (G6) make it worse |
| PER vs uniform | no significant MTTR difference at 6 episodes per fault | — | possible small sample-efficiency gain visible in learning curves only |

The honest headline to plan for: **the RL agent ties the runbook on recovery and MTTR, and the contribution
hinges on whether it can also match its FRR.** PLAN.md's risk register already anticipates the tie.

## 7. Recommendations before M4 (each needs a human decision)

1. **Decide the FRR mitigation now, not after 150 episodes.** Options: raise action costs (PLAN.md's first
   response, a `CONTRACT-CHANGE`); make fault timing less predictable (longer, variable lead-in or more NULL
   episodes, also a contract change); or add a behaviour-cloning term to the warm start (outside §5.12,
   needs approval). The prototype can screen these cheaply before cluster time is spent; it cannot validate them.
2. **Fix the HPA definition** (requests vs limits) before writing the baseline manifest.
3. **Decide whether pre-emptive actions in lead-in count as false remediation.** The metric definition must
   be fixed before M5 and must not be changed after seeing results.

## 8. Reproduce

```bash
pip install torch numpy scipy matplotlib pytest
python -m pytest -q tests
python -m prototype.make_schedule --out test_data/eval_schedule_v1.json
python -m prototype.collect_warmstart --out data/warmstart
python -m prototype.train --replay uniform --seed 0 --episodes 300 --warmstart data/warmstart --out runs/sac_uniform_s0
python -m prototype.train --replay per     --seed 0 --episodes 300 --warmstart data/warmstart --out runs/sac_per_s0
for p in noop:k8s_default hpa:k8s_hpa runbook:runbook; do
  python -m prototype.evaluate --policy ${p%%:*} --tag ${p##*:} --schedule test_data/eval_schedule_v1.json --out results/eval; done
for t in sac_uniform_s0 sac_per_s0; do
  python -m prototype.evaluate --policy checkpoint --ckpt runs/$t/final.pt --tag $t --schedule test_data/eval_schedule_v1.json --out results/eval; done
python -m prototype.analyze
```

Each training run takes about 4 minutes on 2 CPU cores. Seeds fix the agent and the simulator, so reruns
on the same machine reproduce these numbers; torch versions may shift them slightly.
