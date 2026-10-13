# Paper B — Venue, Outline, Content Points, Related-Work Notes

> Structure and facts only. **The authors write every sentence of the paper** (titles and abstract included). Numbers: `docs/paperB_results.md` and `figures/paperB/analysis.json`. Verify any number against those before using it.

## 1. Venue: SEAMS 2027 research track, short paper (verified 13 Oct 2026 on the official page)

| Item | Official |
|---|---|
| Deadline | **23 October 2026, AoE (UTC−12)**; single round; HotCRP: seams27.hotcrp.com |
| Short paper | **6 pages of content + 1 page of references**, for "ongoing research, new research ideas, or preliminary results without a mature evaluation" |
| Format | **IEEE** conference template (IEEEtran, `conference`, 10 pt, no compsoc) |
| Review | **double-anonymous**: no author names; own prior work in the third person; no identifying links (use an anonymized artifact link, e.g. anonymous.4open.science) |
| AI policy | IEEE: AI-generated text must be disclosed in the Acknowledgments; editing-only use needs no disclosure under ACM guidance; **papers with fabricated or hallucinated references are desk-rejected** |
| Later dates | notification 3 Dec 2026; revisions due 5 Jan 2027; camera-ready 29 Jan 2027; conference 26–27 Apr 2027, Dublin (with ICSE) |
| Relevant topics | self-healing; runtime models and feedback-loop engineering; MAPE-K; ML/RL for adaptation; cloud/fog/edge; uncertainty and runtime decision-making |

Source: https://conf.researchr.org/track/seams-2027/seams-2027-research-track. Re-check a week before submission.

**Consequence:** we have 6 content pages, not 4. Paper A must not be cited (double-anonymous, and it is unpublished); describe the testbed self-sufficiently.

## 2. The paper in one line (for the authors to phrase)

*Where and how a self-healing controller measures its SLA signal decides what it can see:* in an edge deployment, a client-side tail-latency signal mixes in network noise, forces a loose threshold, and hides latency-only faults; a server-side p95 with enough tail samples does not.

**Title should convey:** monitor/measurement design, self-healing (or self-adaptive) microservices, edge/wireless controller, tail latency or SLA signal.

## 3. Research questions (suggested)

- **RQ1:** What makes a tick-level SLA latency signal noisy in an edge-deployed self-healing loop, and can the sources be separated? → R1 (decomposition, stall diagnosis)
- **RQ2:** How much of the tick-to-tick variability is estimator sampling noise, and how should the quantile and window be chosen? → R2 (tail-sample rule)
- **RQ3:** How does the measurement point change adaptation decisions? → R3 (threshold inflation, detectability), R6 (runbook view), R4 (transport)

## 4. Section plan (6 pages IEEE two-column; 4 figures, 2–3 tables)

| § | Section | Budget | Content points (facts to cover; authors write) | Figures/tables |
|---|---|---|---|---|
| 1 | Introduction | 0.75 p | the Monitor stage of MAPE-K is rarely examined as a design choice; RL/runbook controllers use tail latency as SLA/reward; edge setting: the controller sits across a wireless link; RQ1–3; contributions as a list: (i) three-way noise decomposition on a real testbed, (ii) diagnosis method, (iii) tail-sample rule, (iv) decision-level evidence, (v) artifact | — |
| 2 | Setting | 0.75 p | Online Boutique on single-node K3s; controller + load generator on a separate machine over a hotspot; 20 s control tick; three measurement points (client p99 from the load generator, server p99/p95 from frontend request logs, ping RTT); calibration rule L = ceil_10ms(1.5·q95), G6 gate; Locust closed-loop users, U_base | small table of the three signals |
| 3 | Method | 0.75 p | tick-aligned collection of all three signals; stall-event detection (slow requests grouped, cross-checked with ping RTT and cAdvisor throttle counters); per-window bootstrap of nearest-rank quantiles on a raw request capture; decision view: same calibration rule per signal, held-out false-breach rate, 2-tick detection on recorded fault episodes | — |
| 4.1 | RQ1 results | 0.8 p | before the runtime fix both signals are dominated by in-cluster stalls (CV 0.31–0.73, ~600–1,100 slow requests per 10 min, 7 stall events/min, none host-wide); after it, server CV 0.14–0.19, client CV 0.35–0.60 and r = 0.95/0.87 with ping | Fig b1, Fig b2 |
| 4.2 | RQ2 results | 0.6 p | sampling CV vs k = n(1−q), fit 0.24·k^−0.41; ~8 tail samples per window for 10 % CV; at 20 s ticks p95 0.058 vs p99 0.097; sampling is a minor share of the tick-to-tick variance at p95 and larger at p99; caveat: the mid-run disturbance | Fig b3 (+ optional small table) |
| 4.3 | RQ3 results | 1.0 p | same rule → client threshold 3–6× its median vs 2.4–2.9× server; all four fault smokes visible server-side (1.01–2.83×), invisible client-side (0.27–0.71×); runbook view: F2 27/29 vs 3/29, F4 29/29 vs 5/29, F1/F3 equal; fault-free over-threshold 1.4 % vs 4.0 %; transport: per-query connections 2.96 ConnectTimeouts/100 ticks → 0/90 with keep-alive | Fig b4, table (R6) |
| 5 | Guidelines | 0.45 p | numbered, each backed by a result: measure at the server when the controller is remote; calibrate thresholds per signal, then check detectability with oracle faults; choose quantile and window so that n(1−q) ≳ 8; separate runtime stalls before tuning thresholds; keep monitoring connections alive on lossy links; verify the load model spreads load after a scale-out (testbed validity) | — |
| 6 | Threats | 0.25 p | one application, one node, one wireless link; closed-loop load generator; short capture with a disturbance; F2 w1 marginal; the cluster was retuned during the study (all changes documented and fixed before the reported runs) | — |
| 7 | Related work | 0.45 p | see §5 below | — |
| 8 | Conclusion | 0.2 p | answer RQ1–3 in one sentence each; artifact | — |
| — | Acknowledgments | — | AI-assistance disclosure (software, analysis, figures, literature search); the prose is by the authors | — |

