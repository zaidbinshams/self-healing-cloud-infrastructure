# Self-Healing Cloud Infrastructure

Online reinforcement learning for fault remediation on a live Kubernetes cluster.

An agent watches a running microservice application every 20 seconds and picks one of twelve repair actions: do nothing, restart a service, scale it, or restore it to its known-good template. The application is Google's Online Boutique on K3s. The agent is trained and evaluated on the real cluster, not in a simulator, and nothing in the results is simulated. Faults are injected with Chaos Mesh, through the Kubernetes API and with load surges. The controller runs on a second machine connected over Wi-Fi, so the control loop also has to cope with an unreliable network link.

The agent is a masked discrete Soft Actor-Critic with prioritized experience replay (PER-SAC). It is warm-started from runbook demonstrations using behaviour cloning and a conservative (CQL) critic, then trained online. It is compared with three baselines:
- default Kubernetes;
- Kubernetes with a Horizontal Pod Autoscaler (HPA);
- a scripted SRE runbook.

The comparison follows a protocol that was pre-registered before training (`docs/PREREGISTRATION.md`).

**Status (10 Nov 2026):**
- All experiments were completed on 25 Oct 2026.
- **Paper A** is drafted: 7 pages, IEEE conference format, title *Toward Self-Healing Cloud Infrastructure: Learning Guarded Fault Remediation Online on a Live Kubernetes Cluster*.
- **Paper B** (measurement design) and the final review are in progress.

**New to the repository?** Start with [`docs/REPO_GUIDE.md`](docs/REPO_GUIDE.md): what every folder holds, why, and how the team uses it.

## What is new

To our knowledge, no earlier work combines all of the following:
1. a closed-loop policy over **discrete repair actions** (restart, scale, restore);
2. **learned online on the live system** it repairs, with no simulator or digital twin;
3. driven by **telemetry alone**, with no fault diagnosis given as input;
4. with **hard guards** on every action (masks, a one-action-in-flight lock, replica bounds);
5. compared with native Kubernetes, HPA and a runbook under a **pre-registered** protocol.

We do **not** claim to be the first to apply RL to a real cluster, the first to use RL for remediation, a new warm-start method or a new guarded action interface. Paper A's Related Work explains how this work differs from FIRM, Sinan, AWARE, E2E-REME, ARBITER, GuardedAct and others.

## Testbed

| Part | What was used |
|---|---|
| Machine A (cluster) | One laptop: 16 CPUs, about 15 GB RAM, Ubuntu, swap off. Single-node K3s v1.36.4+k3s1 (Traefik disabled), Chaos Mesh, Prometheus (cAdvisor, 5 s scrape), metrics-server |
| Machine B (controller) | Windows with WSL2, Python 3.11, CPU-only PyTorch. Runs the environment, the agent, the Locust load generator, the injector and the evaluation. Timestamp authority for every measurement |
| Link | A Wi-Fi hotspot, not the wired LAN of the original design. A–B clock offset stayed within about 6–16 ms (gate: 200 ms) |
| Application | Online Boutique v0.10.7: 11 deployments after removing its load generator. The agent manages 4: `frontend`, `cartservice`, `currencyservice`, `productcatalogservice` |
| Load | Locust, 30 users (about 34 requests/s), 0.5–1.5 s think time, one TCP connection per request |
| SLA | Server-side p95 latency of the frontend ≤ 70 ms and failed requests ≤ 1 % |

Every deviation from the original design is documented and was approved before the data it affects. That covers the wireless link, the relaxed preflight thresholds, the CPU limits, the measurement point and the load model (`CLAUDE.md` §2 and `docs/PREREGISTRATION.md`).

### Faults

| Fault | Injection | Correct repair |
|---|---|---|
| F1 bad deploy | `EXTRA_LATENCY` of 300 ms, 600 ms or 1 s added to `productcatalogservice` | RESTORE `productcatalogservice` |
| F2 hot pod | Chaos Mesh CPU stress (1 or 2 workers) on `cartservice` or `currencyservice` | RESTART the target |
| F3 scaled to zero | `cartservice` or `currencyservice` set to 0 replicas | RESTORE (or SCALE_UP) the target |
| F4 traffic surge | Load × 2.5 or × 3.0 | SCALE_UP `frontend` |
| NULL | Nothing (fault-free control) | NOOP |

The agent never sees which fault was injected, its target, its severity or the injection time.

### Agent, actions and guards

