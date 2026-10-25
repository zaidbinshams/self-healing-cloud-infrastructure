# Self-Healing Cloud Infrastructure

Online reinforcement learning for fault remediation on a live Kubernetes cluster.

An agent observes a microservice application (Google's Online Boutique on K3s) every 20 seconds and chooses one of twelve remediation actions: do nothing, restart, scale up or down, or restore a deployment to its known-good template. It is trained and evaluated on the real cluster, not in a simulator. Faults are injected with Chaos Mesh and through the Kubernetes API. The controller runs on a second machine connected over Wi-Fi, so the control loop also has to cope with an unreliable network link.

The agent is a masked discrete Soft Actor-Critic with prioritized experience replay. It is warm-started from runbook demonstrations using behaviour cloning and a conservative (CQL) critic. It is compared with default Kubernetes, Kubernetes with a Horizontal Pod Autoscaler, and a scripted SRE runbook, under a protocol that was pre-registered before training (`docs/PREREGISTRATION.md`).

## Results

Main evaluation: 50 scheduled episodes per policy (10 for each of four fault types, plus 10 without a fault). MTTR is the median over fault episodes; episodes that did not recover count as 360 s.

| Policy | Faults recovered | Median MTTR (s) | Actions in fault-free episodes |
|---|---|---|---|
| Kubernetes (default) | 0 / 40 | 360 | 0 / 10 |
| Kubernetes + HPA | 10 / 40 | 360 | 0 / 10 |
| Scripted runbook | 38 / 40 | 80 | 0 / 10 |
| PER-SAC, warm start | 35 / 40 | 79 | 0 / 10 |
| PER-SAC, cold start | 15 / 40 | 360 | 10 / 10 |

- The warm-started agent recovers faster than Kubernetes and HPA on three of the four fault types. On traffic surges, HPA was slightly faster.
- Its MTTR is within the pre-registered 20 s non-inferiority margin of the runbook.
- Without the warm start, the agent did not learn a usable policy within 100 episodes.
- A second training seed gave the same picture.
- Under an emulated degraded link (150 ms delay, 1 % loss), the medians were almost unchanged. The 16 episodes per policy were not enough to establish non-inferiority within the margin.
- One fault (CPU stress on `currencyservice`) was never learned by either seed.

Full results with confidence intervals, test statistics and per-episode data are in `paper_a/05_numbers.md` and `paper_a/figure_data/`.

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
| `docs/` | Pre-registration with all amendments, plan of action |
| `paper_a/`, `paper_b/` | Writing packages for the two papers: numbers, figure data, LaTeX skeletons, references |
| `figures/` | Generated figures |

`CLAUDE.md` is the technical specification: the tick protocol, observation and action definitions, PromQL queries, imputation rules and safety constraints. `PLAN.md` lists the milestones and their validation gates.

## Requirements

- **Machine A (cluster):** Linux, about 12 GB RAM or more, swap off. Runs K3s v1.36.4+k3s1, Online Boutique v0.10.7, Chaos Mesh and Prometheus.
- **Machine B (controller):** Linux or WSL2 with Python 3.11. Runs the environment, the agent, Locust and the evaluation. CPU only; no GPU is used.
- The two machines need a LAN connection and synchronised clocks (B is the timestamp authority; see `CLAUDE.md` §2).

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

Then follow Milestone 1 and 2 in `PLAN.md`:
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

The main steps (each runs under `scripts/watchdog.sh` in the chain scripts, and can be resumed after an interruption):

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

The raw episode logs (`data/`) are not in the repository. The derived per-episode results needed for every table and figure are in `paper_a/figure_data/`. The figures can be redrawn from them alone:

```bash
pip install matplotlib numpy
python paper_a/make_figures.py
```

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

A paper describing this work is in preparation. Until it is published, please cite the repository:

```bibtex
@misc{selfhealingcloud2026,
  title        = {Self-Healing Cloud Infrastructure: Online Reinforcement Learning for Fault Remediation on a Live Kubernetes Cluster},
  year         = {2026},
  howpublished = {\url{https://github.com/zaidbinshams/self-healing-cloud-infrastructure}}
}
```

## License

MIT. See `LICENSE`.
