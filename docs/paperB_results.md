# Paper B — Results Pack (facts and numbers, not prose)

> **For the paper team: use `paper_b/` (start at `paper_b/README.md`).** This file is the analysis log the package was built from.

> Source of truth for numbers: `figures/paperB/analysis.json`, produced by `python -m eval.paperb_analysis` from recorded testbed data (nothing simulated). Figures are made by `python -m eval.paperb_figures`. **The authors write all paper text**; this file only lists facts, provenance and caveats. Regenerate both before submission if more data is added.

## Setting (for the method section)

- **Testbed:**
  - Online Boutique v0.10.7 on single-node K3s (Machine A, 16 CPU / 15 GB laptop);
  - controller and load generator on Machine B (WSL2);
  - A and B linked by a Windows mobile hotspot via a third machine.
- **Control loop:** 20 s ticks. The SLA signal is the per-tick latency quantile.
- **Three measurement points compared:**
  1. **Client-side** p99 from Locust on B (includes the wireless link).
  2. **Server-side** p99/p95 of the frontend's own `http.resp.took_ms`, read from its request logs each tick.
  3. **Network:** ping RTT p99 B→A, measured on the same tick windows.
- **Calibration rule** (for any signal): threshold `L = ceil_10ms(1.5 · q95(steady tick values))`. Gate G6: CV of steady tick values < 0.30 and < 2 % of ticks breaching.

## R1. Three noise sources, separated (Fig. b1, b2)

| Run (30 ticks × 20 s each) | CV client p99 | CV server p99 | r(client, ping) | r(server, ping) | server requests > 500 ms | median client / server / ping p99 (ms) |
|---|---|---|---|---|---|---|
| U50 baseline (2 Oct) | 0.44 | 0.48 | 0.02 | −0.04 | 876 | 682 / 629 / 121 |
| U50 repeat (+host metrics) | 0.29 | 0.31 | 0.20 | 0.13 | 1073 | 863 / 758 / 52 |
| U50, recommendation 500m | 0.61 | 0.73 | 0.37 | 0.30 | 622 | 523 / 432 / 88 |
| U40 | 0.63 | 0.67 | 0.30 | 0.27 | 595 | 630 / 536 / 48 |
| **U40, runtime fix (9 Oct)** | **0.60** | **0.14** | **0.95** | 0.39 | **0** | 169 / 88 / 84 |
| U40, runtime fix + Wi-Fi tuned | 0.35 | 0.19 | 0.87 | 0.17 | 0 | 200 / 147 / 62 |

**Before the runtime fix:**
- Both signals were dominated by in-cluster stalls: hundreds of requests over 500 ms per 10 min, and similar CV client- and server-side.
- Neither signal tracked network RTT.

**Runtime stalls (R1b, from the `latency_diag --stalls` runs, U40):**
- Before Treatment A: 37 stall events in 317 s (7.0 per min, median duration 0.81 s); 448 of 16,639 requests slow (2.7 %); **0 events coincided with a ping spike**, so the stalls were container-level, not host-wide.
- After Treatment A: 0 events, 0 slow requests out of 17,141.
- Treatment A: .NET thread-pool minimum 32 for cartservice (`DOTNET_ThreadPool_ForceMinWorkerThreads=0x20`). It removes thread-pool starvation at a CPU limit where `ProcessorCount` = 1.

**After the runtime fix:**
- The server-side signal became stable (CV 0.14–0.19).
- The client-side signal stayed volatile (CV 0.35–0.60) and **tracked ping RTT** (r = 0.95 and 0.87).
- Fig. b2 shows the Treatment A run tick by tick: client p99 spikes coincide with ping spikes; server p99 stays flat.

## R2. Estimator noise and the tail-sample rule (Fig. b3)

- **Data:** 37,933 completed frontend requests over 15.3 min at U_base 30, about 41 req/s server-side (`data/paperB/requests_u30.jsonl`, captured 10 Oct at the final cluster state).
- **Method:**
  - tile the capture into windows of 5, 10, 20, 40 and 60 s;
  - per window, take the nearest-rank quantile (same as the env);
  - estimate pure sampling CV by bootstrap (400 resamples per window, seed 2026).
- **20 s windows** (the control tick, n ≈ 825 requests):

| quantile | expected tail samples k = n(1−q) | sampling CV (bootstrap) | observed tick-to-tick CV |
|---|---|---|---|
| p50 | 412 | 0.022 | 0.112 |
| p90 | 82.5 | 0.034 | 0.268 |
| p95 | 41.3 | 0.058 | 0.444 |
| p99 | 8.3 | 0.097 | 0.294 |

- **Fit over q ≥ 0.9 and all windows:** `CV ≈ 0.24 · k^−0.41`.
  - About **8 expected tail samples** per window give 10 % sampling CV; about 3 give 15 %.
  - This is the **tail-sample rule**: choose window and quantile so that n(1−q) ≥ ~8.
- **Earlier measurement (9 Oct, ~900 requests per tick, U33):** sampling CV about 0.145 for p99 against 0.09 for p95. That was the basis for switching the SLA quantile to p95 (pre-registered UNFREEZE).
- **Caveat to state:** the capture contains a ~5-min mid-run period where window p95 rose to 36–67 ms (otherwise about 20–24 ms). It was most likely background load on Machine A, since A ran other workloads then. It inflates the *observed* CV (0.44 for p95), not the bootstrap sampling CV, and it stayed below the 70 ms SLA. It is kept as recorded, not cut. Sampling noise is therefore a **minor** share of tick-to-tick variance at p95 (about 2 % at 20 s) but larger at p99 (about 11 %).

## R3. Impact on decisions: threshold inflation and detectability (Fig. b4)

**False alarms on fault-free ticks.** Each signal is calibrated with the same rule on the first half of a steady run and tested on the second half:

