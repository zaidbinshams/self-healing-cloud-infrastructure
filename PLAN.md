# PLAN.md — Self-Healing Cloud Infrastructure (PER-iSAC on K3s)

> **Execution roadmap.** This file says *what gets built, in what order, and how we prove each step works.*
> All formulas, queries, protocols and locked numbers live in `CLAUDE.md` and `config/contract.yaml`. If this file and `CLAUDE.md` disagree, `CLAUDE.md` wins.

---

## 0. Ground Rules

1. **One milestone at a time.** A milestone is done only when every validation command passes and its exit gate is green. Never build Milestone N+1 against a cluster that fails Milestone N checks.
2. **Two machines, fixed roles.**
   - **Machine A**: cluster node. Headless Linux, 12 GB RAM. Runs K3s, Online Boutique, Chaos Mesh, Prometheus.
   - **Machine B**: execution node. 10 GB RAM. Runs Locust, the Gymnasium env, the fault injector, the runbook, the PER-iSAC agent, evaluation, and Claude Code.
   - Design: the machines are connected by **wired Ethernet**. Deployed: a **wireless hotspot**, with environment overrides (§0.1).
3. **Where commands run.** Every command runs on **Machine B** from the repo root, unless it is marked **[A]**.
4. **Environment variables.** Every command assumes `source config/cluster.env` has been run.
5. **Wall-clock time on the real cluster is the scarcest resource.** Never start a run longer than 30 minutes without:
   - the watchdog,
   - per-tick transition persistence,
   - checkpointing.
6. **Credentials never enter git.** `config/kube/` is in `.gitignore`.

### 0.1 Environment Overrides (Human-Approved 2026-10-01)

A and B share a wireless hotspot, and A runs other workloads at the same time. Preflight therefore uses relaxed thresholds. Details and consequences are in `CLAUDE.md` §2 "Environment Overrides".

| Check | Design gate | Override |
|---|---|---|
| LAN RTT p99 | < 2 ms | < 800 ms |
| A idle memory | < 5 GiB | < 10 GiB |
| A memory under load (M2) | < 8 GiB | < 12 GiB |
| G6 steady SLA-latency CV (M3) | < 0.25 | < 0.30 (2026-10-09; breach < 2% unchanged) |

A–B clock skew uses the **200 ms design gate**. Its 1000 ms override was retired on 2026-10-01.

No other gate is relaxed. The G6 CV override and its safeguards are explained in `CLAUDE.md` §2.

**Cluster tuning (human-approved 2026-10-02).** Kubelet cAdvisor housekeeping on A is 5 s (`[A] sudo bash k8s/k3s/kubelet-housekeeping.sh`), so the 30 s rate window is never empty at the live edge. `frontend` (400m), `currencyservice` (300m) and the non-managed `recommendationservice` (500m, M3 G6 test) run with raised CPU limits (`golden_overrides`), so the baseline is not CPU-starved. The upstream `redis:alpine` tag floats, so `redis-cart` is pinned by digest to `redis:8.10.2-alpine` (the image it was already running). Details: `CLAUDE.md` §2 "Cluster tuning".

**Clock-sync topology (human-approved 2026-10-01).** A is the NTP server and B follows it through the Windows host's w32time. B's `time.time()` remains the timestamp source. Measured skew is about 7 ms. Setup and rationale: `CLAUDE.md` §2 "Clock-sync topology".

### 0.2 Pre-Experiment Freeze (Human-Approved 2026-10-09)

Every locked-file change made before this date is **pre-experiment tuning**, documented in `CLAUDE.md` §2 ("Cluster tuning", "Measurement point") and in the `CONTRACT-CHANGE` commits. That covers the frontend, currency and recommendationservice limits, the redis pin, cartservice thread-pool minimum, quoted `NULL`, the U_base band, and server-side SLA latency. From now on:

- **`config/contract.yaml` is frozen** once `feat/server-side-latency` is merged into `main`.
- **`config/calibration.json` is frozen** the moment `scripts/calibrate.py` writes it (G6-gated).
- **`eval/schedules/eval_v1.json` is frozen** the moment it is generated.
- Any further change is an **unfreeze**:
  - it needs explicit human approval, recorded as `UNFREEZE: <reason>` (in addition to `CONTRACT-CHANGE:`) in the commit message;
  - it gets an entry in `reports/rolling_project_log.md`;
  - it invalidates every run collected under the previous version for the affected comparisons; those runs are redone, never mixed.