- **Observation:** 36 features. Five are global (latency, failure ratio, request rate and their one-tick changes). Seven are per managed service (CPU, throttling, memory, readiness, replicas, restarts, rollout recency). Three describe the controller (lock held, ticks since the last action, stale telemetry).
- **Actions (12):**
  - NOOP;
  - RESTART × 4 (one per managed service);
  - SCALE_UP `frontend`, `cartservice` or `currencyservice`, and SCALE_DOWN `frontend`;
  - RESTORE `productcatalogservice`, `cartservice` or `currencyservice` to the golden template and base replicas.
- **Reward:** the negative of three terms: SLA violation, action cost and extra replicas. Range [−1.09, 0]. No MTTR shaping.
- **Guards:**
  - An action mask allows only actions that respect the replica limits (frontend 3, cart 2, currency 2, productcatalog 1) and the current state.
  - A global lock allows only one action in flight until its rollout completes.
  - The executor re-checks the bounds before every call.
  - The agent's credential is limited to one namespace and cannot delete pods. The executor accepts only the four managed deployments.
- **Loop:** a fixed 20 s tick. Telemetry is collected concurrently under a 3 s deadline, and actions are dispatched asynchronously. Missing telemetry is imputed by fixed rules, and a stale next state is never stored for training.

## Results

**Main evaluation.** 50 scheduled episodes per policy: 10 for each of four fault types, plus 10 without a fault. Policies run interleaved on each schedule entry. MTTR is the median over fault episodes; episodes that did not recover count as 360 s.

| Policy | Faults recovered | Median MTTR (s) | Actions in fault-free episodes |
|---|---|---|---|
| Kubernetes (default) | 0 / 40 | 360 | 0 / 10 |
| Kubernetes + HPA | 10 / 40 (all surges) | 360 | 0 / 10 |
| Scripted runbook | 38 / 40 | 79.6 | 0 / 10 |
| **PER-SAC, warm start** | **35 / 40** | **79.1** | **0 / 10** |
| PER-SAC, cold start (100 episodes) | 15 / 40 | 360 | 10 / 10 |

- **Against Kubernetes and HPA:** the warm agent recovers faster on F1–F3 (Holm-corrected p ≤ 0.03), and faster than Kubernetes on F4. **HPA was faster on traffic surges** (median 59.2 vs 59.7 s, p = 0.031), because it scales on CPU before the SLA is breached.
- **Against the runbook:** non-inferior on MTTR. The 95 % CI of the median difference is [−20.0, +0.9] s, inside the pre-registered 20 s margin. It is not superior (p = 0.41), and the SLA penalty is a tie.
- **Safety:**
  - no action in 0 of 10 fault-free evaluation episodes and 0 of 30 fault-free training episodes;
  - 0 mask violations, 0 executor refusals, 0 execution errors;
  - median decision latency 0.33 s (p95 0.89 s).
- **Warm start:** it is decisive. Without it, the agent recovers 15/40 and acts in every fault-free episode, always repeating RESTORE `cartservice`.
- **Second seed:** a second training seed recovers 35/40 and is also non-inferior to the runbook, which ran alongside it as a drift check. The runbook showed no drift between days (37/40 vs 38/40 recovered; medians 79.7 vs 79.6 s, p = 0.70).
- **Degraded link:** with an emulated degraded link (netem: +150 ms delay and 1 % loss on control-plane traffic), the medians barely moved (78.2 vs 78.5 s). With 16 episodes per policy, non-inferiority was **not established** (CI [−20.1, +20.2] s against a 20 s margin).
- **One fault/target pair was never learned:** F2 on `currencyservice` (0/5 in both seeds), even when latency clearly breached the SLA (119–237 ms).

Full results, with confidence intervals, test statistics and per-episode data, are in `paper_a/05_numbers.md` and `paper_a/figure_data/`.

## Limitations

- **Scale:** one single-node cluster on a laptop, one application, and a wireless link instead of a wired LAN.
- **Seeds and runs:**
  - two training seeds for the warm agent, sharing the same warm-start data;
  - one cold-start run of 100 episodes, half the warm agent's online budget;
  - 10 episodes per fault type.
- **Not tested:**
  - The uniform-replay ablation was not run, so we make no claim about prioritized replay itself.
  - Neither policy used SCALE_UP `cartservice` or `currencyservice`, so whether gRPC connection reuse limits their effect remains open.
- **Resolution:** MTTR is measured in 20 s steps (one tick).
- **Runbook bias:** we wrote the runbook ourselves, knowing the fault classes, which favours it.
- **Shared machine:** Machine A is a personal laptop that also runs other work. Every evaluation session started on AC power, with other workloads stopped and a passing 10-minute steady check.