| Run | signal | CV | threshold | threshold / median | held-out breach rate |
|---|---|---|---|---|---|
| keep-alive load, U33 (9 Oct) | server p95 | 0.29 | 60 ms | 2.4× | 2.2 % |
| | client p99 | 1.24 | 350 ms | 3.2× | 2.2 % |
| per-request connections, 10 Oct 14:26 | server p95 | 0.22 | 60 ms | 2.6× | 0 % |
| | client p99 | 0.51 | 940 ms | 3.2× | 0 % |
| per-request connections, 10 Oct 15:44 (final) | server p95 | 0.24 | 70 ms | 2.9× | 0 % |
| | client p99 | 0.75 | 1100 ms | 5.7× | 0 % |

The calibration rule keeps false alarms similar for both signals. **The cost of the client-side signal is a much looser threshold** (3–6× its median), which hides faults.

**Detectability, at the final cluster state.** Fault peak before the oracle remedy, as a multiple of each signal's threshold from the final calibration run (server 70 ms, client 800 ms):

| Fault (smoke) | server p95 peak / threshold | client p99 peak / threshold |
|---|---|---|
| F4 surge ×3.0 | 2.83 | 0.65 |
| F4 surge ×2.5 | 2.11 | 0.39 |
| F2 CPU stress cartservice, 1 worker | 1.10 | 0.27 |
| F2 CPU stress currencyservice, 1 worker | 1.01 | 0.71 |

- **All four faults are detected server-side and invisible client-side.**
- In the F4 ×2.5 run, the client signal also produced a lead-in spike of 6.25× its threshold before any fault: a 5 s request timeout over the hotspot, i.e. a false alarm.
- **Caveat to state:** F2 with 1 worker is marginal even server-side (1.01–1.10×). It is a mild, near-SLA fault, reported per severity in Paper A.

## R4. Monitoring transport over a lossy link

- Prometheus queries originally opened a new TCP connection each (3 per tick). A lost SYN costs a 1 s retransmit, which meets the 1.0 s connect timeout, so the tick goes stale.
- **Before:** 8 Prometheus ConnectTimeouts in 270 ticks (2.96 per 100 ticks; stale share 1.9 %; three calibration runs).
- **After a keep-alive session** (retries still disabled): 0 in 90 ticks (stale share 1.1 %, from other causes).
- **Also:** a 30-tick fixture recorded just before the fix had 2 ticks stale from exactly this cause.
- **Caveat:** small numbers. More "after" data will come from the M4 runs (every tick logs source failures).

## R5. Testbed-validity lesson (useful for Paper A methodology too)

Oracle-remedy smoke tests exposed three stacked defects that made F4 uncurable by **any** policy:
1. Keep-alive load-generator connections pinned all load to the original frontend pod. The scaled-out pods used 0.00 cores; one connection per request fixed it.
2. productcatalogservice saturated at 200m with a maximum of 1 replica (throttle 0.87–0.96).
3. Then currencyservice saturated at 300m (throttle 0.35–0.62).

After the fixes, F4 ×3.0 and ×2.5 recover in 80 s after SCALE_UP frontend.

## Figures

| File | Shows | Suggested caption facts |
|---|---|---|
| `figures/paperB/b1_decomposition.pdf` | per-run CV and RTT correlation, client vs server | runs in investigation order; the runtime fix separates the two signals |
| `figures/paperB/b2_timeseries_treatA.pdf` | 30 ticks after the fix | client p99 follows ping p99; server p99 flat (CV 0.14) |
| `figures/paperB/b3_estimator_noise.pdf` | sampling CV vs k, log-log | p90/p95/p99 over 5–60 s windows; fit 0.24·k^−0.41; grid line at 10 % |
| `figures/paperB/b4_detectability.pdf` | fault peak / threshold | thresholds from the same calibration rule; line at 1 |

## R6. Decision impact on the runbook (A7)

- **Data:** the M3 runbook validation (20 episodes, ε = 0) and the warm-start collection (120 episodes, ε = 0.25), 13 Oct, at the final cluster state.
- **Thresholds:** each signal's threshold comes from the same calibration rule on the final calibration run: server p95 70 ms, client p99 800 ms.

**False triggers on fault-free ticks:**
- The ticks used are those from NULL episodes and from lead-ins, cut at the first action so the state is unperturbed: 421 ticks.
- Server p95 is over threshold on 6 ticks (1.4 %); client p99 on 17 (4.0 %), about 3× as often.
- Neither produced two consecutive breaches, so neither would have falsely triggered the runbook (its not-H(2) condition) in these data.

**Detection within each fault's first two ticks** (before the runbook can act). The full health rule is latency over threshold OR failure ratio > 1 %:

| Fault (episodes) | server p95 | client p99 | note |
|---|---|---|---|
| F1 bad deploy (24) | 24/24 | 24/24 | +0.3–1 s latency, visible to both |
| F2 CPU stress (29) | **27/29** | **3/29** | latency-only fault |
| F3 scale-to-zero (33) | 33/33 | 33/33 | detected through the shared failure ratio (latency alone: server 0/33, client 13/33) |
| F4 traffic surge (29) | **29/29** | **5/29** | latency-only fault |

**Takeaway (facts):** with each signal calibrated by the same rule, a client-side-fed controller would miss most latency-only faults (F2, F4) in their first two ticks, while seeing about 3× more isolated false breaches. Faults that cause errors (F3) or very large latency (F1) are caught either way.

**Caveat:** detection is measured on the first two fault ticks only. In these runs the runbook acted on the server signal; the client numbers are what the same ticks would have shown.

## Still to add

- **Related work and venue fit (SEAMS):** to be discussed with the authors.
- **Related work and venue fit (SEAMS):** to be discussed with the authors.