- M3 fault smoke tests (G7) are the last point where an unfreeze is still expected, for example if a fault severity turns out undetectable. No unfreeze is allowed once M4 training starts.

**Unfreeze log:**
- **2026-10-09, `sla.latency_quantile: 0.95`** (SLA latency server-side p95 instead of p99). The server-side calibration failed G6 at CV 0.285, and a bootstrap showed p99 sampling noise of ~0.145 CV per tick. Superseded: ticks_steady fixture `e62bcb5`, calibration attempt `20261009T144632`.

### `config/cluster.env` (create first, fill in real values)

```bash
export A_IP=192.168.1.50                       # Machine A static LAN IP
export NS=boutique
export KUBE_ADMIN=$PWD/config/kube/admin.kubeconfig        # humans/bootstrap scripts only
export KUBE_AGENT=$PWD/config/kube/agent.kubeconfig        # env action executor only
export KUBE_CTRL=$PWD/config/kube/controller.kubeconfig    # fault injector + reset only
export PROM_URL=http://$A_IP:30090
export FRONTEND_URL=http://$A_IP:30080
export LOCUST_URL=http://127.0.0.1:8089
export OB_VERSION=vX.Y.Z                       # pin one Online Boutique release tag, never "main"
```

---

## 1. Timeline & Owners

| Milestone | Weeks | Lead | Support |
|---|---|---|---|
| M1 Cluster Baseline | 1 | Cloud Lead | Telemetry Lead |
| M2 Telemetry & Adversary | 2 | Telemetry Lead | Cloud Lead, Research Lead (Locust) |
| M3 Runbook & Gym Bridge | 3–4 | Telemetry Lead | AI Lead (env API), Research Lead (calibration, gates) |
| M4 PER-iSAC Integration | 5–6 | AI Lead | Whole team (run babysitting rota) |
| M5 Evaluation | 7–8 | Research Lead | Whole team (paper) |

---

## 2. Sample & Time Budget (Read Before M4)

Budget arithmetic:
- One episode ≈ 6–7 minutes including reset.
- At about 65% effective uptime, that is ≈ **140–150 episodes per day**.

| Phase | Episodes | Approx. wall-clock |
|---|---|---|
| M3 runbook validation (ε = 0) | 40 | ~0.3 day |
| M3 warm-start collection (ε = 0.25) | 120 | ~0.8 day |
| M4 SAC uniform, seeds 0/1/2 | 3 × 300 | ~6 days |
| M4 SAC PER, seeds 0/1/2 | 3 × 300 | ~6 days |
| **M4 SAC PER cold-start, seed 0 (mandatory ablation)** | 1 × 300 | ~2 days |
| M5 evaluation (10 policies × 50 episodes, interleaved) | 500 | ~3.5 days |

### Execution order for M4

Run the M4 jobs **interleaved**, so a paired comparison exists even if you run out of time:

```
uniform s0 → per s0 → per_cold s0 → uniform s1 → per s1 → uniform s2 → per s2
```

**Fallback (human decision 2026-10-09):** if wall-clock budget runs short, drop the seed-2 training runs (`uniform s2`, then `per s2`) to pay for the M5 evaluation budget. Never cut the evaluation's 50 episodes per policy or the cold-start pair.

The **cold-start ablation is mandatory**: `per_cold s0` is PER-iSAC with the same seed, contract and calibration as `per s0`, but no runbook warm-start (empty buffer, no offline phase). The pair (`per s0`, `per_cold s0`) is the minimum evidence for any claim that runbook warm-starting helps. PPO runs **only** if budget remains after the paired runs.

---

## 3. Repository Layout

The authoritative layout, with descriptions of each file, is in `CLAUDE.md` §3. Each milestone below lists the files it creates.

---

## Milestone 1 — The Cluster Baseline (Week 1)

### Objective

Have the following in place:
- K3s running headless on Machine A.
- Online Boutique deployed as a frozen **golden** baseline:
  - `loadgenerator` removed,
  - frontend exposed on NodePort `30080`,
  - every deployment at 1 replica.
- Least-privilege kubeconfigs (`agent`, `controller`) working from Machine B over the LAN.

### Files

