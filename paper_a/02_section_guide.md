# 02 — Section Guide (flow and content points; ≤ 7 pages in IEEE two-column)

> For every section: purpose, page budget, paragraph-level points in order, which figure/table, which numbers (IDs from `05_numbers.md`). Points are not prose. **Write your own sentences.**

Page budget (7 pages including references): I 0.8 · II 0.5 · III 0.8 · IV 1.3 · V 0.7 · VI 2.0 · VII 0.6 · VIII 0.2 · refs 0.5 (~25 entries; drop the uncited ones when `\nocite{*}` is removed). If over budget, move Fig. 6 (seed 1, `a5_seed1.pdf`) out first (Table VI carries the replication), then shorten Section II.

---

## Abstract (write last; 150–200 words)
Points are in `01_story_and_claims.md`. Include 4 numbers at most: 35/40, 0/40 & 10/40, CI or "non-inferior", 0/10.

## I. Introduction (≈ 0.8 page)
1. **Context.** Microservice faults; MTTR as the operator's metric; runbooks are hand-written per fault class and must be maintained.
2. **Why RL, and what is missing.** RL has been applied to resource management (FIRM, Sinan), autoscaling (AWARE) and remediation (offline/CMDP from logs: Dai et al.; LLM playbooks: E2E-REME; guarded planners: ARBITER, GuardedAct). What is missing: a *learned discrete remediation policy trained online on the live system*, behind hard guards, under edge constraints, compared to the tools operators actually use, with a pre-registered protocol.
3. **Why it is hard.** A real-time loop (20 s ticks), noisy telemetry (missing metrics, pod churn), unsafe exploration on a live system, and a remote controller over a lossy wireless link.
4. **What we do.** One-sentence summary of the testbed and agent (refer to Fig. 1).
5. **Contributions** (numbered list): the four items in `01_story_and_claims.md`, each with its headline number.
6. Optional: the paper's organisation, in one sentence (skip if space is short).

## II. Related Work (≈ 0.5 page)
`06_references.md` lists each paper with what it does and how we differ.
1. **RL for cloud control:** FIRM, Sinan, AWARE. These control resource knobs; AWARE bootstraps RL from autoscalers (closest to our warm start; say so).
2. **Automated remediation:** Dai et al. (offline, CMDP, FRR, runbook baseline, Train Ticket), E2E-REME (LLM fine-tuned with RL, Ansible playbooks), ARBITER (guarded agentic control on Kubernetes), GuardedAct (sandboxed AI fixes). Our position: online learned control on the live system, guards as hard masks plus a lock, measured MTTR/FRR against Kubernetes, HPA and a runbook.
3. **Method roots:** SAC and discrete SAC; PER; CQL; invalid-action masking; learning from demonstrations (DQfD, AWAC); safe RL survey. One sentence each, at most.

## III. Testbed and Control Loop (≈ 0.8 page; Fig. 1, Table I)
1. **Machines and link.** A = single-node K3s (16 CPU laptop); B = controller (env, agent, Locust, clock authority); wireless link; documented overrides (RTT, memory).
2. **Application and managed services.** Online Boutique, 11 deployments; the agent observes and acts on 4 (frontend, cart, currency, productcatalog). Documented cluster tuning (limits, housekeeping): one sentence with a pointer to the artefact.
3. **Telemetry.** Server-side p95 latency from frontend request logs (why server-side: one sentence, or cite the companion measurement study if published), failure ratio and RPS from Locust, cAdvisor CPU/throttle/memory via Prometheus (5 s scrape, 30 s rate window), Kubernetes deployment and pod status.
4. **Faults.** Table I: injection, cure condition, curing action. One sentence on why these four (bad config, hot pod, lost replicas, load surge).
5. **Loop and guards.** 20 s tick, 3 s collection deadline, asynchronous dispatch, one action in flight, least-privilege credentials (agent cannot delete pods or touch other namespaces), replica bounds enforced twice.

