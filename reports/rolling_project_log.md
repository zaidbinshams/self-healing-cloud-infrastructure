# Rolling Project Log — Self-Healing Cloud Infrastructure (PER-iSAC on K3s)

> A living summary of the project, appended at the end of each milestone. Detailed evidence lives in the per-milestone reports (`reports/m<N>_report.md`); the technical source of truth is `CLAUDE.md`, and the locked numbers are in `config/contract.yaml`.

---

## Project at a Glance

**Research question.** Can a masked discrete Soft Actor-Critic agent with Prioritized Experience Replay (PER-iSAC) restore a real microservice system faster than three baselines:
- default Kubernetes,
- Kubernetes with HPA,
- a scripted SRE runbook?

**Headline metric.** Mean time to recovery (MTTR) under a fixed, committed fault schedule.

**Testbed.** Google's Online Boutique (11 deployments) on single-node K3s (Machine A). It is driven and observed from a separate execution machine (Machine B), which is the sole timestamp authority. The agent sees and acts on four services:
- `frontend`
- `cartservice`
- `currencyservice`
- `productcatalogservice`

**Faults.**
- **F1:** bad deploy, injected latency in `productcatalogservice`.
- **F2:** hot pod, CPU StressChaos.
- **F3:** service scaled to zero.
- **F4:** load surge.
- **NULL:** no fault.

**Actions.** 12 discrete actions: NOOP, plus RESTART, SCALE and RESTORE on the managed services.

**Core principle.** Real system physics only. No simulator, no synthetic metrics, and no ground truth in the agent's observations.

---

## Milestone Status

| Milestone | Scope | Status | Gate evidence |
|---|---|---|---|
| **M1** Cluster Baseline | two-machine topology, least-privilege RBAC, golden state, clock sync | ✅ Done (2026-10-01; re-validated 2026-10-02) | `preflight m1` 18/18 |
| **M2** Telemetry & Adversary | Prometheus, Locust `/tick`, Chaos Mesh, telemetry freshness, limit tuning | ✅ Done (2026-10-02) | `preflight m2` 15/15; smoke test throttle 0.035 → 0.52 at 15 s → 1.0 |
| **M3** Scripted Runbook & Gym Bridge | real-cluster Gymnasium env, injector, `reset()`, calibration, gates G1–G7, warm-start data | ⏭ Next | — |
| **M4** PER-iSAC Integration | masked discrete SAC + PER, offline warm-start, online training | Planned | — |
| Later | evaluation against baselines, ablations, figures | Planned | — |

---

## M1 — Cluster Baseline (summary)

**Architecture.**
- **Machine A** (16 vCPU, 15 GiB, Ubuntu 26.04.1) runs K3s `v1.36.4+k3s1` with Traefik disabled.
- **Machine B** (WSL2 on Windows) runs everything else.
- The two are joined by a wireless hotspot.

**Access.** Three kubeconfigs (admin, agent, controller) enforce least privilege. The agent cannot delete pods or touch other namespaces. Preflight verifies this with 18 and 19 access-review cases.

**Golden state.** Online Boutique `v0.10.7` is transformed deterministically by `build_golden.py`:
- `loadgenerator` removed;
- NodePort 30080;
- replicas set to 1;
- contract limit overrides applied.

The API-defaulted live templates are then snapshotted to `config/golden_live.json`. This file is the source for RESTORE and `reset()`.

**Clock.** WSL2's kernel clock follows the Windows host, and A cannot reach B through NAT. The time direction was therefore reversed: A serves NTP (chrony), and Windows w32time follows A with a tuned loop (`UpdateInterval=100`, `FrequencyCorrectRate=2`, `MaxAllowedPhaseOffset=1`). Skew went from **−200 to −1139 ms and drifting** to **+6.8 ms mean (max 13.8 ms) over 20 min**. It stayed at **+3 to +4 ms** after a full reboot. The 200 ms design gate was restored.

**Approved overrides.** RTT p99 < 800 ms (measured: 187 ms), A idle memory < 10 GiB, A loaded memory < 12 GiB.

→ Details: [`m1_report.md`](m1_report.md)

---

## M2 — Telemetry & Adversary (summary)

**Observability.**
- **Prometheus `v3.5.0`:** 5 s cAdvisor scrape, a 4-metric keep-list, 125 series, 44 MiB.
- **Locust `2.46.6`:** 50 users, pinned to cores 0–1, with a `/tick` window endpoint.

**Adversary.** Chaos Mesh 2.8.4 runs on the K3s containerd socket, is limited to the `boutique` namespace, and has no dashboard.

**Finding 1: stale CPU telemetry.** The kubelet's default 10 s cAdvisor housekeeping left the 30 s `rate()` window empty in 33–50% of live-edge evaluations. Housekeeping was set to **5 s** on A. Result: **0/24 missing**, and newest-sample age fell from ~12 s to ~8 s (median).

**Finding 2: saturated baseline.** At 50 users:
- `frontend` was ~99% throttled, so its limit went from 200m to **400m**.
- That exposed `currencyservice` at 0.41–0.46 throttle, above the runbook's 0.40 fault threshold, so its limit went from 200m to **300m**.