| Path | Purpose |
|---|---|
| `config/cluster.env` | LAN addresses, kubeconfig paths, pinned versions |
| `.gitignore` | Must contain `config/kube/`, `data/`, `.venv/` |
| `k8s/k3s/install-server.sh` **[A]** | Swap off, chrony, K3s install with `--disable traefik --tls-san $A_IP` |
| `k8s/k3s/chrony-server.sh` **[A]** | chrony on A serves NTP to the hotspot subnet (clock-sync override, §0.1) |
| `scripts/w32time-follow-a.ps1` **[B, Windows]** | Windows host w32time follows A; WSL2 inherits it via PHC0 |
| `scripts/chrony-client.sh` **[B]** | chrony on B following A; passive A–B offset monitor under WSL2 (`-x`) |
| `k8s/boutique/upstream/kubernetes-manifests.yaml` | Unmodified upstream release manifest (pinned `OB_VERSION`) |
| `k8s/boutique/build_golden.py` | Strips `loadgenerator`; converts `frontend-external` to NodePort 30080; sets `replicas: 1`; applies limit overrides from `contract.yaml` |
| `k8s/boutique/golden.yaml` | Generated. **Never hand-edit.** |
| `k8s/boutique/namespace.yaml` | Namespace `boutique` with label/annotation `chaos-mesh.org/inject=enabled` (used in M2) |
| `k8s/rbac/agent-rbac.yaml` | ServiceAccount, Role, RoleBinding, and long-lived token Secret for `agent-sa` |
| `k8s/rbac/controller-rbac.yaml` | Same for `controller-sa` (fault injection + reset) |
| `k8s/rbac/make-kubeconfig.sh` | Builds a kubeconfig from a ServiceAccount token Secret |
| `scripts/snapshot_golden.py` | Captures the API-defaulted golden template and replicas → `config/golden_live.json` |
| `scripts/preflight.py` | Staged readiness checks (`--stage m1\|m2\|m3`) |
| `requirements.txt` | Pinned Python dependencies (CPU-only torch is installed separately) |
| `config/contract.yaml` | Locked parameters (content defined in `CLAUDE.md` §5.1) |

### Steps

**[A] Machine A, once:**

```bash
sudo apt-get update && sudo apt-get install -y chrony curl
sudo swapoff -a && sudo sed -i.bak '/\sswap\s/ s/^/#/' /etc/fstab
export A_IP=192.168.1.50
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="<pinned v1.xx.y+k3s1>" \
  INSTALL_K3S_EXEC="server --disable traefik --tls-san ${A_IP} --write-kubeconfig-mode 644" sh -
```

Notes for Machine A:
- Do **not** install NVIDIA drivers or the device plugin. The GTX 1650 plays no role in this project.
- Keep metrics-server enabled; it ships with K3s and the HPA baseline needs it.

**Machine B:**

