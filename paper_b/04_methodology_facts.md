# 04 — Methodology Facts and Glossary

> Exact facts for the Setting and Method sections. Every item was taken from the project's configuration, scripts or recorded data. If a fact is not here, do not invent it; ask the project lead.

## 1. Testbed

| Item | Fact |
|---|---|
| Application | Google **Online Boutique v0.10.7** (`onlineboutique`): 11 deployments (10 services + redis-cart); its bundled load generator is removed |
| Orchestrator | **K3s v1.36.4+k3s1**, single node (Machine A: laptop, 16 CPU, ~15 GB RAM, Ubuntu 26.04) |
| Monitoring | **Prometheus v3.5.0** scraping kubelet **cAdvisor every 5 s** (housekeeping interval 5 s); per-service CPU, CFS throttling, memory |
| Fault injection | **Chaos Mesh 2.8.4** (CPU stress) and Kubernetes API patches |
| Controller host | Machine B: Windows laptop (Intel i7-12700H) running Linux under WSL2; runs the controller, the load generator and all measurement clients |
| Link A–B | **Wi-Fi mobile hotspot** provided by a third machine (wireless edge link); B is the clock authority, synchronized to A (offset typically < 20 ms) |
| Load generator | **Locust 2.46.6**, closed-loop simulated users, wait 0.5–1.5 s between tasks, 5 s request timeout, upstream Online Boutique task mix; pinned to 2 CPU cores of B; one TCP connection per request (see §8) |
| Steady load | U_base = **30 users** (frontend at 50–60 % of its CPU limit), ~34 req/s client-side (≈ 41 req/s server-side, because the frontend logs followed redirects separately) |
| CPU limits (documented tuning) | frontend 400m; currencyservice 500m; productcatalogservice 500m; recommendationservice 500m; cartservice .NET thread-pool minimum 32 (the "runtime fix") |

## 2. Control loop

- **Tick:** 20 s on a fixed monotonic schedule.
- **Collection:** all signals are fetched concurrently after each tick boundary, under a 3.0 s hard deadline. A source that fails or is incomplete marks the tick **stale** (it is imputed and never treated as data).
- **SLA signal (final design):** the server-side p95 of the frontend's request latency over the tick window, with a calibrated threshold L.
- **Health:** latency ≤ L and failure ratio ≤ 1 %.
- **Decision latency** (window close → action dispatched): median ~0.4 s, p95 < 1 s.

## 3. The three signals (Table I)

| Signal | How it is measured | What it includes |
|---|---|---|
| **Client p99** | Locust records every request's response time and success on B; per-tick p99 over requests completed in the window (nearest rank); a timeout counts as a failure at 5,000 ms | application + wireless link + TCP handshake |
| **Server p95 / p99** | each frontend pod's request log is read every tick; `http.resp.took_ms` of requests completed in the window (health checks excluded); nearest-rank quantile; a read with fewer than 0.9 × the expected request count is treated as missing | application only |
| **Ping p99** | `ping -i 0.2` from B to A during the window (≈ 100 probes per tick); p99 RTT | network path only |

**Same requests, same windows.** All three are computed over the identical 20 s window on B's clock.

## 4. SLA calibration rule

- **Threshold:** L = ceil to 10 ms of (1.5 × q95 of the steady-state tick values), from a 30-min (90-tick) steady run at U_base.
- **Stability gate:** CV of steady ticks < 0.30 and < 2 % of ticks breaching L.
- **Final server-side calibration:** L = **70 ms**, steady median tick p95 24 ms, q95 41 ms, CV 0.239, 0 breaches, 1 stale tick of 90.
- **For RQ3** the same rule is applied to each signal separately (§7 below).

## 5. RQ1: tick-aligned runs and stall diagnosis

**Tick-aligned runs:** 6 runs × 30 ticks (10 min each), 2 and 9 Oct 2026.

| Row label | Users | Cluster state |
|---|---|---|
| U50 baseline | 50 | frontend 400m, currency 300m, recommendation 200m |
| U50 repeat (+host metrics) | 50 | same, 12 min later; host CPU and pressure-stall metrics added |
| U50, recommendation 500m | 50 | recommendationservice limit 500m |
| U40 | 40 | as above |
| U40, runtime fix | 40 | + cartservice thread-pool minimum 32 |
| U40, runtime fix + Wi-Fi tuned | 40 | + Wi-Fi power saving disabled on A and on B's Windows host (power-save off, SMPS off, U-APSD off) |

**Statistics:** per run, the CV across ticks of client p99 and of server p99, and the Pearson r of each with ping p99. The client/server series are p99 here because these runs predate the p95 decision; RQ2 explains the switch.

