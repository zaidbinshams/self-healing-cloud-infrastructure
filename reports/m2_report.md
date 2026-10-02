# Milestone 2 Report — Telemetry & Adversary

**Project:** Self-healing cloud infrastructure: a PER-iSAC agent remediating injected faults in Online Boutique on K3s
**Milestone window:** 2026-10-01 – 2026-10-02
**Status:** Exit gate passed on 2026-10-02:
- `preflight --stage m2`: 15/15 PASS;
- StressChaos smoke test: PASS against an unthrottled baseline;
- no throttling on non-target services;
- `preflight --stage m1` re-validated at 18/18 PASS.

**Commits:** `41a0b6f`, `0c41cfb`, `288fe90`, `69a0195`, `1a2f54a`, `4e9576a`, `94d60f8`

---

## 1. Objective

M2 provides the agent's senses and its adversary:
- **Prometheus** samples per-container CPU, CPU throttling and memory from the kubelet's cAdvisor.
- **Locust** generates realistic user load from Machine B and reports end-user latency and errors for any time window.
- **Chaos Mesh** injects CPU-stress faults inside the `boutique` namespace only.

The exit gate requires three things:
1. Telemetry is complete and finite for all four managed services.
2. A CPU-stress fault on `currencyservice` is visible as a throttle ratio ≥ 0.40 within 40 s.
3. No throttling appears on the non-target services while it runs.

---

## 2. Observability Setup

### 2.1 Prometheus (Machine A, namespace `monitoring`)

| Property | Value |
|---|---|
| Image | `quay.io/prometheus/prometheus:v3.5.0` |
| Scrape | kubelet cAdvisor every **5 s** |
| Keep-list | 4 metrics only: `container_cpu_usage_seconds_total`, `container_cpu_cfs_throttled_periods_total`, `container_cpu_cfs_periods_total`, `container_memory_working_set_bytes`; namespace `boutique` only |
| Resources | request 200m / 512Mi, limit 1 CPU / 1.5Gi; retention 3 d / 4 GB; NodePort 30090 |
| Head series | 115 (2026-10-01), 125 (2026-10-02); gate < 1000 |
| Memory in use | 44 MiB; gate < 1.2 GiB |

**Contract queries.** Three queries are evaluated at an explicit `time=` taken from B's clock. Each aggregates per deployment by parsing the pod name, because every Online Boutique container is named `server`.

| Query | Feature | Computation |
|---|---|---|
| `Q_cpu` | CPU usage | `rate(container_cpu_usage_seconds_total[30s])` |
| `Q_thr` | throttle ratio | `rate(throttled_periods[30s]) / rate(periods[30s])` |
| `Q_mem` | memory | `container_memory_working_set_bytes` |

The 30 s rate window is a locked contract value (`telemetry.rate_window`).

### 2.2 Locust (Machine B)

| Property | Value |
|---|---|
| Version | `locust==2.46.6`, `FastHttpUser` |
| Placement | pinned to cores 0–1 with `taskset`, so learner compute cannot starve the event loop |
| Load | **50 users**, wait 0.5–1.5 s, 5 s request timeout (a timeout counts as a failure at 5000 ms) |
| Task mix (weights) | browse product 10, view cart 3, set currency 2, add to cart 2, index 1, checkout 1 |
| `/tick?from=&to=` | custom route returning `n`, `failures`, `p50_ms`, `p99_ms` and `rps` for exactly the window `(from, to]`, from an in-memory ring buffer of `(t_B, response_ms, success)` |

### 2.3 Chaos Mesh (Machine A, namespace `chaos-mesh`)

The pre-existing install had its dashboard enabled. It was brought to a known configuration with `helm upgrade --install` of chart **2.8.4**, without `--reuse-values` (`chaos/install-chaos-mesh.sh`):
- `chaosDaemon.runtime=containerd`
- `chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock`, the K3s socket; without it the daemon silently does nothing
- `controllerManager.enableFilterNamespace=true`
- `dashboard.create=false`

Only `boutique` is enabled for injection. Preflight verifies the runtime, the socket hostPath and the namespace filter, checks that the daemon logs contain no socket errors, and checks that no dashboard is present.

**Smoke fault** (`chaos/smoke/stress-currency-60s.yaml`): a StressChaos with `mode: one` on `app=currencyservice`, container `server`, 1 CPU worker at 100% load, for 60 s. It has the same shape as the F2 "hot pod" episode fault.

---

## 3. Finding 1 — Missing CPU Metrics at the Live Edge

### 3.1 Symptom

On 2026-10-01, contract PromQL evaluated at the current time ("live edge") often returned **no value** for a managed deployment. A 2-minute test (24 evaluations) found:
- the newest cAdvisor sample was a median **12 s** and p95 **21 s** old at query time;
- `rate(...[30s])` came back empty in **8–46% of evaluations** per deployment.

### 3.2 Why it matters