| Metric | Before | After |
|---|---|---|
| Final baseline throttle (median / max) | frontend ~0.99; currency 0.41–0.46 | frontend 0.158 / 0.234; currency 0.049 / 0.076; productcatalog 0.083 / 0.143; cart 0 |
| p99 latency (20 s window) | 1177 ms | **683 ms** (−42%) |
| p50 latency | 81 ms | 49 ms |
| throughput | 48.0 rps | 53.95 rps |

**Smoke test (final limits).** `currencyservice` throttle went from 0.035 to **0.521 at 15 s** (gate: ≥ 0.40 within 40 s) and to 1.0 at 26 s. Non-target services stayed at or below 0.075.

**Change control.** Both limit changes went through `golden_overrides` with `CONTRACT-CHANGE` commits, a regenerated manifest and a re-snapshotted `golden_live.json`.

→ Details: [`m2_report.md`](m2_report.md)

---

## Decision Log

| Date | Decision | Approved by | Commit |
|---|---|---|---|
| 2026-10-01 | Relax RTT, A idle-memory and (temporarily) skew preflight gates for the hotspot/shared-host environment | human | `35484dd` |
| 2026-10-01 | Reverse clock direction: A serves NTP, Windows w32time follows A | human | `7b791b6` |
| 2026-10-01 | Retire the 1000 ms skew override (back to 200 ms); relax A loaded memory to 12 GiB | human | `0c41cfb` |
| 2026-10-02 | Kubelet cAdvisor housekeeping 10 s → 5 s | human | `1a2f54a` |
| 2026-10-02 | `frontend` CPU limit 200m → 400m (`CONTRACT-CHANGE`) | human | `288fe90`, `69a0195` |
| 2026-10-02 | `currencyservice` CPU limit 200m → 300m (`CONTRACT-CHANGE`) | human | `4e9576a`, `94d60f8` |

---

## Known Risks Carried Forward

1. **Wireless link.** RTT p99 reached 187 ms in M1, and a 1 s Prometheus connect timeout was seen once in M2. This limits the timing headroom for gate G1 and must be absorbed by typed-failure handling.
2. **Telemetry gate G2** requires fewer than 1% stale ticks. One isolated `Q_thr` miss in 24 was observed in one probe, so the rate needs measuring at M3 scale.
3. **Calibration may change `U_base`** from 50 users, in which case the baseline throttle levels must be re-checked.
4. **Shared Machine A.** Unrelated workloads add noise and use memory (A was at 9.02 GiB under load).

---

## Next Steps — Entering Milestone 3

M3 builds the **real-cluster Gymnasium environment** and proves it end-to-end with the **scripted runbook**, before any learning. The **PER-iSAC agent (PyTorch) comes in M4** and trains against this environment.

1. **Telemetry layer and recorded fixtures.**
   - Implement `env/contract.py`, `env/clock.py` and `env/telemetry.py`, with concurrent collection under the 3.0 s deadline and §5.5 imputation.
   - Implement `scripts/record_ticks.py` and record 30 real steady-state ticks to `tests/fixtures/ticks_steady.jsonl`.
   - Add unit tests for normalization and imputation.
2. **Environment core.**
   - `env/k8s_actions.py`: action catalog, mask, async executor.
   - `env/golden.py`, `env/reward.py`, `env/recorder.py`.
   - `env/injector.py`: F1–F4 and cure checks.
   - `env/boutique_env.py`: tick protocol and active-restore `reset()`.
3. **Contract check.** Run the env with a random valid policy and no faults (3 episodes).
4. **Calibration.** Run `scripts/calibrate.py --minutes 30` to produce `L_SLA`, `RPS_base` and `U_base` in `config/calibration.json`.
5. **Fault smoke tests.** Run F1–F4 with their oracle remedies, including the F4 check that frontend is the bottleneck.
6. **Runbook validation.**
   - 40 runbook episodes, then gates G1–G7.
   - Then collect 120 runbook episodes with ε = 0.25 as the warm-start dataset for M4.

### M3 progress log (appended 2026-10-09)

- **G6 Treatment A** (cartservice .NET thread-pool minimum 32): removed the whole-process stalls (0 slow requests in 17k). Server-side tick latency became stable; client-side stayed hostage to hotspot RTT (r = +0.95).
- **Measurement point:** SLA latency moved server-side (frontend request logs; agent credential gained read-only `pods/log`; kubelet log size raised to 200Mi so reads are not truncated).
- **Pre-experiment freeze** declared (PLAN.md §0.2). The synthetic prototype moved to branch `prototype/synthetic-model`. Evaluation protocol: interleaved, 50 episodes per policy, mandatory cold-start ablation.
- **UNFREEZE 2026-10-09:** the SLA quantile p99 → p95, after calibration #2 failed G6 narrowly (CV 0.285) and a bootstrap attributed ~0.145 CV per tick to p99 sampling noise alone. G6's threshold is unchanged. Superseded: fixture e62bcb5 and calibration attempt 20261009T144632.

- **G6 override (human-approved 2026-10-09):** CV limit 0.25 → 0.30, breach criterion unchanged. Calibrations:

  | # | SLA latency | CV | breach |
  |---|---|---|---|
  | 1 | client p99 | 0.519 | 1.1% |
  | 2 | server p99 | 0.285 | 1.2% |
  | 3 | server p95 | 0.292 | 1.1% |

  Run 3's CV is 0.237 without its single slowest tick. Run 3's recorded data is the calibration (U_base 33, L_SLA 60 ms). All three are to be reported in the paper.

*(Append the M3 summary here when its exit gate passes.)*
