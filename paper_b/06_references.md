# 06 — References and Related Work

> Every reference below was **checked to exist** (title, authors, venue, year) on 13–14 Oct 2026. BibTeX keys are in `latex/refs.bib`. **Venues desk-reject papers with fabricated or wrong references**, so before citing, each author opens the paper (or its DOI/publisher page) and confirms the entry, completing any field marked `VERIFY` in `refs.bib`. Cite only what you have opened. Do not add references from memory.

## How to use this file

- For **Related Work**, write three short paragraphs (groups A–C below). For each, say what the work does and how ours differs (the "difference" column gives the facts).
- Other citations go where the "Cite in" column says.
- Software (group E) is cited where you first name the tool; if space is tight, give the version in the text and cite only Online Boutique.
- Target about **15–20 references** (they fill the 7th page).

## A. Tail latency: sources and measurement

| Key | Reference | What it does (verified summary) | Difference from us | Cite in |
|---|---|---|---|---|
| `dean2013tail` | J. Dean, L. A. Barroso, "The Tail at Scale," *Commun. ACM* 56(2):74–80, 2013 | why the tail of the latency distribution dominates user-perceived performance in large services; techniques to tolerate tail variability | we treat the tail quantile as a **control signal** and ask how to measure it | §1 P1, §7 |
| `li2014tales` | J. Li, N. K. Sharma, D. R. K. Ports, S. D. Gribble, "Tales of the Tail: Hardware, OS, and Application-level Sources of Tail Latency," *SoCC* 2014 | measures single-server tail latency against a queueing model; identifies background interference, scheduling and concurrency models, interrupt routing, CPU power saving and NUMA as sources | our sources are at the **microservice/controller** level (runtime stalls, wireless link, estimator) and we measure their effect on **adaptation decisions** | §1 P2, §7 |
| `zhang2016treadmill` | Y. Zhang, D. Meisner, J. Mars, L. Tang, "Treadmill," *ISCA* 2016, pp. 456–468 | load testers that behave as closed loops underestimate tail latency; statistically sound aggregation; quantile regression to attribute tail effects | benchmarking methodology; we address the **per-tick signal of a controller** and give a tail-sample rule for its window | §1 P2, §3 (statistics), §7 |
| `kogias2019lancet` | M. Kogias, S. Mallon, E. Bugnion, "Lancet," *USENIX ATC* 2019, pp. 881–896 | open-loop µs-scale latency measurement with online statistical tests that flag unreliable tail estimates | datacenter µs benchmarking; we work at a 20 s control tick across a **wireless** link | §7 |
| `schroeder2006open` | B. Schroeder, A. Wierman, M. Harchol-Balter, "Open Versus Closed: A Cautionary Tale," *NSDI* 2006, pp. 239–252 | open vs closed workload generators behave very differently; eight guiding principles | our load generator is closed-loop (a threat to validity); we add a **connection-level** effect (keep-alive pinning defeats scale-out) | §6, §7 |

## B. SLO-driven control of microservices (where such signals are used)

| Key | Reference | What it does | Difference | Cite in |
|---|---|---|---|---|
| `qiu2020firm` | H. Qiu et al., "FIRM," *OSDI* 2020, pp. 805–825 | ML localization of SLO-violating microservices and RL-based mitigation via resource reprovisioning; tail latency as the objective | assumes an in-datacenter latency signal; we show how the signal degrades when measured across an edge link and how to fix it | §1 P1, §7 |
| `zhang2021sinan` | Y. Zhang et al., "Sinan," *ASPLOS* 2021 | ML-based, QoS-aware resource manager keeping end-to-end tail latency within its target | same: we study the **Monitor input**, not the planner | §1 P1, §7 |
| `gan2019deathstarbench` | Y. Gan et al., "An Open-Source Benchmark Suite for Microservices and Their Hardware-Software Implications for Cloud & Edge Systems," *ASPLOS* 2019 | DeathStarBench microservice suite; tail latency studies | benchmark context; we use Online Boutique | §2 or §7 (optional) |
| `vonkistowski2018teastore` | J. von Kistowski et al., "TeaStore," *MASCOTS* 2018 | microservice reference application for benchmarking, modeling and resource-management research (incl. auto-scalers) | benchmark context | §7 (optional) |