**Stall diagnosis:**
- Over ~5 min: every frontend request is logged (deduplicated by request id).
- Requests > 500 ms ("slow") are grouped into one **stall event** when they start within 1 s of each other.
- During each event, ping to A (every 0.2 s) is checked: a ping > 150 ms would indicate a host-wide freeze.
- cAdvisor CFS throttling counters per service are sampled to see which service was throttled.
- Run before and after the runtime fix at 40 users.

## 6. RQ2: estimator analysis

- **Capture:** 37,933 completed frontend requests (health checks excluded, deduplicated) over 15.3 min at U_base = 30 on 10 Oct 2026, at the final cluster state; 41.4 req/s server-side.
- **Windows:** 5, 10, 20, 40, 60 s, non-overlapping. Quantiles q = 0.5, 0.9, 0.95, 0.99, nearest rank (`hyndman1996quantiles`).
- **Sampling CV:** per window, 400 bootstrap resamples (with replacement, same size) of that window's latencies; the CV of the resampled quantile; the median across windows is reported (`efron1993bootstrap`). Fixed RNG seed.
- **Observed CV:** the CV of the quantile across windows (real variation + sampling).
- **Fit:** log-log least squares of sampling CV on k = n(1−q) over q ≥ 0.9 → CV ≈ a·k^b with a = 0.235, b = −0.406.
- **Disclosure:** a ~5-min period in the middle of the capture had window p95 of 36–67 ms (otherwise 20–24 ms), most likely background load on Machine A. It is kept as recorded.

## 7. RQ3: decision view

- **Threshold inflation:**
  - per signal, the calibration rule is applied to the first half of a steady run and false breaches are counted on the second half;
  - three steady runs: 9 Oct (keep-alive load, U33), 10 Oct 14:26 and 10 Oct 15:44 (final; both U30, connection per request).
- **Detectability:**
  - four oracle-remedy fault smokes at the final state (F4 surge ×3.0 and ×2.5; F2 CPU stress with one worker on cartservice and on currencyservice);
  - per signal, the peak per-tick value between injection and the remedy, divided by the signal's threshold from the final calibration run (server 70 ms, client 800 ms).
- **Runbook view:**
  - 140 recorded episodes of a scripted SRE runbook (20 validation with no randomness; 120 warm-start episodes with 25 % uniformly random valid actions), 13 Oct 2026;
  - fault-free ticks = NULL-episode ticks and lead-in ticks, cut at the first action (421 ticks);
  - detection = a breach in the first **two** fault ticks (the runbook needs two consecutive unhealthy ticks before acting), counting "latency over threshold OR failure ratio > 1 %", and also latency-only;
  - stale ticks excluded.
- **Fault types:**

  | Fault | What it does | Severities |
  |---|---|---|
  | F1 bad deploy | productcatalogservice gets an injected extra latency | 300 ms / 600 ms / 1 s |
  | F2 hot pod | Chaos Mesh CPU stress in cartservice or currencyservice | 1 or 2 workers |
  | F3 scale to zero | cartservice or currencyservice scaled to 0 | — |
  | F4 traffic surge | Locust users ×2.5 / ×3.0 | — |

## 8. Engineering facts used in Guidelines and Threats

- **Transport:** Prometheus queries originally used a new TCP connection each (3 per tick). On the lossy link a lost SYN costs a 1 s retransmit, which meets the 1.0 s connect timeout, so the tick goes stale. A keep-alive session (library retries still disabled) removed this (N55, N56).
- **Load model:** with keep-alive Locust users, kube-proxy (per-connection balancing) pinned all users to the frontend pod that existed when they connected. During a surge, scaled-out pods received 0.00 cores of load and latency never recovered. One connection per request fixed it. Two further CPU-limit bottlenecks (productcatalogservice, currencyservice) were then found and raised, after which the surge became curable by scale-out (smoke MTTR 80 s).
- **Runtime fix:** cartservice (.NET) at a 300m CPU limit sees one processor, so its thread pool starts with one worker; bursts caused thread-pool starvation (sub-second stalls). Forcing a minimum of 32 workers removed the stalls (Table II).

## Glossary

| Term | Meaning here |
|---|---|
| tick | one 20 s control period |
| p95 / p99 | 95th / 99th percentile (nearest rank) of latencies in a tick window |
| CV | coefficient of variation = standard deviation / mean (across ticks or windows) |
| L / L_SLA | calibrated SLA latency threshold (server p95: 70 ms) |
| U_base | steady load: 30 simulated users |
| stale tick | a tick whose data was incomplete (failed or truncated source); imputed, never used as data |
| k = n(1−q) | expected number of samples above the q-quantile in a window of n requests |
| oracle remedy | the known correct fix applied by a test script, to check that a fault is curable and detectable |
| runbook | rule-based remediation controller (7 ordered rules) acting on the server signal |
| fault-free tick | no fault active and no action taken yet in that episode |