Each missing value is imputed and marks the tick stale (CLAUDE.md §5.5):
- stale transitions never enter the replay buffer;
- 3 consecutive stale ticks truncate an episode and exclude it from evaluation;
- M3 gate G2 requires stale ticks on fewer than 1% of ticks.

At the measured miss rate, a large share of real-cluster experience would have been discarded.

### 3.3 Root cause

The kubelet's embedded cAdvisor refreshes container statistics every **10 s** by default, and backs off up to 15 s when stats change little. Prometheus keeps cAdvisor's own sample timestamps. The newest sample is therefore often 10–20 s old. A 30 s window anchored at "now" then frequently holds fewer than the two samples `rate()` needs.

### 3.4 Fix (human-approved 2026-10-02)

`k8s/k3s/kubelet-housekeeping.sh` (run on A) adds the K3s drop-in `/etc/rancher/k3s/config.yaml.d/capstone-kubelet.yaml` with `kubelet-arg+: ["housekeeping-interval=5s"]`, then restarts K3s. Pods are not restarted.

This choice keeps the locked `rate_window` (30 s) and Prometheus's honoring of source timestamps. It fixes the cause, sampling frequency, rather than loosening a gate.

### 3.5 Before and after

All three runs used the same read-only probe at 50 users: contract `Q_cpu` / `Q_thr` evaluated at `time=now` every 5 s, plus `timestamp()` of the newest sample.

| Run | Evaluations | `Q_cpu` missing per deployment | `Q_thr` missing per deployment | Newest-sample age, median | p95 |
|---|---|---|---|---|---|
| 2026-10-01, default 10 s | 24 | 8–46% | — | 12 s | 21 s |
| 2026-10-02, default 10 s | 12 | **4–6 / 12** (33–50%) | 3–5 / 12 | 11.2–12.3 s | 16.7–18.0 s |
| 2026-10-02, housekeeping 5 s | 24 | **0 / 24** | 1 / 24 † | **7.2–8.9 s** | 10.8–11.9 s |
| 2026-10-02, 5 s + final limits | 24 | **0 / 24** | **0 / 24** | 8.2–9.3 s | 10.6–12.8 s |

† A single evaluation in which all four deployments dropped together, consistent with one missed scrape rather than per-service staleness. Under §5.5 the value is carried forward (`locf_max_ticks = 2`), so the episode continues, but that tick is still flagged stale.

---

## 4. Finding 2 — CPU Saturation at Baseline (Limit Tuning)

### 4.1 Frontend: 200m → 400m

On 2026-10-01, with its upstream 200m limit, the `frontend` container was **~99% CFS-throttled at the 50-user baseline**, before any fault was injected. This would have had three consequences:
- calibration (M3) would record a saturated, queueing system as "healthy" and inflate the latency SLA;
- the "no throttling on non-target services" smoke gate would fail;
- the F4 load-surge fault, whose cure is scaling up frontend, would be indistinguishable from normal operation.

The load was held at 50 users and only the limit was raised, to **400m** (`288fe90`, `CONTRACT-CHANGE`).

### 4.2 Currencyservice: 200m → 300m

With frontend no longer the bottleneck, more traffic reached `currencyservice`. It became the most throttled service at baseline:

| Measurement (frontend at 400m, currencyservice at 200m) | `currencyservice` throttle |
|---|---|
| Probe, 12 evaluations | median 0.358 |
| Probe, 24 evaluations | median **0.414** |
| Pre-smoke baseline snapshot | **0.462** |

This sits above the scripted runbook's `throttle_threshold` of **0.40**, so a healthy baseline looked like an F2 (hot-pod) fault. It also meant the smoke gate "≥ 0.40" was met *before* any stress was applied, making that gate uninformative. The limit was raised to **300m** (`4e9576a`, `CONTRACT-CHANGE`), as anticipated in CLAUDE.md for `golden_overrides`.

### 4.3 Change procedure (both limits)

Both changes followed the same locked-file workflow:
1. Add the limit under `golden_overrides` in `config/contract.yaml`, in a commit marked `CONTRACT-CHANGE`.
2. Regenerate `golden.yaml` with `build_golden.py`; the diff is one limit line plus the contract-hash header.
3. Apply the manifest; the affected deployment does a zero-downtime rolling update.
4. Re-snapshot `golden_live.json` from a verified clean apply (`69a0195`, `94d60f8`).

Requests (100m), memory limits and the 50-user load were unchanged.

### 4.4 Before and after: baseline throttling (50 users, no fault)

| Service | Initial (all 200m) | Frontend 400m (24 evaluations) | **Final: frontend 400m, currency 300m (24 evaluations)** |
|---|---|---|---|
| frontend | ~0.99 | median 0.085 | median **0.158**, max 0.234 |
| cartservice | — | 0.000 | **0.000**, max 0.000 |
| currencyservice | — | median 0.414 | median **0.049**, max 0.076 |
| productcatalogservice | — | median 0.086 | median **0.083**, max 0.143 |

