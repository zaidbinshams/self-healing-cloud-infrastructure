# Milestone 1 Report — Cluster Baseline

**Project:** Self-healing cloud infrastructure: a PER-iSAC agent remediating injected faults in Online Boutique on K3s
**Milestone window:** 2026-10-01 – 2026-10-02
**Status:** Exit gate passed (`preflight --stage m1`: 18/18), including a re-run after the M2 limit changes on 2026-10-02
**Evidence:** `reports/m1_preflight.txt`, `reports/m1_cluster_state.txt`, commits `2e3ec34`, `35484dd`, `7b791b6`, `0c41cfb`

---

## 1. Objective

M1 establishes a reproducible, least-privilege, two-machine testbed with a frozen **golden** application state. Every later measurement depends on three properties set up here:

1. **Real physics.** The agent acts on a real Kubernetes cluster, never a simulator.
2. **A single trusted clock.** Machine B timestamps every observation, so A and B must agree on time.
3. **A known-good reference state.** RESTORE actions and `reset()` return the cluster to this state between episodes.

---

## 2. Dual-Machine Architecture

| Role | Machine A — cluster | Machine B — execution |
|---|---|---|
| Host | `zaid-laptop-0-u`, 16 vCPU, 15 GiB RAM, swap off | Windows host running WSL2 (Linux 6.6.87.2-microsoft-standard-WSL2) |
| OS / runtime | Ubuntu 26.04.1 LTS, kernel 7.0.0-30, containerd 2.3.4-k3s1.36 | Python 3.11 venv, CPU-only (no GPU anywhere in the project) |
| Software | K3s `v1.36.4+k3s1` (Traefik disabled, metrics-server enabled), Online Boutique, Chaos Mesh, Prometheus | Locust load generator, environment/injector code, runbook and agent, evaluation |
| Address | `192.168.137.10` | WSL2 NAT (`172.28.x`) behind the Windows Mobile Hotspot |

**Separation of concerns.** The cluster (A) only hosts the system under test. All control, load generation and measurement runs on B. Training compute on B therefore cannot perturb the cluster's CPU physics, and B is the only clock that stamps data.

**Network.** The design called for wired Ethernet with RTT p99 < 2 ms. The deployed link is a wireless hotspot (Section 5).

**Access control: three kubeconfigs.** Each is bound to its own ServiceAccount token. Files live in the git-ignored `config/kube/` with mode 0600.

| Kubeconfig | Holder | Rights (namespace `boutique` only) | Verified cases |
|---|---|---|---|
| `admin` | humans and bootstrap scripts | full cluster | — |
| `agent` | environment action executor and telemetry reads | deployments get/list/watch/patch; deployments/scale get/patch; pods get/list/watch (**no pod delete**) | 18/18 |
| `controller` | fault injector and `reset()` | deployments + scale get/list/patch; pods get/list/delete; `stresschaos` create/get/list/delete | 19/19 |

`preflight` checks each matrix with `SelfSubjectAccessReview` under that credential, covering both allowed and denied cases:
- **Agent:** 9 allowed and 9 denied. The denied cases include pod delete, deployment create/update/delete, `pods/exec`, StressChaos, Secrets, and anything in `kube-system`.
- **Controller:** 12 allowed and 7 denied. The denied cases include deployment create/delete, `pods/exec`, NetworkChaos, Secrets, and other namespaces.

---

## 3. Clock Synchronization (A → Windows → WSL2)

### 3.1 Problem

The design makes B the time authority: wall timestamps come from `time.time()` on B, and Prometheus queries are evaluated at an explicit `time=` from B. Any A–B skew shifts *which* samples a query sees. The first M1 preflight measured a skew of **−200 to −770 ms and drifting**, and once observed B's wall clock stepping backwards mid-probe. Later the skew reached −1139 ms.

### 3.2 Root cause

Under WSL2, B's Linux kernel clock is not B's to control. WSL's hidden system VM runs its own `chronyd`, which disciplines the shared kernel clock to the **Windows host clock** through the Hyper-V PTP device (`PHC0`). B's own chronyd is started with `-x` (it cannot set the clock), so it can only monitor. In addition, A cannot reach B on udp/123 because B sits behind NAT. The designed direction, with A following B, was therefore impossible.

### 3.3 Solution (human-approved 2026-10-01)

The time-source direction is reversed: **A serves time and B follows it through Windows.**

| Layer | Configuration | File |
|---|---|---|
| Machine A | chrony serves NTP to `192.168.137.0/24`, with `local stratum 10 orphan` so it keeps serving if A loses its own upstream sources | `k8s/k3s/chrony-server.sh` |
| Windows host of B | w32time with A as its only peer (`/manualpeerlist:A,0x8 /syncfromflags:manual`); poll 2^6 = 64 s; `UpdateInterval=100` (1 s); `FrequencyCorrectRate=2`; `MaxAllowedPhaseOffset=1` (step when the error exceeds 1 s) | `scripts/w32time-follow-a.ps1` |
| WSL2 (B) | kernel clock follows Windows via PHC0; B's chronyd (`-x`) is a passive A–B offset monitor (`chronyc -h 127.0.0.1 sources`) | `scripts/chrony-client.sh` |

**Why the non-default w32time loop settings matter.** With the standalone-PC defaults, w32time re-evaluated its slew only about once an hour. After a correction it kept slewing at ~1.2 ms/s and overshot A, going **from −600 ms to +590 ms**. The 1 s update interval and faster frequency correction removed the overshoot.

