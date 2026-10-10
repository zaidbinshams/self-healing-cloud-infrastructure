# Plan of Action — Papers A & B (agreed 2026-10-10)

> Working plan for turning the capstone into publications. Technical rules stay in `CLAUDE.md`; milestones and gates in `PLAN.md`. **All paper prose is written by the authors** (no generative-AI text); Claude Code writes code, runs the cluster (asking first), and provides analysis, figures, outlines and related-work notes.

## 1. Goals and deliverables

| # | Deliverable | Purpose | Target |
|---|---|---|---|
| D1 | **Paper A**, complete draft with results, < 7 pages | **the paper for the guide (top priority)** | ~25 Oct 2026 |
| D2 | **Paper B**: measurement & reward-signal design | bonus paper; SEAMS 2027 short paper if ready | ~21 Oct 2026 (optional) |
| D3 | Capstone report material (all experiments) | final review, 2nd–3rd week of November | Nov 2026 |

**Rule:** if anything competes, Paper A wins. Paper B uses **no extra cluster time**.

## 2. Positioning of Paper A (after the literature check)

**Claim:** online, model-free deep RL that learns a **discrete remediation policy** (restart, scale, rollback-to-golden, incl. scale-from-zero) **entirely on a live cluster** (no simulator / digital twin), under **hard safety masks + a global in-flight lock**, a **20 s real-time loop** and **edge-network constraints**. Evaluated on **MTTR, false-remediation rate (FRR) and SLA penalty** against default Kubernetes, HPA and a per-fault runbook; runbook bootstrapping (AWARE-style) measured for remediation; robustness under degraded edge network.

