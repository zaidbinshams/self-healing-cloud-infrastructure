# 02 — Section-by-Section Guide

> For each section: purpose, length, the content to cover (as facts), figures/tables, numbers (IDs from `05_numbers.md`), pitfalls. **Write all text yourselves.** Page budget: 7 pages in total, IEEE two-column: about 6 pages of content (~4,500 words of text once figures and tables take their space) and references on the last page.

---

## Abstract (150–200 words) — write last
Content points: `01_story_and_claims.md`, "Abstract content points". Quote at most 3 numbers. No citations in the abstract.

---

## §1 Introduction (~0.75 page, 4–5 paragraphs)

**Purpose:** convince a reviewer in self-adaptive / self-healing systems that the Monitor stage's *measurement design* is a real problem for self-healing systems, and state RQs and contributions.

| Paragraph | Content to cover | Cite |
|---|---|---|
| P1 Context | self-adaptive/self-healing systems run a monitor–analyze–plan–execute loop; for latency-sensitive microservices the monitored signal is usually a tail-latency quantile per control period; controllers (rule-based or learned) compare it with an SLA threshold | `kephart2003vision`, `weyns2020introduction`, `qiu2020firm`, `zhang2021sinan`, `dean2013tail` |
| P2 Problem | at the edge the controller and load sources sit across a lossy wireless link; a client-side signal then contains network delay the controller cannot fix; tail quantiles over short ticks are also statistically noisy; runtime effects (e.g. thread-pool starvation) add stalls; the literature rarely examines this Monitor design | `li2014tales`, `zhang2016treadmill`, `hezavehi2021uncertainty` |
| P3 This paper | real testbed (Online Boutique on K3s, controller across a Wi-Fi hotspot, 20 s tick); measure the same requests at the client, at the server and on the network path; diagnose stalls; analyze quantile estimators on a raw request capture; evaluate on recorded fault and runbook episodes | — |
| P4 RQs | RQ1–RQ3 exactly as in `01_story_and_claims.md` | — |
| P5 Contributions | bulleted list (5–6 items) from `01_story_and_claims.md`; one headline number in the decisions bullet (e.g. N40/N41) | — |

**Pitfalls:** no RL details; no "first"; do not promise generality.

---

## §2 Setting (~0.75 page) — Fig. 1, Table I

**Purpose:** let a reader picture the system and the three signals in under a minute.

Content (facts in `04_methodology_facts.md` §1–§3):
1. **Application and cluster:** Online Boutique v0.10.7 (11 deployments) on single-node K3s (16 CPU, ~15 GB); fault-injection tooling (Chaos Mesh); Prometheus scraping cAdvisor every 5 s.
2. **Controller host and network:** a second machine runs the controller and the Locust load generator; it reaches the cluster over a Wi-Fi hotspot (wireless edge link). RTT is typically tens of ms, with spikes into hundreds of ms.
3. **Control loop:** 20 s ticks; each tick collects all signals within a 3 s deadline; the SLA signal is a latency quantile over the tick window.
4. **The three signals** (Table I): client p99 at the load generator; server p95/p99 from the frontend's own request logs; ping p99 on the same path. Same requests, same windows.
5. **SLA calibration rule:** threshold L = ceil to 10 ms of (1.5 × the 95th percentile of the steady-state tick values), with a stability gate (CV of steady ticks < 0.30 and < 2 % of ticks breaching). The final server-side calibration: L = 70 ms at 30 users (N60, N61).
6. **Load model:** closed-loop simulated users (wait 0.5–1.5 s); one TCP connection per request (realistic short-lived clients).

Fig. 1 is the testbed drawing (spec in `03_figures_tables.md`). Table I is already in the skeleton.

---

## §3 Method (~0.75 page)

**Purpose:** make each result reproducible and believable. One paragraph per analysis, with parameters. Facts: `04_methodology_facts.md` §4–§7.

1. **Tick-aligned measurement (RQ1):**
   - six 30-tick runs (10 min each) collecting, per 20 s tick, client p99, server p99 and ping p99 (100 pings at 0.2 s);
   - statistics: CV across ticks and Pearson r of each latency signal with ping p99.