## C. Self-adaptive systems and monitoring

| Key | Reference | What it does | Difference | Cite in |
|---|---|---|---|---|
| `kephart2003vision` | J. O. Kephart, D. M. Chess, "The Vision of Autonomic Computing," *IEEE Computer* 36(1):41–50, 2003 | the autonomic/self-managing systems vision | grounding for the self-healing loop (**do not** claim this paper defines "MAPE-K" unless you confirm it in the text) | §1 P1 |
| `garlan2004rainbow` | D. Garlan et al., "Rainbow," *IEEE Computer* 37(10):46–54, 2004 | architecture-based self-adaptation with reusable monitoring infrastructure (probes, gauges) and a closed control loop | we evaluate a **probe/gauge design choice** (measurement point, estimator) empirically | §7 |
| `moreno2018swim` | G. A. Moreno, B. Schmerl, D. Garlan, "SWIM," *SEAMS* 2018 | **simulated** web-application exemplar for comparing self-adaptation approaches (response-time based) | a real cluster and a real wireless link show measurement effects a simulator does not | §7 |
| `hezavehi2021uncertainty` | S. M. Hezavehi, D. Weyns, et al., "Uncertainty in Self-Adaptive Systems: A Research Community Perspective," *ACM TAAS* 15(4), 2021 | community survey on sources and management of uncertainty in self-adaptive systems | measurement noise in the Monitor input is one concrete uncertainty source; we quantify it | §1 P2, §7 |
| `weyns2020introduction` | D. Weyns, *An Introduction to Self-adaptive Systems: A Contemporary Software Engineering Perspective*, Wiley, 2020 | textbook on self-adaptation foundations (feedback loops, uncertainty) | background | §1 P1 |

## D. Statistics used in the Method

| Key | Reference | Use |
|---|---|---|
| `hyndman1996quantiles` | R. J. Hyndman, Y. Fan, "Sample Quantiles in Statistical Packages," *The American Statistician* 50(4):361–365, 1996 | defines the sample-quantile families; cite when you say "nearest-rank quantile" |
| `efron1993bootstrap` | B. Efron, R. J. Tibshirani, *An Introduction to the Bootstrap*, Chapman & Hall, 1993 | cite when you describe the bootstrap estimate of sampling CV |

## E. Software (cite as software; check URLs and versions)

`onlineboutique` (v0.10.7), `k3s` (v1.36.4+k3s1), `chaosmesh` (2.8.4: add the version in the text), `locust` (2.46.6), `prometheus` (v3.5.0: add the version in the text).

## F. Grey literature (footnote, not a reference)

`morgan2018grpclb`: Kubernetes blog (Nov 2018) explaining that HTTP/2 long-lived connections defeat connection-level load balancing. Use it only if you mention connection pinning, and prefer a footnote.

## Excluded / unverified (do NOT cite as is)

- N. Esfahani, S. Malek, "Uncertainty in Self-Adaptive Software Systems": the paper exists, but its venue/year were **not** verified. Cite only after confirming via DOI (believed: SEfSAS II, LNCS 7475, Springer 2013); otherwise use `hezavehi2021uncertainty`.
- "Kubow" (architecture-based self-adaptation for Kubernetes): found only as a Portuguese-language Brazilian workshop paper/thesis, not ECSA. Excluded.

## Optional extra reading (verify before citing; not in refs.bib)

If a reviewer-facing angle needs strengthening, look for:
- SEAMS papers on **monitoring/observability for self-adaptation** in recent proceedings (2022–2026);
- work on **coordinated omission** in load testing (mostly grey literature; prefer Treadmill as the peer-reviewed reference).

Add any new reference to `refs.bib` only after opening it.
