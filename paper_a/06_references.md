# 06 — References (what each does, where to cite, how we differ)

> BibTeX: `latex/refs.bib`. Each entry has a status note: **[web 25-Oct-2026]** = title, authors and venue checked online that day; **[paperB]** = checked 13–14 Oct for Paper B; **[canonical]** = standard reference, open the PDF/DOI once before submitting. Entries marked VERIFY need one field completed. **Open every paper you cite.** Fabricated or wrong references are a desk-reject reason at most venues.

## Closest work: remediation

| Key | What it does | How we differ | Cite in |
|---|---|---|---|
| `dai2026saferemediation` (arXiv 2607.20005, Jul 2026) | Remediation as a constrained MDP learned **offline from incident logs**; risk decomposition (blast radius, reversibility, uncertainty); human-in-the-loop gate; runbook baseline; Train Ticket + Chaos Mesh; bounded false-remediation rate | We learn **online on the live system**; hard masks + lock instead of a learned constraint; MTTR against Kubernetes/HPA/runbook | I, II |
| `zhang2026e2ereme` (FSE'26 Industry, arXiv 2604.11094) | LLM (Qwen3-8B) fine-tuned with RL in simulated experience to write Ansible remediation playbooks; builds on the MicroRemed benchmark | We learn a closed-loop control policy with 12 discrete actions; decisions in 0.33 s median; no LLM | I, II |
| `habibi2026arbiter` (arXiv 2607.19182, Jul 2026) | Guarded agentic control for SLO-oriented Kubernetes remediation; deterministic or LLM planners pick typed actions; 4-node cluster; beats autoscaling on rollbacks/downstream fixes | We put a **learned** policy inside a guarded interface; similar finding that autoscaling alone misses non-load faults (our HPA 10/40) | II, VII |
| `cai2026guardedact` (arXiv 2609.11264, Sep 2026) | AI-suggested fixes tested in a sandboxed copy (digital twin) of the system; low-risk fixes auto-applied; 87.4 % of faults recovered | No sandbox or twin: learning and evaluation on the live cluster, made safe by masks, lock and least privilege | II |

## RL for cloud control

| Key | What it does | How we differ | Cite in |
|---|---|---|---|
| `qiu2020firm` (OSDI'20) | RL for fine-grained resource management of SLO-oriented microservices; anomaly injection | Continuous resource knobs vs our discrete repair actions; we do not claim to be the first RL on a real cluster | I, II |
| `zhang2021sinan` (ASPLOS'21) | ML-based QoS-aware resource management for microservices | Same distinction | II |
| `qiu2023aware` (USENIX ATC'23) | RL autoscaling in production, bootstrapped from existing autoscalers (HPA/VPA), meta-learning for new workloads | **Closest to our warm start:** bootstrapping RL from an existing controller; we apply it to remediation with a runbook and measure it with a cold-start ablation. Do not claim the warm-start idea as new | II, IV |

## RL method

| Key | Use |
|---|---|
| `haarnoja2018sac` | SAC; fixed-temperature SAC is the first version of the method (supports α fixed at 0.1) |
| `christodoulou2019sacd` | discrete-action SAC (soft value as an expectation over actions) |
| `schaul2016per` | prioritized replay, IS weights, β annealing |
| `kumar2020cql` | CQL penalty (logsumexp − data action), used offline and online |
| `huang2022masking` | invalid-action masking with −∞ logits is a valid policy gradient |
| `hester2018dqfd`, `nair2020awac` | learning from demonstrations / offline-to-online RL (context for the warm start) |
| `garcia2015saferl` | safe RL survey (context for hard masks as a safety mechanism) |

## Systems, tools and statistics

| Key | Use |
|---|---|
| `kephart2003vision` | self-healing as part of autonomic computing (Introduction, one clause) |
| `gan2019deathstarbench` | other microservice benchmarks (when saying "one application") |
| `onlineboutique`, `k3s`, `chaosmesh`, `locust`, `prometheus`, `k8shpa`, `hemminger2005netem` | software, at first mention |
| `mann1947test`, `holm1979simple`, `cliff1993dominance`, `efron1993bootstrap`, `kaplan1958nonparametric` | statistics in Section V |

## Not in the bib (decide as a team)
- **Our own Paper B** (server-side vs client-side SLA latency on the same testbed): cite it as the source of the measurement choice once it has a citable form; until then, describe it in one sentence without a citation, or as "under submission" if the venue allows (anonymize).
- MicroRemed (arXiv 2511.01166), the benchmark E2E-REME builds on: add it only if you discuss LLM remediation benchmarks.