2. **Stall diagnosis (RQ1):**
   - every frontend request is logged;
   - requests > 500 ms are grouped into stall events when they start within 1 s of each other;
   - each event is checked against ping RTT (a ping > 150 ms during the event would indicate a host-wide freeze) and against per-service CPU-throttling counters;
   - runs before and after the runtime fix (cartservice .NET thread-pool minimum).
3. **Estimator noise (RQ2):**
   - a 15.3-min raw capture of 37,933 server-side request latencies at steady state;
   - tiled into windows of 5–60 s; per window, the nearest-rank quantile (q = 0.5, 0.9, 0.95, 0.99);
   - sampling CV estimated by bootstrap (400 resamples per window);
   - fit of CV against expected tail samples k = n(1−q) over q ≥ 0.9 (log-log least squares). Cite `hyndman1996quantiles`, `efron1993bootstrap`.
4. **Decision view (RQ3):**
   - each signal gets its own threshold from the same calibration rule (first half of a steady run), and false breaches are counted on the held-out second half;
   - detectability: fault smokes at the final cluster state, peak before remedy ÷ threshold;
   - runbook view: 140 recorded runbook episodes (20 validation, 120 with 25 % random exploration); fault-free ticks before any action, and detection within the first two fault ticks (the runbook needs two unhealthy ticks before it may act).
5. **Transport:** Prometheus connect timeouts per tick, before vs after switching the monitor's HTTP client to a keep-alive session (retries disabled).