```bash
source config/cluster.env
mkdir -p config/kube data
scp user@$A_IP:/etc/rancher/k3s/k3s.yaml $KUBE_ADMIN
sed -i "s/127.0.0.1/$A_IP/" $KUBE_ADMIN && chmod 600 $KUBE_ADMIN

curl -sSL -o k8s/boutique/upstream/kubernetes-manifests.yaml \
  https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${OB_VERSION}/release/kubernetes-manifests.yaml
python k8s/boutique/build_golden.py --upstream k8s/boutique/upstream/kubernetes-manifests.yaml \
  --contract config/contract.yaml --out k8s/boutique/golden.yaml

kubectl --kubeconfig $KUBE_ADMIN apply -f k8s/boutique/namespace.yaml
kubectl --kubeconfig $KUBE_ADMIN -n $NS apply -f k8s/boutique/golden.yaml
kubectl --kubeconfig $KUBE_ADMIN -n $NS wait --for=condition=available deploy --all --timeout=600s

kubectl --kubeconfig $KUBE_ADMIN apply -f k8s/rbac/
bash k8s/rbac/make-kubeconfig.sh agent-sa      $KUBE_AGENT
bash k8s/rbac/make-kubeconfig.sh controller-sa $KUBE_CTRL
python -m scripts.snapshot_golden            # → config/golden_live.json (commit this)

python3.11 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### Validate

```bash
kubectl --kubeconfig $KUBE_ADMIN get nodes -o wide                       # 1 node, Ready
kubectl --kubeconfig $KUBE_ADMIN -n $NS get deploy                        # 11 deployments (10 app services + redis-cart), all AVAILABLE, no loadgenerator
curl -s -o /dev/null -w "%{http_code}\n" $FRONTEND_URL/                   # 200
kubectl --kubeconfig $KUBE_AGENT auth can-i patch deployments -n $NS      # yes
kubectl --kubeconfig $KUBE_AGENT auth can-i patch deployments/scale -n $NS # yes
kubectl --kubeconfig $KUBE_AGENT auth can-i delete pods -n $NS            # no
kubectl --kubeconfig $KUBE_AGENT auth can-i list pods -n kube-system      # no
kubectl --kubeconfig $KUBE_CTRL  auth can-i create stresschaos.chaos-mesh.org -n $NS   # yes
chronyc tracking | grep "System time"                                     # on A: |offset| < 0.05 s
chronyc -h 127.0.0.1 -n sources                                          # on B: A (^*) offset < 50 ms (passive monitor under WSL2)
ssh user@$A_IP free -h                                                    # idle "used" < 5 GiB (design; < 10 GiB under §0.1)
python -m scripts.preflight --stage m1                                    # all PASS
```

### Exit Gate M1

- `preflight --stage m1` is all PASS.
- `config/golden_live.json` is committed.
- LAN round-trip time p99 is under 2 ms (`ping -c 200 $A_IP`). Under the §0.1 override: under 800 ms.

---

## Milestone 2 — Telemetry & Adversary (Week 2)

### Objective

Bring up three components:
- **Chaos Mesh**: running on K3s's containerd, restricted to namespace `boutique`.
- **Prometheus**: a minimal install scraping kubelet cAdvisor every 5 s, with a metric keep-list.
- **Locust**: running on B on pinned cores, serving the custom `/tick` window endpoint.

### Files

| Path | Purpose |
|---|---|
| `chaos/install-chaos-mesh.sh` | Helm install with containerd runtime, K3s socket, namespace filter, no dashboard |
| `chaos/smoke/stress-currency-60s.yaml` | 60 s CPU StressChaos on `currencyservice` (smoke test only) |
| `k8s/prometheus/prometheus-rbac.yaml` | SA + ClusterRole (`nodes`, `nodes/proxy`, `nodes/metrics`: get/list/watch) |
| `k8s/prometheus/prometheus-config.yaml` | ConfigMap: scrape 5 s, cAdvisor job, keep-list relabel (see `CLAUDE.md` §5.4) |
| `k8s/prometheus/prometheus-deploy.yaml` | Namespace `monitoring`, Deployment, memory limit 1.5Gi, retention 3d / 4GB, NodePort 30090 |
| `locust/locustfile.py` | Online Boutique task mix, `FastHttpUser`, 5 s timeouts, ring buffer, `/tick` route |
| `locust/run_locust.sh` | `taskset -c 0,1` launcher with web UI on 127.0.0.1:8089 and autostart |
| `scripts/promq.py` | CLI that runs the contract PromQL queries (`--all`, `--query cpu\|thr\|mem`) |
| `scripts/preflight.py` | Adds `--stage m2` checks |

### Steps

```bash
bash chaos/install-chaos-mesh.sh        # helm, KUBECONFIG=$KUBE_ADMIN (values in CLAUDE.md §8)
kubectl --kubeconfig $KUBE_ADMIN apply -f k8s/prometheus/
bash locust/run_locust.sh --users 50 --background
```

### Validate

```bash
# Chaos Mesh
kubectl --kubeconfig $KUBE_ADMIN -n chaos-mesh get pods                   # controller-manager + chaos-daemon Running
kubectl --kubeconfig $KUBE_ADMIN -n chaos-mesh logs ds/chaos-daemon | grep -iE "socket|no such file" | head   # empty

# Prometheus
curl -s "$PROM_URL/api/v1/targets" | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"'   # all "up"
curl -s "$PROM_URL/api/v1/status/tsdb" | jq '.data.headStats.numSeries'   # < 1000 (keep-list works)
python -m scripts.promq --all                                             # cpu/thr/mem rows for all 4 managed deployments, no NaN

# Locust /tick
sleep 60
curl -s "$LOCUST_URL/tick?from=$(( $(date +%s) - 20 ))&to=$(date +%s)" | jq   # n > 500, fail_ratio < 0.01

# Adversary smoke: CPU stress must be visible as throttling
kubectl --kubeconfig $KUBE_ADMIN apply -f chaos/smoke/stress-currency-60s.yaml
sleep 40 && python -m scripts.promq --query thr --deployment currencyservice   # >= 0.40
kubectl --kubeconfig $KUBE_ADMIN -n $NS delete stresschaos smoke-stress-currency