### 3.4 Result

| Measurement | Value |
|---|---|
| 20 min NTP probe B→A | mean **+6.8 ms**, sd 3.1 ms, max \|offset\| 13.8 ms, no trend |
| `preflight m1` skew after the fix | **−1 ± 6 ms** |
| `preflight m2` skew, 2026-10-02 (after a full reboot of both machines) | +3 ± 5 ms and +4 ± 6 ms, no wall-clock step during the probe |

The skew gate went back to its **200 ms design value**; the temporary 1000 ms override was retired. Measured skew is about 30× inside that gate.

---

## 4. Golden Baseline State

**Source.** The upstream Online Boutique manifest at pinned release **`v0.10.7`** (`k8s/boutique/upstream/kubernetes-manifests.yaml`).

**Deterministic transformation.** `k8s/boutique/build_golden.py` produces `k8s/boutique/golden.yaml` from the upstream manifest and `config/contract.yaml`. It:
1. removes `loadgenerator` (load comes only from Locust on B);
2. converts `frontend-external` from LoadBalancer to **NodePort 30080**;
3. sets `replicas` explicitly: the contract's `replicas.base` (all 1) for the four managed services, and 1 for the rest;
4. applies any `golden_overrides` resource limits from the contract (used in M2, see `m2_report.md`);
5. stamps the namespace `boutique`, then checks for exactly **11 deployments** (10 application services + `redis-cart`), each managed service exposing a container named `server`.

The output header records SHA-256 hashes of both inputs, so the manifest can be traced to its sources.

**Managed services.** The agent observes and acts on exactly four: `frontend`, `cartservice`, `currencyservice`, `productcatalogservice`. The other seven run but are outside its state and action space.

**Live snapshot.** Live Kubernetes objects carry API-defaulted fields, so comparing them with the raw YAML always shows drift. After a verified clean apply, `scripts/snapshot_golden.py` therefore captures the *live* templates into `config/golden_live.json`, with an identical copy at `k8s/boutique/snapshot-base.json`. It refuses to snapshot unless:
- all 11 deployments are rollout-complete at their golden replica counts;
- no template carries a `restartedAt` annotation;
- no `server` container carries the `EXTRA_LATENCY` fault variable.

Each entry stores the template, a SHA-256 hash of the template (canonical JSON, excluding `restartedAt`) and the managed services' CPU and memory limits. The limits are the denominators of the agent's `cpu_util` and `mem_util` features. The snapshot also records the contract hash, the golden.yaml hash, the OB version and the git SHA.

**Chaos scope.** The namespace `boutique` carries the label and annotation `chaos-mesh.org/inject=enabled`; no other namespace does.

---

## 5. Environment Overrides (Human-Approved 2026-10-01)

The deployed testbed differs from the design. Only these preflight thresholds were relaxed, each marked `[override]` in `scripts/preflight.py`:

| Check | Design gate | Override | Reason |
|---|---|---|---|
| A–B RTT p99 (`ping -c 200`) | < 2 ms | < 800 ms | wireless hotspot link, not wired Ethernet |
| A idle memory | < 5 GiB | < 10 GiB | A runs unrelated workloads alongside the testbed |
| A memory under load (M2) | < 8 GiB | < 12 GiB | A idles at about 8.8 GiB |
| A–B clock skew | < 200 ms | *(1000 ms override retired)* | fixed at the source (Section 3) |

**Clean-up.** Two orphaned `monitoring-kube-prometheus-*` Services left in `kube-system` were deleted. A pre-existing Chaos Mesh install with its dashboard enabled was reconfigured in M2.

**Reporting consequence.** All results from this project must state that they were obtained over a wireless link, with a shared Machine A.

---

## 6. Validation Results

**Final M1 preflight (`reports/m1_preflight.txt`):** 18/18 PASS.

| Check | Result |
|---|---|
| Contract, env file, `.gitignore`, kubeconfigs (0600, untracked) | PASS |
| Single node Ready, Traefik absent, metrics-server Available | PASS |
| 11 golden deployments rollout-complete; NodePort 30080; frontend HTTP 200 in 94 ms | PASS |
| Agent RBAC 18/18 and controller RBAC 19/19 least-privilege cases | PASS |
| `golden_live.json` matches all 11 live templates and is committed | PASS |
| A idle memory | 6.68 GiB (< 10 GiB override) |
| A–B RTT, n = 200, 0 lost | median 86.3 ms, **p99 187 ms**, max 261 ms (< 800 ms override) |
| A–B clock skew | −542 ± 6 ms in this record, under the then-active 1000 ms override; **−1 ± 6 ms** after the w32time fix, under the 200 ms design gate |

**Re-validation, 2026-10-02.** After the M2 changes to the frontend and currencyservice limits and the re-snapshot, `preflight m1` again gave **18/18 PASS**. Its golden check reported 11 templates matching snapshot git SHA `4e9576a`.

---

## 7. Limitations and Threats to Validity

- **Network.** The hotspot RTT (p99 187 ms here, versus a 2 ms design) reduces the headroom of the 3.0 s telemetry collection deadline and of the decision-latency gate G1 in M3.
- **Shared host.** Machine A runs other workloads, so CPU and memory contention from outside the testbed is possible. Watch for OOM kills and evictions.
- **Clock chain.** The clock depends on a Windows → WSL chain that the project does not fully control. If skew grows, the procedure is: check `w32tm /query /status` on Windows, then run `wsl --shutdown`. Preflight re-checks skew at every stage.