## What building it taught us (Paper B)

The measurement and engineering work needed to make the loop trustworthy on this hardware is documented in Paper B, `CLAUDE.md` §2 and `reports/`:
- **Where latency is measured decides what the controller can see.** Client-side p99 tracked the Wi-Fi round-trip time (r = 0.95). The SLA is therefore measured at the server, from the frontend's own request logs. It uses p95 because, at about 900 requests per tick, p99 carries about 1.6 times the sampling noise of p95 (CV 0.145 vs 0.09).
- **Keep-alive connections pin load to one pod.** With them, a frontend scale-out left the new pods idle and surges were incurable by any policy. The load generator now opens one connection per request.
- **Limits can make a fault incurable.** At the upstream 200m CPU limit, `productcatalogservice` throttled during surges in a way no catalog action could relieve. Several CPU limits were raised (documented, with reasons).
- **Metric freshness.** With the default kubelet housekeeping interval, the newest cAdvisor sample was a median 12 s (p95 21 s) old when queried, and 8–46 % of live-edge `rate(...[30s])` queries came back empty. The interval is now 5 s.
- **Clock sync across Windows, WSL2 and Linux.** B follows A's NTP server through the Windows time service, with tuned update settings; with the defaults, B overshot A by about 0.6 s.

## Papers

| Folder | Paper | State |
|---|---|---|
| `paper_a/` | **Paper A:** *Toward Self-Healing Cloud Infrastructure: Learning Guarded Fault Remediation Online on a Live Kubernetes Cluster*. Method, pre-registered evaluation and results | Drafted; 7 pages, IEEE conference format; venue to be chosen with the guide |
| `paper_b/` | **Paper B:** where and how the SLA signal is measured, and how that decides which faults a self-healing controller can detect | Writing package ready; draft in progress |

Each folder is a writing package. It holds the numbers you may quote (`05_numbers.md`), the methodology facts, the figure data, verified references, a LaTeX template and a figure script.

## Repository layout

| Path | Contents |
|---|---|
| `env/` | Gymnasium environment for the real cluster: tick clock, telemetry, action executor, fault injector, reward, reset |
| `agents/` | Masked discrete SAC, PER buffer, scripted runbook, training and policy runners |
| `eval/` | Evaluation runner, metrics, pre-registered statistics, figures, data export |
| `eval/schedules/` | Fixed evaluation schedule (`eval_v1.json`, seed 2026) |
| `config/` | Locked parameters (`contract.yaml`), calibration, golden deployment snapshot, run settings |
| `k8s/` | K3s install, Online Boutique manifests, RBAC, Prometheus, HPA baseline |
| `chaos/`, `locust/` | Chaos Mesh install and smoke tests; load generator |
| `scripts/` | Preflight checks, calibration, fault smoke tests, session start, watchdog, experiment chains |
| `tests/` | Unit tests for the pure logic (no cluster needed) |
| `docs/` | Repository guide, project and code explainers, review Q&A, pre-registration with all amendments, plan of action |
| `reports/` | Milestone reports (M1, M2) and the rolling project log |
| `paper_a/`, `paper_b/` | Writing packages for the two papers: numbers, figure data, LaTeX templates, references, figure scripts, slide PNGs |
| `presentation/` | Slide figures R01–R17 for the final review, their script, and a slide-by-slide guide |
| `figures/` | `analysis.json` for each paper: the full machine-readable results behind every number |
| `data/` | Not in the repository (gitignored): raw episode logs, checkpoints and diagnostics, on Machine B only |

Documents:
- `docs/REPO_GUIDE.md`: the map of the repository and the team's workflows.
- `docs/PROJECT_EXPLAINER.md`: the whole project in one document (design, formulas, decisions, results, how to present it).
- `docs/CODE_EXPLAINER.md`: the source folder by folder, with the key lines of each file and why they are written that way.
- `docs/FINAL_REVIEW_QA.md`: the questions to expect at a review, with detailed answers.
- `docs/PREREGISTRATION.md`: hypotheses, metrics, schedule and analysis plan, with all eight dated amendments.
- `CLAUDE.md`: the technical specification (tick protocol, observation and action definitions, PromQL queries, imputation rules, safety constraints, approved deviations).
- `PLAN.md`: the milestones and their validation gates, as originally planned.

## Requirements