# Resource headroom under load
ssh user@$A_IP free -h                                                    # "used" < 8 GiB (design; < 12 GiB under §0.1)
kubectl --kubeconfig $KUBE_ADMIN top pod -n monitoring                    # prometheus < 1.2Gi
python -m scripts.preflight --stage m2                                    # all PASS (incl. only `boutique` annotated for chaos)
```

### Exit Gate M2

- All of the above pass.
- The StressChaos smoke test raises the currencyservice throttle ratio to ≥ 0.40 within 40 s.
- No CPU throttling appears on non-target services during the smoke test.

---

## Milestone 3 — Scripted Runbook & Gym Bridge (Weeks 3–4)

### Objective

Build the complete real-cluster Gymnasium environment:
- the 20 s fixed wall-clock tick,
- action masking with the global action lock,
- telemetry imputation,
- the fault injector for F1–F4 with ground-truth cure conditions,
- active-restore `reset()`.

Then validate it end-to-end with the **scripted runbook**. Calibrate the SLA, pass gates G1–G7, and collect the warm-start buffer. **No PyTorch in this milestone.**

### Files

| Path | Purpose |
|---|---|
| `env/contract.py` | Loads `contract.yaml` + `calibration.json` into frozen dataclasses |
| `env/clock.py` | `TickClock`: absolute monotonic schedule, boundary wall-times, overrun detection |
| `env/telemetry.py` | Contract PromQL, Locust `/tick` client, K8s status reads, concurrent collection with a hard deadline, imputation |
| `env/k8s_actions.py` | Action catalog, mask computation, single-worker async executor (agent client only) |
| `env/golden.py` | Loads `golden_live.json`; template hash ignoring `restartedAt` |
| `env/injector.py` | F1–F4 injection, cure checks, StressChaos cleanup, Locust `/swarm` surge (controller client only) |
| `env/reward.py` | Pure functions: `sla_violation`, `replica_surplus`, `reward`, `healthy`, `recovered` |
| `env/recorder.py` | Append-and-flush JSONL transition log per episode |
| `env/boutique_env.py` | `gymnasium.Env`: `Dict(obs=Box(36), mask=MultiBinary(12))`, `step`, `reset` |
| `agents/runbook.py` | Rules R0–R6 (`CLAUDE.md` §5.11), stateful, observation-only |
| `agents/run_policy.py` | Runs `noop\|random\|runbook\|checkpoint` policies for N episodes with optional ε |
| `scripts/record_ticks.py` | Records real ticks to `tests/fixtures/` for unit tests |
| `scripts/calibrate.py` | Searches `U_base` (frontend CPU ≈ 50–60% of its 400m limit), runs 30 min steady state, writes `config/calibration.json` |
| `scripts/fault_smoke.py` | Injects one fault, applies the known-correct action, verifies cure and recovery, resets |
| `scripts/gates.py` | Computes G1–G7 from run logs and prints PASS/FAIL |
| `scripts/watchdog.sh` | Restarts a crashed run from its last checkpoint/episode; alerts after 3 failures |
| `tests/test_normalize.py`, `test_imputation.py`, `test_reward.py`, `test_mask.py`, `test_runbook.py` | Pure-function tests on **recorded real** fixtures |

### Steps (in order)

1. **Telemetry layer and fixtures.**
   ```bash
   python -m scripts.record_ticks --n 30 --out tests/fixtures/ticks_steady.jsonl
   pytest tests/test_normalize.py tests/test_imputation.py -q
   ```
2. **Env contract check with a random valid policy.** No faults yet; injector disabled.
   ```bash
   python -m agents.run_policy --policy random --episodes 3 --no-faults --out data/m3/contract
   ```
3. **Calibration.**
   ```bash
   python -m scripts.calibrate --minutes 30
   ```
   This writes `L_SLA`, `RPS_base`, and `U_base`.

   **`U_base` definition (redefined 2026-10-02, human-approved).** `U_base` is the Locust user count at which the **frontend** runs at roughly **50–60 % CPU utilization relative to its 400m limit** before any fault, i.e. the contract `cpu_util[frontend]` feature (`Q_cpu[frontend] / (0.4 · replicas)`), averaged over steady ticks. It replaces the original target of ≈ 50 % of Machine A's host CPU: A has 16 cores, so that target (~8 cores) would push the user count far past what Online Boutique's 200–500m service limits can carry and crush the baseline. Anchoring on frontend keeps headroom for the F4 surge (2.5–3 × `U_base`), which is meant to saturate frontend, and is independent of the host size.
4. **Fault smoke tests.** Run each fault and target with its oracle remedy.
   ```bash
   python -m scripts.fault_smoke --fault F1 --severity 600ms
   python -m scripts.fault_smoke --fault F2 --target currencyservice --workers 2
   python -m scripts.fault_smoke --fault F2 --target cartservice --workers 2
   python -m scripts.fault_smoke --fault F3 --target cartservice
   python -m scripts.fault_smoke --fault F3 --target currencyservice
   python -m scripts.fault_smoke --fault F4 --multiplier 3.0 --check-bottleneck frontend
   ```
   **If the F4 bottleneck is not frontend:**
   1. Raise the `currencyservice` limit override in `contract.yaml`.
   2. Rebuild the golden manifest.
   3. Re-apply it.
   4. Re-snapshot `golden_live.json`.
   5. **Re-run calibration.**
5. **Runbook validation run**, then the gates.
   ```bash
   python -m agents.run_policy --policy runbook --episodes 40 --epsilon 0 --out data/m3/runbook_eval
   python -m scripts.gates --run data/m3/runbook_eval
   ```
6. **Warm-start collection.** Only after G1–G7 pass.
   ```bash
   nohup bash scripts/watchdog.sh python -m agents.run_policy --policy runbook --epsilon 0.25 \
     --episodes 120 --out data/warmstart > data/logs/warmstart.log 2>&1 &
   ```

### Validation Gates (computed by `scripts/gates.py`)

| Gate | Criterion |
|---|---|
| G1 Clock | p95 tick jitter < 1 s; p95 decision latency ≤ 4 s |
| G2 Telemetry | `telemetry_stale` on < 1% of ticks |
| G3 Faults | Runbook recovers ≥ 95% of F1–F4 episodes within 18 ticks |
| G4 Safety | Zero runbook actions in NULL episodes (FRR = 0) |
| G5 Reset | ≥ 98% of resets succeed without hard reset |
| G6 Signal | CV of steady-state tick SLA latency (server-side p95, `CLAUDE.md` §5.6) < 0.30 [override; design 0.25]; < 2% of NULL ticks breach the SLA |
| G7 Calibration | F4 bottleneck = frontend; every F2 severity yields throttle ≥ 0.40 |

### Validate

```bash
pytest tests/ -q
python -m scripts.preflight --stage m3
python -m scripts.gates --run data/m3/runbook_eval          # G1–G7 all PASS
python -m scripts.gates --run data/warmstart --only G1,G2,G5
ls data/warmstart/episodes/*.jsonl | wc -l                  # ≥ 120 valid episodes
```

### Exit Gate M3

- G1–G7 pass.
- `config/calibration.json` is committed, with its env git SHA.
- The warm-start dataset holds at least 120 valid episodes.

---

## Milestone 4 — PER-iSAC Integration (Weeks 5–6)

### Objective

Build a masked discrete SAC in Tianshou, warm-started from the runbook buffer, and train it in three configurations:
1. **uniform replay** (the PER ablation), 3 seeds;
2. **full PER**, 3 seeds;
3. **full PER, cold-start** (the warm-start ablation), **mandatory**, at least seed 0.

All runs follow the interleaved order and the fallback rule in §2.

### Files

| Path | Purpose |
|---|---|
| `agents/masked_discrete_sac.py` | Discrete SAC with masked logits, masked soft V(s′), per-state target entropy, α-loss filter (`CLAUDE.md` §5.12) |
| `agents/per_buffer.py` | Wrapper on Tianshou's prioritized buffer: mean-twin \|δ\| priorities, cap, β by gradient step, `source` tag |
| `agents/warmstart.py` | Loads JSONL transitions into a buffer; runs the 2,000-step offline phase |
| `agents/train.py` | Online loop on the real env: checkpoint every 25 episodes and on SIGTERM; resume; greedy probe every 75 episodes (5 episodes) |
| `agents/probe.py` | Greedy (argmax) policy evaluation on N episodes |
| `config/runs/*.yaml` | Per-run settings only (replay type, seed, episodes). Never overrides `contract.yaml` |
| `tests/test_masked_sac.py` | Masked actions get zero probability; no NaN from 0·log0; α-loss excludes `n_valid == 1`; masked V(s′) correct |
| `tests/test_per_buffer.py` | Priority cap and ε; β schedule; IS weights normalized by batch max |

### Steps

1. **Pin the Tianshou version** in `requirements.txt` and read the installed source of its discrete SAC policy and prioritized buffer **before** writing wrappers.
2. **Unit tests:**
   ```bash
   pytest tests/test_masked_sac.py tests/test_per_buffer.py -q
   ```
3. **Dry run**, then a resume test:
   ```bash
   python -m agents.train --config config/runs/dryrun.yaml --episodes 3
   python -m agents.train --resume data/checkpoints/dryrun/latest.pt --episodes 1
   ```
4. **Production runs** (interleaved order from §2), each under the watchdog:
   ```bash
   nohup bash scripts/watchdog.sh python -m agents.train --config config/runs/sac_uniform_s0.yaml \
     > data/logs/sac_uniform_s0.log 2>&1 &
   ```
5. **Monitor** in TensorBoard (`tensorboard --logdir data/runs --bind_all`):
   - rolling recovery rate,
   - rolling FRR on NULL episodes,
   - α,
   - policy entropy (states with ≥ 2 valid actions),
   - **Q-mean minus realized n-step return**,
   - PER max priority,
   - β,
   - tick jitter,
   - stale rate.

### Kill / Debug Criteria

**Stop a run and investigate** (reward, mask, telemetry) before spending more budget if any of these occur:
- After 150 episodes, the greedy probe is no better than the runbook-warm-started probe at episode 0.
- α leaves the range [1e-4, 1].
- The Q-gap grows monotonically for more than 50 episodes.
- FRR exceeds 10%.

The first response to high FRR is to raise action costs. **Raising action costs requires human approval** (it changes a locked parameter).

### Validate

```bash
pytest tests/ -q
python -m agents.probe --ckpt data/checkpoints/sac_per_s0/final.pt --episodes 10
python -m scripts.gates --run data/runs/sac_per_s0 --only G1,G2,G5
```

### Exit Gate M4

- At least the seed-0 and seed-1 pairs (uniform and PER) have completed with final checkpoints.
- The cold-start pair (`per s0`, `per_cold s0`) has completed with final checkpoints.
- Each PER run's greedy probe beats the NOOP baseline on every fault type, with FRR ≤ 5%.
- No unexplained α divergence occurred.

---

## Milestone 5 — Evaluation (Weeks 7–8)

### Objective

Measure every policy on one fixed, seeded fault schedule, on the same machines and calibration, using the greedy policy. Then produce statistically defensible MTTR and SLA comparisons.

**Protocol (human-approved 2026-10-09):**
- **50 episodes per policy:** 10 each of F1–F4 plus 10 NULL. This gives the per-fault Mann–Whitney tests and bootstrap CIs usable power.
- **Interleaved across policies.** Schedule episode *i* is run for every policy, in a seeded random policy order, before episode *i + 1*. Machine A is a shared host, so this keeps time-of-day and host-load drift from aligning with any one policy.

### Policies Under Test (10)

| Tag | What it is |
|---|---|
| `k8s_default` | NOOP policy (pure Kubernetes self-healing) |
| `k8s_hpa` | NOOP policy with `k8s/hpa/hpa-baseline.yaml` applied: frontend 1–3, cart 1–2, currency 1–2, 70% CPU |
| `runbook` | Scripted runbook, ε = 0 |
| `sac_uniform_s{0,1,2}` | Final checkpoints, greedy |
| `sac_per_s{0,1,2}` | Final checkpoints, greedy |
| `sac_per_cold_s0` | Cold-start ablation, final checkpoint, greedy |

### Files

| Path | Purpose |
|---|---|
| `k8s/hpa/hpa-baseline.yaml` | HPA objects for the `k8s_hpa` baseline only |
| `eval/make_schedule.py` | Fixed schedule: 10 episodes each of F1–F4 (targets and severities balanced) + 10 NULL = 50; seed 2026 |
| `eval/schedules/eval_v1.json` | Generated, committed, immutable |
| `eval/run_eval.py` | Runs one policy on given schedule episode indices; per-second Locust aggregates kept for fine-grained MTTR; records env git SHA. Agent/controller clients only, never admin or kubectl (`CLAUDE.md` §4.2) |
| `scripts/run_eval_interleaved.py` | Orchestrator: for each schedule episode, runs every policy in a seeded random order via `eval/run_eval.py`; applies `k8s/hpa/hpa-baseline.yaml` (admin kubeconfig) only around `k8s_hpa` episodes and deletes it after; resumable |
| `eval/metrics.py` | Recovery rate, MTTR (median/IQR, censored at 360 s), cumulative SLA penalty Σv, actions per episode, FRR, wasted-action rate |
| `eval/stats.py` | Mann–Whitney U per fault type with Holm correction across comparisons; effect sizes; bootstrap 95% CIs; Kaplan–Meier time-to-recovery |
| `eval/plots.py` | Figures (list below) |

### Steps

```bash
git tag eval-v1                                                           # freeze env code; run_eval refuses a dirty tree
python -m eval.make_schedule --seed 2026 --out eval/schedules/eval_v1.json
python -m scripts.gates --stage pre-eval                                  # re-check G1, G2, G6 against calibration

# Interleaved: episode i for all 10 policies (seeded random order) before episode i+1; HPA objects
# exist only around k8s_hpa episodes. Resumable under the watchdog.
nohup bash scripts/watchdog.sh python -m scripts.run_eval_interleaved \
  --schedule eval/schedules/eval_v1.json --order-seed 2026 \
  --policies k8s_default k8s_hpa runbook sac_uniform_s0 sac_per_s0 sac_per_cold_s0 \
             sac_uniform_s1 sac_per_s1 sac_uniform_s2 sac_per_s2 \
  > data/logs/eval_v1.log 2>&1 &

python -m eval.metrics --in data/eval --out data/eval/summary.csv
python -m eval.stats   --in data/eval --out data/eval/stats.md
python -m eval.plots   --in data/eval --out figures/
```

### Required Figures

1. MTTR box plots per fault type × policy.
2. Kaplan–Meier time-to-recovery curves per policy (handles non-recovered episodes honestly).
3. Recovery rate per fault type × policy.
4. Cumulative SLA penalty Σv per episode × policy.
5. Learning curves: rolling return and recovery rate vs. episode, mean ± sd over seeds, uniform vs. PER.
6. Fault × action frequency heat map for `sac_per` vs. the outcome grid in `CLAUDE.md` §5.2. This is the interpretability figure.
7. FRR and wasted-action rate per policy.

### Validate

```bash
python -m eval.metrics --check                # 10 tags × 50 episodes present (fewer only if seed-2 runs were dropped), all with the same schedule hash, env SHA and calibration hash
ls figures/                                   # all 7 figures present
```

### Exit Gate M5

- `summary.csv`, `stats.md`, and all figures exist.
- Every result row carries the schedule hash, env git SHA, and calibration hash.
- Claims in the paper are limited to comparisons with p < 0.05 or explicit CIs.
- Ties with the runbook are reported as ties.

---

## 4. Risk Register

| Risk | Early signal | Mitigation |
|---|---|---|
| A runs out of memory under StressChaos or surge | `free -h` used > 9 GiB; OOMKilled outside target | Lower `U_base`; tighten the Prometheus keep-list; never raise service limits beyond the contract |
| B CPU contention inflates P99 | G6 fails during training but passes when idle | Verify `taskset`; reduce `torch.set_num_threads`; cap updates per tick |
| Tianshou API mismatch | Wrappers fight internals | Subclass and override `learn`; an in-house SAC using Tianshou buffers is allowed **only with human approval** |
| RL only ties the runbook | M4 probes ≈ runbook | Report honestly; Phase-2 compound faults (below) create headroom |
| Wall-clock overrun | M4 behind by > 2 days | Drop seed 2 runs (uniform first); never cut evaluation episodes |
| Silent telemetry rot | `stale` rate creeping up | G2 is checked daily from training logs; pause and fix |

---

## 5. Phase-2 Stretch (Only After M5 Data Is Safe)

1. **Compound faults:** F2 + F4, or a bad deploy that throttles CPU (indistinguishable from F2 except via `rollout_recency`).
2. **Linkerd** for per-service latency, which re-enables localizable NetworkChaos faults.
3. **PPO baseline** under the identical contract and sample budget, reported with its budget.
4. **Cold-start PER-iSAC** (no warm-start) as an ablation.
