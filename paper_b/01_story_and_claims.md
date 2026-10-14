# 01 — Story, Research Questions, Claims, Abstract Content Points

> Facts and structure only; write the text yourselves.

## Why we are writing this paper

- **Where it comes from:** our capstone ("Self-Healing Cloud Infrastructure") built a real testbed in which a controller (a scripted SRE runbook, and later an RL agent) detects and repairs faults in a microservice application. To make that work, we had to find out *what latency signal the controller should watch*. Doing so took a week of measurement and diagnosis, and produced findings that stand on their own. That is Paper B.
- **Why it matters to the field:** self-adaptive and self-healing systems follow a Monitor–Analyze–Plan–Execute loop. Papers usually focus on Analyze/Plan (the policy) and take the Monitor input (e.g. "p99 latency") for granted. We show that the Monitor design alone (measurement point, quantile, window, transport) changes what the controller can detect, before any policy is involved.
- **Relation to Paper A** (the RL remediation paper): separate contribution, separate venue. Paper B is about the *signal*, Paper A about the *policy*. Do **not** describe Paper A's agent or results here, and do not cite it (double-anonymous, unpublished).

## The gap (one paragraph of facts)

- Controllers in the literature (e.g. FIRM, Sinan) use end-to-end tail latency as their objective, measured inside the datacenter.
- Edge deployments put the controller and load sources across lossy wireless links, so a client-side measurement includes the network.
- Measurement papers (Treadmill, Lancet, Tales of the Tail) study how to measure tail latency for *benchmarking*, not how a *controller's* per-tick signal behaves or what it does to adaptation decisions.
- Missing: an empirical account of the noise sources in a control-tick latency signal, how to size the quantile window, and the decision-level consequence of the measurement point.

## Research questions

- **RQ1 (sources):** What makes a per-tick SLA latency signal noisy in an edge-deployed self-healing loop, and can the sources be separated?
- **RQ2 (estimator):** How much of the tick-to-tick variability is sampling noise of the quantile estimator, and how should the quantile and window be chosen?
- **RQ3 (decisions):** How does the measurement point change what the controller can detect and how often it would act on false alarms?

## Contributions (content for the bulleted list at the end of the Introduction)

1. **A three-way decomposition** of SLA-signal noise on a real Kubernetes testbed (runtime stalls, wireless network, estimator sampling), with evidence for each source (Fig. b1, b2; Table II).
2. **A diagnosis method:** tick-aligned client/server/network measurement plus sub-second stall events, cross-checked against ping RTT and CPU throttling, which separates container-level stalls from host and network effects.
3. **A tail-sample rule** for sizing the control-tick quantile: sampling CV falls roughly as k^−0.4 in the expected number of tail samples k = n(1−q); about 8 tail samples per window give ~10 % sampling CV (Fig. b3).
4. **Decision-level evidence:** with the same calibration rule, the client-side threshold inflates to 3–6× its median, hiding latency-only faults. All four fault smokes are detected server-side (1.01–2.83× threshold) but none client-side (0.27–0.71×). In 140 recorded runbook episodes, F2/F4 detection drops from 27/29 and 29/29 (server) to 3/29 and 5/29 (client) (Fig. b4, Table IV).
5. **Practical guidelines** for the Monitor stage, each backed by a result (§5 of the paper).
6. **An artifact** (scripts and recorded data), anonymized for review.

## Claims we CAN make (all backed by data)

- On our testbed, after a runtime fix, server-side tail latency is stable (CV 0.14–0.19), while client-side tail latency stays volatile (CV 0.35–0.60) and tracks wireless RTT (r = 0.95 / 0.87).
- Before the runtime fix, in-cluster stalls dominated both signals (hundreds of > 500 ms requests per 10 min; 7 stall events/min; none host-wide).
- Sampling noise of the tick quantile follows a power law in tail samples; we give the numbers.
- The same calibration rule produces a 3–6× looser client-side threshold, which hides latency-only faults; we quantify detection per fault type.
- Keeping monitoring connections alive removed Prometheus connect timeouts on the lossy link (2.96 → 0 per 100 ticks), a small but clean effect.

## Claims we must NOT make

- No generality beyond the setting ("in all edge systems…"). State it as one application, one cluster, one wireless link, and present the findings as evidence plus guidelines.
- Not "first to study tail-latency measurement" (Treadmill, Lancet, Tales of the Tail exist).
- Nothing about the RL agent's performance (that is Paper A).
- That the client side *never* detects faults: F1 (large latency) and F3 (errors) are detected by both.
- That sampling noise is the main source of tick-to-tick variance: it is minor for p95 (~2 % of variance at 20 s windows) and larger for p99 (~11 %). The rule is about controlling it, not about it dominating.

## Title ingredients (write your own title)

A good title names: (1) the **Monitor/measurement** design question, (2) **self-healing / self-adaptive microservices**, and (3) the **edge or wireless** setting; optionally (4) **tail latency** or "SLA signal". Keep it under ~15 words. Avoid hype words; a question-style or "where to measure" framing fits SEAMS.

## Abstract content points (150–200 words; write it LAST)

In this order, one or two sentences each:
1. **Context:** self-healing controllers act on an SLA latency signal (tail latency per control tick).
2. **Problem:** in edge deployments the controller measures across a lossy wireless link; how the signal is measured (point, quantile, window, transport) is usually taken for granted.
3. **Approach:** a real Kubernetes testbed (Online Boutique, 20 s control tick, controller across a Wi-Fi hotspot); client-side vs server-side vs network measurement, stall diagnosis, bootstrap analysis of quantile estimators, recorded fault and runbook episodes.
4. **Findings (pick 3 numbers):**
   - client p99 tracks network RTT (r = 0.95), while server-side tail latency is stable (CV 0.14);
   - ~8 expected tail samples per window keep sampling CV near 10 %;
   - with the same calibration rule, the client threshold inflates 3–6× and misses most latency-only faults (F2: 3/29 vs 27/29; F4: 5/29 vs 29/29 in the first two ticks).
5. **Takeaway:** measure the SLA signal at the server, size the quantile window by tail samples, keep monitoring connections alive; guidelines and an artifact are provided.

## Keywords (already in the skeleton; adjust if you like)

self-adaptive systems, self-healing, monitoring, tail latency, microservices, edge computing