- **Machine A (cluster):** Linux, about 12 GB RAM or more, swap off. Runs K3s v1.36.4+k3s1, Online Boutique v0.10.7, Chaos Mesh and Prometheus.
- **Machine B (controller):** Linux or WSL2 with Python 3.11. Runs the environment, the agent, Locust and the evaluation. CPU only; no GPU is used.
- **Link:** the design assumes a wired LAN (RTT p99 < 2 ms). The reported results used a Wi-Fi hotspot, with the relaxed thresholds listed in `CLAUDE.md` §2.
- **Clocks:** the machines need synchronised clocks; B is the timestamp authority. Under WSL2, B follows A through the Windows time service (`scripts/w32time-follow-a.ps1`, `CLAUDE.md` §2).

## Setup

On Machine A:

```bash
A_IP=<static LAN IP> bash k8s/k3s/install-server.sh
```

On Machine B, from the repository root:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
source config/cluster.env   # edit the IPs and paths in this file first
```

Then follow Milestones 1 and 2 in `PLAN.md`:
- copy the admin kubeconfig from A;
- build and apply the golden Online Boutique manifest;
- create the agent and controller credentials (`k8s/rbac/`);
- snapshot the golden state;
- install Prometheus and Chaos Mesh;
- start Locust.

Each step has a validation command. `python -m scripts.preflight --stage m2` should pass before any experiment.

## Running the experiments

Every session starts with:

```bash
bash scripts/session_start.sh
```

This checks the cluster, the clock skew and steady-state latency against the frozen calibration.

The main steps are below. In the chain scripts each runs under `scripts/watchdog.sh` and can be resumed after an interruption.

```bash
# runbook demonstrations for the warm start (120 episodes, 25 % random valid actions)
python -m agents.run_policy --policy runbook --episodes 120 --epsilon 0.25 --seed 0 --out data/m4/warmstart_runbook

# training (200 episodes on the live cluster)
python -m agents.train --run sac_per_s0 --episodes 200 --seed 0 --warmstart data/transitions/warmstart_runbook
python -m agents.train --run sac_per_cold_s0 --episodes 100 --seed 0 --cold

# evaluation on the fixed schedule, policies interleaved per entry
python -m eval.run_eval --schedule eval/schedules/eval_v1.json --out data/eval/eval_v1_main \
  --policy k8s_default --policy k8s_hpa --policy runbook \
  --policy sac_per=data/m4/sac_per_s0/ckpt/final.pt --policy sac_per_cold=data/m4/sac_per_cold_s0/ckpt/final.pt
```

`scripts/chain_m4.sh` and `scripts/chain_seed1.sh` run the full sequence used for the reported results, including the edge-network subset (`scripts/edge_stage.sh`).

One episode takes 6–8 minutes, so a 200-episode training run takes roughly a day of cluster time.

## Analysis and figures

```bash
python -m eval.analyze --main data/eval/eval_v1_main --edge data/eval/eval_v1_edge \
  --train sac_per=data/m4/sac_per_s0 --train sac_per_cold=data/m4/sac_per_cold_s0 \
  --seed1 sac_per_s1=data/eval/eval_v1_seed1=data/m4/sac_per_s1 --out data/eval/analysis.json
python -m eval.papera_export --analysis data/eval/analysis.json --out paper_a/figure_data
```

The raw episode logs (`data/`) are not in the repository. The derived per-episode results needed for every table and figure are in `paper_a/figure_data/` and `paper_b/figure_data/`. The figures can be redrawn from those files alone:

```bash
pip install matplotlib numpy
python paper_a/make_figures.py            # Paper A figures (PDF + PNG) into paper_a/latex/figures/
python paper_a/make_figures.py --slides   # 600-dpi PNGs into paper_a/slides/
python paper_b/make_figures.py            # Paper B figures (add --slides for 600-dpi PNGs)
```

The final-review slide figures are drawn the same way: `python presentation/make_review_figures.py` (guide: `presentation/README.md`).

## Tests

```bash
pytest -q
ruff check .
```

The unit tests cover the pure parts of the system:
- normalisation and imputation;
- the reward and the action mask;
- the runbook rules;
- the SAC and PER mathematics;
- the statistics.

They use fixtures recorded from the real cluster and need no cluster access. Tests that touch the cluster are marked `cluster` and are not run by default.

## Notes on reproducing

The code, configuration, evaluation schedule and analysis are complete, but cluster behaviour is not deterministic: the same seed on different hardware will not give identical numbers. `docs/PREREGISTRATION.md` records the environment used for the reported results, including the deviations from the original design (wireless link, CPU limits, measurement changes) and why each was made. Expect results of the same shape, not the same values.

## Citing

TBA (after publication).

## License

MIT. See `LICENSE`.