**Do not claim:**
- first RL on a real cluster (FIRM, OSDI'20);
- first RL for remediation (E2E-REME, FSE'26; risk-constrained remediation, 2026);
- a novel warm-start (AWARE, ATC'23, bootstraps offline RL from HPA/VPA);
- a novel guarded action interface (ARBITER, 2026; risk-constrained CMDP, 2026).

| Paper | What it does | Our difference |
|---|---|---|
| FIRM (OSDI'20) | online DDPG, real 15-node cluster, continuous resource reallocation | discrete remediation incl. config rollback; edge; MTTR |
| AWARE (ATC'23) | offline RL bootstrapped from HPA/VPA, then online | same idea applied to remediation; cold-start ablation measures it |
| Risk-constrained remediation (arXiv 2607.20005) | offline CQL + Lagrangian (CMDP) from logs; 12 actions; FRR; runbook baseline | online, live, hard masks + lock, real-time/edge, MTTR |
| E2E-REME (FSE'26) | RL fine-tuned LLM writing Ansible playbooks; Online Boutique + Chaos Mesh | closed-loop control policy, ~0.6 s decisions, no LLM |
| ARBITER (arXiv 2607.19182) | guarded typed actions, deterministic/LLM planners, live 4-node | learned policy inside a guarded interface |
| GuardedAct (arXiv 2609.11264) | LLM + digital-twin sandbox | live system, learned control |
| AIOpsLab; TELKA; REACH | LLM-agent benchmark; RL in twin/simulator | no simulator |

**Implied changes:** report FRR prominently (risk-constrained framing); report decision latency/cost vs LLM approaches; related-work section as above.

## 3. Paper B (SEAMS 2027 short paper: 4 pages + 1 page refs, deadline ~21 Oct)

**Topic:** the Monitor stage of a self-healing loop. Where to measure and with which estimator so the adaptation/reward signal reflects what the controller can change.

**Content (existing data, no new cluster time):**
1. Three-way decomposition of SLA-latency noise: runtime pauses (.NET thread-pool starvation), edge network jitter (r = +0.95 with RTT), percentile sampling noise (p99 ≈ 0.145 vs p95 ≈ 0.09 CV at ~900 req/tick).
2. Diagnosis method: stall-event detection + probes aligned with a control service.
3. Guidelines, incl. a **tail-sample rule** n·(1−q), validated offline across the 33/40/50-user data.
4. **Impact on adaptation decisions:** false alarms / unnecessary remediations under client- vs server-side signal (runbook validation runs); fault detectability (smoke tests).
5. Artifact: scripts + anonymized data.

**Assessment:** a fair chance (competitive venue) *with* points 3–4; without them it reads as an experience report. If it misses SEAMS, it moves to the next fitting venue without touching Paper A.

## 4. Experiments (streamlined: only what is published)

| Experiment | Episodes | Status |
|---|---|---|
| Contract check | 3 | ✅ |
| Fault smoke tests | 8 | 4 ✅, 4 remaining |
| Runbook validation + gates G1–G7 | 40 | next |
| Warm-start collection (runbook, ε = 0.25) | 120 | |
| PER-SAC warm-started, seed 0 | 200 | |
| PER-SAC cold-start, seed 0 | 200 | |
| Evaluation: k8s, HPA, runbook, PER-SAC, PER-SAC cold × 50, interleaved | 250 | |
| Edge subset: runbook + PER-SAC × 20 under netem | 40 | |

**Total:** about 850 episodes, about 6 cluster days.

**Dropped:** uniform-replay ablation, compound faults, seeds 1–2, PPO, classifier baseline (stated as limitations).

**Cut order if late:** shrink the edge subset, then evaluation to 40 per policy.

## 5. Timeline

| Dates | Cluster | Code / analysis (Claude) | Writing (authors) |
|---|---|---|---|
| 10–11 Oct | smoke ×4, runbook validation, gates | server-side MTTR, pre-registration, netem tooling; Paper B analyses | Paper B from outline |
| 11–12 Oct | warm-start collection | SAC + PER + training (Tianshou) | Paper B draft |
| 12–16 Oct | PER-SAC → cold-start training | eval runner, metrics, stats, figures; related-work notes | Paper A intro/related/system/method; Paper B → guide |
| 16–20 Oct | evaluation + edge subset | results pipeline | Paper B → SEAMS (~21 Oct) if approved |
| 20–22 Oct | spare | final figures and tables | Paper A results |
| ~25 Oct | — | — | **Paper A → guide** |
| Nov | — | capstone report material | revise; submit A (ICPE ~16 Nov / CCGrid 1 Dec / later) |

## 6. Venues (verify dates on official pages before submitting)

| Venue | Format | Deadline | Use |
|---|---|---|---|
| SEAMS 2027 (ICSE, Dublin) | short 4+1 pp, IEEE/ACM proceedings | ~21 Oct 2026 | Paper B |
| ICPE 2027 | research up to 10 pp (ACM); Emerging track TBA | abstract ~9 Nov, paper ~16 Nov | Paper A (needs guide OK on length) |
| CCGrid 2027 | 10 pp (IEEE) | 1 Dec 2026 | Paper A fallback |

## 7. Risks

| Risk | Mitigation |
|---|---|
| Machine A busy / hotspot outages | advance notice; spare days; cut order |
| Gates expose a problem (e.g. 60 ms SLA too tight → FRR) | documented SLA adjustment **before** training only |
| SAC only ties the runbook | report honestly; safety, warm-start and edge results; LLM latency context |
| A close prior paper appears | positioning above; re-check before submission |
| Bugs in real runs | fix with tests; calibration stays valid (staleness scoped to measurement path) |

## 8. AI-assistance disclosure

ACM and IEEE require disclosure, in the Acknowledgments, of AI-generated content incl. **code**. Name the tool, the parts and how it was used. Here: experiment software, analysis/figure scripts, literature-search assistance. Prose is written by the authors. Check each venue's CFP and the university's rules.

## 9. Open decisions (owner: authors / guide)

1. Paper B to SEAMS?
2. Paper A venue / length (ICPE allows up to 10 pages).
3. How the university wants AI assistance declared.