In the final configuration, every managed service stays below the 0.40 threshold at every evaluation. The highest value observed was 0.234, on frontend.

### 4.5 Before and after: end-user performance (Locust `/tick`, 20 s window)

| Configuration | n requests | failures | p50 | **p99** | rps |
|---|---|---|---|---|---|
| Frontend 400m, currency 200m (preflight, 2026-10-02) | 959 | 0 | 81 ms | **1177 ms** | 48.0 |
| Frontend 400m, currency 300m (2026-10-02) | 1079 | 0 | 49 ms | **683 ms** | 53.95 |

Removing the currencyservice bottleneck cut tail latency by **42%** (1177 → 683 ms), cut median latency by 40%, and raised throughput by 12% at the same offered load.

**Caveat.** Each row is a single 20 s window of about 1000 requests, so this is indicative rather than a statistically tested difference. The distribution of steady-state tick-P99 is measured properly by the 30-minute calibration run in M3.

**Side effect.** Frontend now does more work: it used 0.29 of its 0.40 cores, and its median throttle rose from 0.085 to 0.158. This is intended, because frontend should be the bottleneck under the F4 surge. M3 gate G7 checks that explicitly.

---

## 5. Fault Injection: StressChaos Smoke Test

The same 60 s CPU StressChaos was run on `currencyservice` twice. Throttle values come from the contract `Q_thr`, evaluated every ~5 s.

| t since apply | Run 1: currency at 200m | **Run 2: final limits (300m)** |
|---|---|---|
| baseline | 0.462 | **0.035** |
| +5 s | 0.503 | 0.044 |
| +10 s | 0.503 | 0.162 |
| +15–16 s | 0.760 | **0.521** (crosses 0.40) |
| +20–21 s | 0.760 | 0.807 |
| +26 s and later | **1.000** | **1.000** |
| Non-target maximum during stress | frontend 0.110, productcatalog 0.101, cart 0 | frontend 0.075, productcatalog 0.055, cart 0 |

**Run 2 is the valid exit-gate evidence.** Throttling rises from an unthrottled baseline (0.035) past 0.40 within **≈15 s**, against a 40 s requirement, and saturates at 1.0 by 26 s. Non-target services stayed between 0 and 0.075 throughout, and their throttling actually *fell* during the stress as traffic through the slowed currencyservice dropped. No sample was missing during either run.

---

## 6. Final Validation

`preflight --stage m2`, 2026-10-02, final configuration: **15/15 PASS**.

| Check | Result |
|---|---|
| Node Ready, 11 golden deployments rollout-complete, namespace chaos-enabled | PASS |
| Chaos Mesh: controller and daemon Running, no dashboard, containerd + K3s socket, namespace filter, clean daemon logs, only `boutique` enabled | PASS |
| Prometheus: targets up, 125 head series (< 1000), contract PromQL finite for 4 deployments × 3 queries, 44 MiB (< 1.2 GiB) | PASS |
| Locust `/tick` (n > 500, failure ratio < 0.01) | PASS |
| A memory under load | 9.02 GiB (< 12 GiB override) |
| A–B clock skew | +4 ± 6 ms (< 200 ms design gate) |

`preflight --stage m1` after the re-snapshot: **18/18 PASS**, with `golden_live.json` matching all 11 live templates.

---

## 7. Changes Relative to the Design (all human-approved)

| Change | Design | Deployed | Kind |
|---|---|---|---|
| Kubelet cAdvisor housekeeping | 10 s | 5 s | cluster tuning (2026-10-02) |
| `frontend` CPU limit | 200m | 400m | `CONTRACT-CHANGE` (2026-10-02) |
| `currencyservice` CPU limit | 200m | 300m | `CONTRACT-CHANGE` (2026-10-02) |
| A memory under load gate | < 8 GiB | < 12 GiB | threshold override (2026-10-01) |
| A–B skew gate | < 200 ms | < 200 ms (override retired) | — |

No other gate was loosened. All are documented in CLAUDE.md §2 and PLAN.md §0.1.

---

## 8. Limitations and Open Risks for M3

- **G2 telemetry budget.** One missing `Q_thr` evaluation out of 24 (≈4%) appeared in one probe; the final probe had 0 of 24. Every miss, even a carried-forward one, flags the tick stale (§5.5 rule 3), and gate G2 requires fewer than 1% stale ticks. A 24-sample probe cannot resolve a rate that low, so the true miss rate must be measured over the M3 steady-state and runbook runs.
- **Calibration may change the load.** `scripts/calibrate.py` searches `U_base` for about 50% CPU on A. If `U_base` differs from 50 users, the baseline throttle levels reported here must be re-measured.
- **Wireless link.** One 1 s Prometheus connect timeout was observed on 2026-10-02 and recovered immediately. The environment's typed-failure handling (CLAUDE.md §4.3) must absorb such events.