**Pitfalls:** say "recorded on the real testbed" (no simulation anywhere). Give parameters; avoid script names (anonymity is fine, but reviewers don't need file paths).

---

## §4 Results (~2.4 pages)

### 4.1 RQ1 — noise sources (~0.8 page) — Fig. b1 (full width), Fig. b2, Table II
Cover, in this order:
1. **Before the runtime fix:**
   - both signals unstable, CV client 0.29–0.63 and server 0.31–0.73 (N1–N4);
   - hundreds of > 500 ms requests per 10 min (N5);
   - neither tracks the network (r ≤ 0.37) → the noise is in the cluster.
2. **Stall diagnosis (Table II):**
   - 37 events in 317 s, 7.0/min, median 0.81 s, 2.7 % of requests slow;
   - 0 events coincide with a ping spike → container-level, not host or network (N6–N9);
   - after the fix: 0 events, 0 slow requests in 17,141 (N10).
3. **After the fix:**
   - server CV 0.14 / 0.19, while client CV stays 0.60 / 0.35;
   - client tracks ping: r = 0.95 / 0.87; server r = 0.39 / 0.17 (N11–N16).
4. **Fig. b2:** tick by tick, client spikes coincide with ping spikes; the server line stays flat.
5. **Wi-Fi power-save tuning** lowered client CV from 0.60 to 0.35 but did not remove the coupling (r 0.87): the link remains the dominant client-side noise source (N15, N16).
6. **Answer to RQ1** (one sentence): three sources (runtime stalls, network, estimator), separable by measuring at the server and on the path.

### 4.2 RQ2 — estimator noise and the tail-sample rule (~0.6 page) — Fig. b3
1. **Capture:** 37,933 requests, 41 req/s; at a 20 s tick ≈ 825 requests (N20, N21).
2. **Sampling CV at 20 s:** p50 0.022, p90 0.034, p95 0.058, p99 0.097 (N22–N25).
3. **Fit:** CV ≈ 0.24·k^−0.41 → ~8 tail samples for 10 % CV, ~3 for 15 % (N26–N28).
4. **Practical reading:**
   - at our tick and load, p95 has ~41 tail samples and p99 only ~8;
   - for p99 to have as many tail samples as p95 at 20 s, the window would need to be 5× longer (100 s), i.e. a 5× slower reaction (N29);
   - this motivated using the server-side **p95**.
5. **Share of variance:** sampling noise explains only a minor part of the tick-to-tick variance for p95 (~2 % at 20 s) and more for p99 (~11 %) (N30, N31). The rest is real load variation, including a ~5-min disturbance in the capture (window p95 36–67 ms; disclose it, N32).
6. **Answer to RQ2:** size the window by tail samples; p95 at a 20 s tick satisfies the rule here, p99 does not.

### 4.3 RQ3 — decisions (~1.0 page) — Table III, Fig. b4, Table IV
1. **Threshold inflation (Table III):**
   - same rule, client threshold 3.2–5.7× its median vs server 2.4–2.9× (N40–N43);
   - held-out false-breach rates similar (0–2.2 %) → the rule controls false alarms for both, **at the cost of a looser client threshold**.
2. **Detectability (Fig. b4):**
   - all four fault smokes exceed the server threshold (1.01–2.83×) and stay below the client threshold (0.27–0.71×) (N44–N47);
   - the client signal also produced a lead-in spike of 6.25× its threshold before any fault (a 5 s timeout over the link) (N48).
3. **Runbook view (Table IV):**
   - on 421 fault-free ticks: server over threshold 1.4 %, client 4.0 %, no consecutive pairs for either (N49, N50);
   - detection in the first two fault ticks: F2 27/29 vs 3/29; F4 29/29 vs 5/29; F1 and F3 equal (N51–N54);
   - explain F3: detected through the failure ratio, which both use; F1: very large latency.
4. **Transport:** 2.96 → 0 Prometheus connect timeouts per 100 ticks with keep-alive (N55, N56). Small numbers; present as an engineering observation.
5. **Answer to RQ3:** the measurement point decides detectability of latency-only faults; the threshold, not the false-alarm rate, carries the cost.

---

## §5 Guidelines (~0.45 page)
Numbered list, each guideline with **"because: result X"**:
1. Measure the SLA signal at the server (or as close to the service as possible) when the controller is remote (RQ1, RQ3).
2. Calibrate a threshold per signal, then verify detectability with oracle-remedied fault injections before trusting it (RQ3; smoke tests).
3. Choose quantile and window so that n(1−q) ≳ 8 expected tail samples per window (RQ2).
4. Diagnose and remove runtime stalls before tuning thresholds; stall events checked against ping separate container from host/network (RQ1).
5. Keep monitoring connections alive on lossy links; disable library retries and let the control loop handle failures (transport).
6. Check that the load model spreads load after a scale-out (keep-alive load generators pinned all traffic to one pod in our testbed) (testbed validity; one sentence, facts in `04_methodology_facts.md` §8).

## §6 Threats to validity (~0.25 page)
- **Internal:** cluster retuned during the study (CPU limits, runtime fix, logging), all documented and fixed before the reported decision-level runs; the capture's 5-min disturbance; F2 with one worker is near the SLA (smoke peaks 1.01–1.10×).
- **External:** one application, a single-node cluster on a laptop, one wireless link, a closed-loop load generator (`schroeder2006open`).
- **Construct:** detection measured on the first two fault ticks; the runbook acted on the server signal, so client-side detection is what the same ticks would have shown.
- **Statistical:** 30-tick runs; bootstrap with 400 resamples; seeds fixed.

## §7 Related work (~0.45 page)
Three short paragraphs; content and differences in `06_references.md`:
1. Tail-latency sources and measurement: `dean2013tail`, `li2014tales`, `zhang2016treadmill`, `kogias2019lancet`, `schroeder2006open`.
2. SLO-driven control of microservices: `qiu2020firm`, `zhang2021sinan`; benchmarks `gan2019deathstarbench`, `vonkistowski2018teastore`.
3. Self-adaptive systems and monitoring: `kephart2003vision`, `garlan2004rainbow`, `moreno2018swim`, `hezavehi2021uncertainty`, `weyns2020introduction`.

## §8 Conclusion (~0.2 page)
One sentence per RQ answer; artifact availability (anonymized link at review time); a one-line outlook (e.g. multi-node clusters, open-loop load, learned controllers).

## Acknowledgment
AI-assistance statement: content points in `07_format_and_submission.md`. Anonymous at review time.