## IV. Remediation as a Masked MDP (≈ 1.3 pages; Eqs. 1–12, Algorithm 1)
1. **Observation** (Eq. 1 for latency; describe the other 35 features in one sentence each by group). No ground truth.
2. **Actions and mask** (Eq. 2). Lock rule. RESTORE is never masked by drift (would leak the fault).
3. **Reward** (Eqs. 3–5): SLA violation + action cost + replica cost; no MTTR shaping. **Health and recovery**, MTTR (Eq. 6).
4. **Agent** (Eqs. 7–10): masked softmax, soft value over valid actions, n-step target, actor loss; zeroing masked log-probs (NaN pitfall).
5. **Conservative critic and PER** (Eqs. 11–12). Say *why*:
   - plain offline SAC made the agent value rare actions in healthy states (Amendment 5);
   - plain online SAC drifted to acting in healthy states (Amendment 6).
   So: BC actor + CQL critic offline; CQL kept online; α fixed at 0.1. Cite CQL and SAC.
6. **Warm start.** 120 runbook episodes with ε = 0.25, 2000 offline steps (~9 s). Then 200 online episodes, 4 gradient steps per tick. A stale next state is never stored.
7. Algorithm 1: one tick (refer to it, don't re-describe it).

## V. Experimental Design (≈ 0.7 page; Table VII hypotheses can sit here or in VI)
1. **Baselines:**
   - Kubernetes default (its own controllers only);
   - Kubernetes + HPA (CPU AverageValue 60 % of limit; min = base, max = agent bounds);
   - scripted runbook (7 ordered rules, same observation, no ground truth).
2. **Pre-registration.** Committed before training; 8 dated amendments (table in `04_methodology_facts.md`); give one clause each for A5/A6 (method), A7 (cold length), A8 (seed 1).
3. **Schedule and protocol:**
   - 50 entries (10 per fault type + 10 NULL, seed 2026), balanced targets and severities;
   - policies interleaved per entry;
   - invalid episodes re-run on the same entry;
   - session start checks (AC power, steady check).
4. **Edge subset.** First 4 entries per type (20); netem +150 ms and 1 % loss on control-plane traffic only (Kubernetes API and Prometheus; Locust untouched); dry-run validity rule.
5. **Seed-1 replication.** Same schedule; runbook as same-block anchor.
6. **Metrics and statistics:**
   - MTTR censored at 360 s; recovery; SLA penalty; FRR; safety violations; decision latency;
   - Mann–Whitney + Holm per family, Cliff's δ, 10 000-resample bootstrap CIs, Kaplan–Meier + log-rank;
   - non-inferiority margin 20 s (one tick).

## VI. Results (≈ 2 pages; Tables II–VII, Figs. 2–5 (6))
Order and what to say (`03_figures_tables.md` has the details):
1. **Overall recovery and MTTR** (Table II, Fig. 2): 35/40 vs 0/40 and 10/40 [N1–N3]; log-rank [N19b]. H1 per fault [N8]. Then the honest F4 point: HPA is faster on F4 [N9].
2. **Against the runbook** (Table II, Fig. 3): pooled non-inferiority [N10]; per fault faster on F1/F4, slower on F3, F2 split by target [N5, N7]. SLA penalty tie [N11].
3. **What the agent learned** (Table IV): its actions match the runbook's cure per fault; F2-curr never acted on. Cold agent: repeated RESTORE cartservice.
4. **Warm vs cold** (Fig. 4, H3): return [N18], evaluation MTTR [N19], cold FRR [N16].
5. **Safety** (H4): 0/10, 0/30, 0 violations [N14, N15, N17]; decision latency [N22].
6. **Edge** (Fig. 5, H5): medians and recoveries [N20, N21]; latency rise [N23]; H5 not established, and why (n = 16).
7. **Seed 1** (Table VI, optional Fig. 6): 35/40, H2 holds within block [N24, N25]; anchor shows no drift [N26]; same blind spot.
8. **Summary** (Table VII): hypothesis → outcome.

## VII. Discussion and Limitations (≈ 0.6 page)
Use "What the results mean" and "Limitations" in `01_story_and_claims.md`. Suggested order:
1. Why F2-curr is never learned (no exploration under fixed α + CQL): the trade-off between safety in healthy states and exploration.
2. HPA on F4: a reactive autoscaler beats a debounce-based policy on the one fault it sees. Possible combination: HPA + agent.
3. The warm start is necessary.
4. Edge.
5. Threats: internal (measurement gaps, near-SLA F2, amendments), external (one node, one app, two seeds), construct (MTTR at 20 s resolution).

## VIII. Conclusion (≈ 0.2 page)
What was shown (two numbers); next steps: exploration that keeps NULL safety (e.g. learned α with a floor), multi-node, more seeds and applications, combining with HPA.

## Acknowledgment / AI disclosure
See `07_format_and_submission.md`.