## 5. Related-work notes (every entry verified to exist on 13 Oct 2026)

| Group | Reference (verified) | What it does | Our difference |
|---|---|---|---|
| Tail latency | J. Dean, L. A. Barroso, "The Tail at Scale," *Commun. ACM* 56(2):74–80, 2013 | why tail latency dominates user experience at scale | we study *measuring* the tail as a control signal |
| | J. Li, N. K. Sharma, D. R. K. Ports, S. D. Gribble, "Tales of the Tail: Hardware, OS, and Application-level Sources of Tail Latency," *SoCC* 2014 | decomposes the sources of tail latency on single servers (background interference, scheduling, interrupts, power saving, NUMA) | our decomposition is at the microservice/controller level: runtime stalls vs network vs estimator noise, and its effect on adaptation decisions |
| Measurement methodology | Y. Zhang, D. Meisner, J. Mars, L. Tang, "Treadmill: Attributing the Source of Tail Latency through Precise Load Testing and Statistical Inference," *ISCA* 2016, doi:10.1109/ISCA.2016.47 | load testers with closed-loop behaviour bias tail estimates; statistically sound aggregation | we quantify estimator noise per control tick and derive a tail-sample rule for controllers |
| | M. Kogias, S. Mallon, E. Bugnion, "Lancet: A self-correcting Latency Measuring Tool," *USENIX ATC* 2019, pp. 881–896 | online statistical tests flag unreliable tail measurements | our concern is the controller's signal under a wireless link, not µs-scale benchmarking |
| | B. Schroeder, A. Wierman, M. Harchol-Balter, "Open Versus Closed: A Cautionary Tale," *NSDI* 2006, pp. 239–252 | open vs closed workload generators behave very differently | our Locust users are closed-loop; we also show a connection-level effect (keep-alive pinning) on scale-out |
| SLO-driven control | H. Qiu, S. S. Banerjee, S. Jha, Z. T. Kalbarczyk, R. K. Iyer, "FIRM: An Intelligent Fine-grained Resource Management Framework for SLO-Oriented Microservices," *OSDI* 2020, pp. 805–825 | ML/RL mitigation of SLO violations using tail latency | assumes a clean in-datacenter latency signal; we show where the signal breaks at the edge |
| | Y. Zhang, W. Hua, Z. Zhou, G. E. Suh, C. Delimitrou, "Sinan: ML-Based and QoS-Aware Resource Management for Cloud Microservices," *ASPLOS* 2021, doi:10.1145/3445814.3446693 | ML resource manager targeting end-to-end tail latency | same: we examine the Monitor input, not the planner |
| Self-adaptive systems | J. O. Kephart, D. M. Chess, "The Vision of Autonomic Computing," *IEEE Computer* 36(1):41–50, 2003 | autonomic/self-managing systems vision | grounding for the monitor-analyze-plan-execute framing (confirm the MAPE-K attribution before claiming it is from this paper) |
| | D. Garlan, S.-W. Cheng, A.-C. Huang, B. Schmerl, P. Steenkiste, "Rainbow: Architecture-Based Self-Adaptation with Reusable Infrastructure," *IEEE Computer* 37(10):46–54, 2004 | reusable monitoring/adaptation infrastructure (probes, gauges) | we evaluate a probe/gauge design choice empirically |
| | G. A. Moreno, B. Schmerl, D. Garlan, "SWIM: An Exemplar for Evaluation and Comparison of Self-Adaptation Approaches for Web Applications," *SEAMS* 2018 | simulated web-application exemplar (response-time based) | real cluster and real network; the measurement artifacts a simulator cannot show |
| | N. Esfahani, S. Malek, "Uncertainty in Self-Adaptive Software Systems" | sources of uncertainty in self-adaptive systems | **venue/year NOT verified** (believed: SEfSAS II, LNCS 7475, 2013); verify via DOI before citing, or drop |
| Grey literature (footnote, not a reference, if used) | W. Morgan, "gRPC Load Balancing on Kubernetes without Tears," Kubernetes blog, Nov 2018 | HTTP/2 long-lived connections defeat connection-level load balancing | supports the connection-pinning observation |

**Rule for the authors:** cite only what you have opened yourselves and confirmed (title, authors, venue, year, pages/DOI). A single wrong reference can cause a desk reject at SEAMS 2027.

## 6. Working order for the team (suggested)

1. Agree RQs and the section plan (this file), then decide with the guide whether to submit (deadline 23 Oct, AoE).
2. Draft §2–4 from `docs/paperB_results.md` (numbers) and the figures in `figures/paperB/`.
3. Draft §1, 5, 6, 7, 8.
4. I fact-check every number, figure reference and citation against the data and sources, then the authors revise.
5. Anonymize (artifact link, no names), add the AI-assistance statement, and submit via HotCRP.
