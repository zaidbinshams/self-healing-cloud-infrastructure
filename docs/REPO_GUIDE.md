# Repository Guide — Start Here

This is the map of the repository. For every folder it says:
- what is in it;
- why it exists;
- who on the team uses it, and how;
- what must never be changed by hand.

The other documents explain the *ideas* (`docs/PROJECT_EXPLAINER.md`) and the *code* (`docs/CODE_EXPLAINER.md`). This one tells you where things are and what to do with them.

**Status, 25 Oct 2026.** Every experiment is finished and analysed. What remains is writing Paper A and Paper B, and the final review. Nobody needs the cluster for that: everything the team needs is committed, in plain files.

---

## 1. Where to start, by task

| You want to… | Read / open | Then |
|---|---|---|
| Understand the project (new teammate, first hour) | `README.md`, then `docs/PROJECT_EXPLAINER.md` §1–§8 | skim §4 of this guide |
| Write **Paper A** (the RL agent) | `paper_a/README.md` | follow its reading order; numbers only from `paper_a/05_numbers.md` |
| Write **Paper B** (measuring the SLA signal) | `paper_b/README.md` | same, with `paper_b/05_numbers.md` |
| Prepare the **final review** | `presentation/README.md` (slides), `docs/FINAL_REVIEW_QA.md` (questions) | regenerate figures with one command (§5.3) |
| Check a number someone quoted | `paper_a/05_numbers.md` or `paper_b/05_numbers.md` | follow its source column to the CSV (§5.4) |
| Understand a piece of code | `docs/CODE_EXPLAINER.md` | open the file; `CLAUDE.md` has the rule it implements |
| Change code | §5.5 below, then `CLAUDE.md` §4 and §9 | tests and ruff must pass |
| Re-run anything on a cluster | `README.md` "Setup" and "Running", `PLAN.md` | ask the project lead first (§6) |

---

## 2. The map

```
.
├── README.md            public front page: what, results, setup, how to run
├── CLAUDE.md            technical specification: rules, contracts, tick protocol, every approved deviation
├── PLAN.md              the original milestone plan (M1–M5) with validation gates
├── LICENSE              MIT
├── requirements.txt     pinned Python dependencies (torch installed separately, CPU wheel)
├── pytest.ini           test settings: cluster tests excluded by default
├── .gitignore           keeps credentials and data/ out of git
│
├── config/              locked parameters, calibration, golden snapshot, cluster addresses
├── k8s/                 everything applied to Kubernetes: K3s install, app manifests, RBAC, Prometheus, HPA
├── chaos/               Chaos Mesh install and one smoke-test manifest
├── locust/              the load generator, with the custom /tick endpoint
│
├── env/                 the real-cluster Gymnasium environment (clock, telemetry, actions, faults, reward)
├── agents/              the RL agent (masked SAC + PER), the runbook, training and policy runners
├── eval/                evaluation runner, metrics, pre-registered statistics, analysis, exports, paper figures
├── scripts/             setup, checks, calibration, experiment chains, diagnostics, monitoring
├── tests/               196 unit tests for the pure logic; fixtures recorded from the real cluster
│
├── docs/                pre-registration, explainers, review Q&A, plan of action, this guide
├── reports/             milestone reports M1, M2 and the rolling project log
├── paper_a/             writing package for Paper A (numbers, CSVs, LaTeX, figures, guides)
├── paper_b/             writing package for Paper B (same structure)
├── presentation/        final-review slide figures R01–R17, their script, slide-by-slide guide
├── figures/             analysis.json for each paper: every computed result, machine-readable
│
└── data/                NOT IN GIT: raw logs, checkpoints, diagnostics (Machine B only, ~200 MB)
```

Three kinds of file live here. Knowing which kind a file is tells you whether you may edit it.

| Kind | Examples | May you edit it? |
|---|---|---|
| **Hand-written** | code in `env/`, `agents/`, `eval/`, `scripts/`; all `.md` docs; `main.tex` | yes, following §5.5 |
| **Locked** (changed only with written human approval and a `CONTRACT-CHANGE:` commit) | `config/contract.yaml`, `config/calibration.json`, `config/golden_live.json`, `eval/schedules/eval_v1.json` | **no** |
| **Generated** (rebuilt by a script; a hand edit is overwritten or, worse, silently wrong) | CSVs in `paper_*/figure_data/`, `figures/*/analysis.json`, every PNG/PDF figure, `k8s/boutique/golden.yaml`, `tests/fixtures/ticks_steady.jsonl` | **no**: rerun the script instead |

---

## 3. Folder by folder

### Top-level files

- **`README.md`.** The public front page that an examiner or a visitor sees first: the project in two paragraphs, the results table, setup and run commands. Keep it short; details belong in `docs/`.
- **`CLAUDE.md`.** The technical source of truth. It holds:
  - the architecture;
  - the hard rules: no simulator, replica limits, credentials, timeouts;
  - the contracts: observation, actions, PromQL, imputation, reward, tick protocol;
  - every human-approved deviation from the design, with its reason and date (§2).

  When a paper or a slide describes *how the system works*, this is where to check it. Despite its name it is not about the AI assistant: it was the specification the coding assistant was held to.
- **`PLAN.md`.** The milestone plan as written at the start: M1 cluster, M2 telemetry, M3 environment and runbook, M4 agent, M5 evaluation, each with its gates. It is a historical plan. Where reality differs (in-house SAC, two seeds, HPA definition), the change is recorded in `CLAUDE.md` or `docs/PREREGISTRATION.md`, not in `PLAN.md` (see §7).
- **`requirements.txt`**, **`pytest.ini`.**
  - All dependencies are pinned; never upgrade them.
  - Torch is installed separately as the CPU wheel (instructions in the file header).
  - `pytest.ini` excludes tests marked `cluster`, so `pytest -q` never touches a cluster.

### `config/`: the numbers everything runs on

| File | What it is | Why it matters |
|---|---|---|
| `contract.yaml` | **Locked.** Every parameter: tick 20 s, deadlines, replica limits, fault mix, reward weights, SAC/PER hyper-parameters, runbook thresholds | No number is hard-coded anywhere else; the papers' hyper-parameter table comes from here |
| `calibration.json` | **Locked, generated** by `scripts/calibrate.py`: `l_sla_ms` 70, `u_base` 30 users, `rps_base` 34.0 | Defines "healthy" (L_SLA) and the baseline load |
| `golden_live.json` | **Locked, generated** by `scripts/snapshot_golden.py`: the known-good state of every deployment, as the API returns it | RESTORE and `reset()` restore exactly this |
| `cluster.env` | LAN IPs, URLs, kubeconfig paths, pinned versions (K3s v1.36.4+k3s1, Online Boutique v0.10.7, Chaos Mesh 2.8.4) | `source` it before any cluster command |
| `runs/edge_degraded.env` | The degraded-link condition: +150 ms delay, 1 % loss, no jitter, with the reasons | Used by `scripts/netem.sh` for the edge experiment (H5) |

`config/kube/` (the three kubeconfigs) is gitignored: credentials never go into git.

### `k8s/`: what runs on the cluster

| Path | What it is |
|---|---|
| `k3s/install-server.sh` | Installs K3s on Machine A (run once on A) |
| `k3s/chrony-server.sh` | Makes A the time server that B follows (clock sync) |
| `k3s/kubelet-housekeeping.sh`, `k3s/kubelet-logsize.sh` | Two approved kubelet tunings: 5 s cAdvisor refresh, 200 MiB log rotation (`CLAUDE.md` §2 "Cluster tuning") |
| `boutique/upstream/kubernetes-manifests.yaml` | The pinned upstream Online Boutique release, unmodified |
| `boutique/build_golden.py` → `boutique/golden.yaml` | Builds the deployed manifest from upstream: removes `loadgenerator` and applies the approved `golden_overrides` from `contract.yaml` (CPU limits, the cartservice thread-pool setting, the digest-pinned redis image). `golden.yaml` is generated |
| `boutique/namespace.yaml` | The `boutique` namespace |
| `boutique/snapshot-base.json` | Identical copy of `config/golden_live.json`, written by the same script. It is kept next to the manifests on purpose |
| `rbac/agent-rbac.yaml`, `rbac/controller-rbac.yaml`, `rbac/make-kubeconfig.sh` | The two least-privilege credentials, both limited to the `boutique` namespace. The agent may read pods and logs and patch or scale deployments; the code further limits it to the four managed ones. The controller may also delete pods, create and delete StressChaos (faults) and HPA objects (HPA baseline only) |
| `prometheus/*.yaml` | Prometheus with a keep-list of four cAdvisor metrics, 5 s scrape, NodePort 30090 |
| `hpa/hpa-baseline.yaml` | The HPA objects, used **only** for the Kubernetes + HPA baseline (60 % of each pod's CPU limit) |

### `chaos/` and `locust/`

- **`chaos/`.**
  - `install-chaos-mesh.sh` installs Chaos Mesh with the K3s socket settings. Without them it silently does nothing (`CLAUDE.md` §8.1).
  - `smoke/stress-currency-60s.yaml` is a one-off smoke test. The faults used in episodes are built in code (`env/injector.py`), not from manifests.
- **`locust/`.**
  - `locustfile.py` generates the shop traffic. It adds a `/tick` endpoint that returns request count, failures and latency for an exact time window, and it sends `Connection: close` (one connection per request, `CLAUDE.md` §2).
  - `run_locust.sh` starts it pinned to CPU cores 0–1, so training never starves the load generator.

### `env/`: the environment (the heart of the system)

| File | Job |
|---|---|
| `contract.py` | Loads and validates `contract.yaml` and `calibration.json`; refuses to run on a stale calibration |
| `clock.py` | The fixed 20 s tick schedule on Machine B (`sleep_until`, never a fixed sleep) |
| `telemetry.py` | Collects all sources in parallel under a 3 s deadline: Prometheus, Kubernetes API, Locust, frontend logs. Then imputation (§5.5) and normalisation into the 36 observation values |
| `k8s_actions.py` | The 12 actions, the action mask, the one-action lock, and the executor that calls the Kubernetes API |
| `golden.py` | Compares live deployments with the golden snapshot; builds the RESTORE patch |
| `injector.py` | Injects faults F1–F4 and checks their cure conditions (controller credential only) |
| `reward.py` | Reward, health and recovery: pure functions |
| `recorder.py` | Writes one JSON line per tick, flushed every tick |
| `boutique_env.py` | Ties it together: `reset()` (restore, settle, plan the episode) and `step()` (the tick protocol) |

`docs/CODE_EXPLAINER.md` §3–§12 walks through these with the key lines.

### `agents/`: the policies

| File | Job |
|---|---|
| `masked_discrete_sac.py` | The agent: masked discrete Soft Actor-Critic, with the BC + CQL warm-start losses and the fixed α of the online phase |
| `per_buffer.py` | n-step returns and prioritized experience replay (sum tree); stale next-states are never stored |
| `warmstart.py` | Loads the runbook's recorded transitions and runs the offline phase |
| `train.py` | Online training on the live cluster, with checkpoints, resume, and greedy probes (`--probe-every`) |
| `runbook.py` | The scripted SRE runbook (rules R0–R6), seeing only what the agent sees |
| `run_policy.py` | Runs a fixed policy (runbook, random, noop) for N episodes: warm-start data and validation |

### `eval/`: from episodes to statistics to figures

| File | Job | Needs |
|---|---|---|
| `make_schedule.py` → `schedules/eval_v1.json` | Built the fixed evaluation schedule (seed 2026). **Locked**: the schedule is never regenerated | — |
| `run_eval.py` | Runs every policy on each schedule entry in a random per-entry order (interleaving), re-runs invalid episodes | cluster |
| `metrics.py` | Per-episode MTTR, SLA penalty, actions: pure | — |
| `stats.py` | The pre-registered tests (Mann–Whitney + Holm, Cliff's δ, bootstrap CIs, non-inferiority): pure | — |
| `analyze.py` | Runs H1–H5 and the seed-1 replication → `data/eval/analysis.json` (copy: `figures/paperA/analysis.json`) | `data/` |
| `papera_export.py` | Writes `paper_a/figure_data/*.csv` from the analysis and the runs | `data/` |
| `plots.py` | Paper A figures from the raw analysis → `paper_a/latex/figures/` | `data/` |
| `paperb_analysis.py` | Paper B's analyses → `data/paperB/analysis.json` (copy: `figures/paperB/analysis.json`) | `data/` |
| `paperb_export.py` | Writes `paper_b/figure_data/*.csv` | `data/` |
| `paperb_figures.py` | Paper B figures from the raw analysis → `paper_b/latex/figures/` | `data/` |

**Two figure scripts per paper.**
- `eval/plots.py` and `eval/paperb_figures.py` draw from the raw data. They are the provenance: they prove that the figures come from the measurements.
- `paper_*/make_figures.py` draw the same figures from the committed CSVs, so the team can restyle them without `data/`.

Both draw the same data in the same design; the outputs differ only in font anti-aliasing. The CSVs themselves were re-exported from `data/` on 25 Oct and are identical to the committed ones.

### `scripts/`: operating the testbed

Grouped by when you would use them. The machine each runs on is noted where it is not Machine B.

| Group | Script | What it does |
|---|---|---|
| **One-time setup** | `chrony-client.sh` | B follows A's clock (passive monitor under WSL2) |
| | `w32time-follow-a.ps1` | Windows host of B: sync to A (administrator PowerShell) |
| | `install_netem_sudo.sh` | Lets the unattended edge run apply netem without a password prompt (a human runs it once) |
| | `snapshot_golden.py` | Captures `config/golden_live.json` after a verified clean apply |
| **Checks** (read-only) | `preflight.py` | Staged readiness checks (`--stage m1`, `m2`, …); never mutates the cluster |
| | `session_start.sh` | Start-of-session checks (cluster, clock skew, steady latency) before relaunching a run |
| | `steady_check.py` | Steady-state check against the frozen calibration |
| | `promq.py` | Runs the four contract PromQL queries once |
| | `status.sh` | One-shot status of a running chain |
| **Calibration and validation** | `calibrate.py` | Measures U_base, RPS_base, L_SLA → `config/calibration.json` (locked afterwards) |
| | `record_ticks.py` | Records real ticks as test fixtures, and the edge dry run |
| | `fault_smoke.py` | Injects one fault, applies its known cure, verifies recovery |
| | `gates.py` | Computes gates G1–G7 from run logs |
| **Experiments** (mutate the cluster) | `chain_m4.sh` | The full unattended pipeline: validation, warm-start data, training, main evaluation, edge stage |
| | `chain_seed1.sh` | Seed-1 replication: training and evaluation block |
| | `edge_stage.sh`, `netem.sh` | The degraded-link subset: apply netem, dry run, evaluate, remove netem |
| | `watchdog.sh` | Restarts a long run after a failure; stops after 3 in a row |
| **Diagnostics** (Paper B's evidence; read-only) | `latency_diag.py` | Tick-latency variation under steady load (the G6 investigation) |
| | `grpc_probe.py` | Probes the gRPC services directly during load |
| | `server_latency_poc.py` | Proof that server-side latency can be read every tick within the deadline |
| | `capture_server_requests.py` | Raw per-request latencies for the estimator analysis |
| **Reporting** | `training_report.py` | Interim training report from `data/` |
| | `_kube.py` | Shared helpers for the scripts above (not run directly) |

The diagnostic scripts are not clutter. They produced the measurements Paper B is built on, and its methods section refers to them.

### `tests/`

- **What runs.** 196 unit tests in 22 files run in about 10 seconds: `pytest -q`. They need no cluster.
- **What they cover.** The pure logic: normalisation, imputation, reward, mask, runbook rules, SAC and PER mathematics (`test_sac_per.py`), statistics, schedule, exports.
- **Fixtures.** `fixtures/ticks_steady.jsonl` holds real ticks recorded from the cluster by `scripts/record_ticks.py`. It is never synthetic.

Run `pytest -q` and `ruff check .` before every commit that touches code.

### `docs/`

| File | What it is | Who uses it |
|---|---|---|
| `REPO_GUIDE.md` | This map | everyone, first |
| `PROJECT_EXPLAINER.md` | The whole project in one narrative: problem, design, formulas, decisions, results, a 15-minute talk outline | everyone |
| `CODE_EXPLAINER.md` | The code folder by folder, with the key lines and why they are written that way; a syntax primer | anyone asked about the code |
| `FINAL_REVIEW_QA.md` | Every question an external examiner may ask, with answers, evidence and the weak spots to know (§15) | presenters |
| `PREREGISTRATION.md` | **The scientific record.** Hypotheses, protocol and statistics, committed before training; Amendments 1–8 with reasons; the completion report (§9) | paper writers (methods, threats to validity) |
| `PLAN_OF_ACTION.md` | The plan for the two papers agreed on 10 Oct, with a status note on top | background only |

Do not edit `PREREGISTRATION.md`. Its value is that it was written before the data. The completion report is its last entry.

### `reports/`

| File | What it is |
|---|---|
| `m1_report.md`, `m1_cluster_state.txt`, `m1_preflight.txt` | Milestone 1 (cluster baseline): report and raw evidence |
| `m2_report.md` | Milestone 2 (telemetry and fault injection) |
| `rolling_project_log.md` | Dated log of status and decisions up to the start of training (13 Oct). For later events it points to the pre-registration |

These are history. Use them for the "process" part of the final review (how the testbed was built and validated), not as a source of result numbers.

### `paper_a/` and `paper_b/`: the writing packages

Both have the same structure, so learning one teaches you the other.

| Item | Purpose |
|---|---|
| `README.md` | Start here: the paper in one paragraph, folder map, reading order, who writes what, how to compile |
| `01_story_and_claims.md` | The storyline, contributions, and what we may and may **not** claim |
| `02_section_guide.md` | Section by section: purpose, length, content points, which figure and which numbers |
| `03_figures_tables.md` | Every figure and table: what it shows, how to read it, the facts its caption must carry |
| `04_methodology_facts.md` | Testbed, versions, algorithms and settings, stated as facts for the method section |
| `05_numbers.md` | **Every number you may quote**, with an ID (N1, N2, …) and its source |
| `06_references.md` | Verified related work: what each paper did, and how ours differs |
| `07_format_and_submission.md` | Template, IEEE rules, anonymisation, AI-use disclosure, final checklist |
| `08_making_figures.md` + `make_figures.py` | Redraw every figure from the CSVs (one command), with labelling rules and check-values |
| `figure_data/` | The CSVs behind every figure and table (generated; never edit) |
| `latex/` | `main.tex` skeleton (compiles; tables already filled), `refs.bib`, `figures/` (the paper's PDFs and PNG previews) |
| `slides/` | The paper figures as 600-dpi PNGs, for a talk about one paper |

The ground rule in both packages: **the authors write every sentence.** The packages give facts, numbers and structure, not prose.

### `presentation/`: the final review

- `make_review_figures.py` draws 17 slide figures, R01–R17, from the same CSVs.
- `README.md` gives, for each figure: the slide order, a slide title that does not overclaim, what to say, the number IDs, and the source. It also has check-values and the colour conventions.
- `figures/` holds the PNG (slides) and PDF (vector) of each figure.

### `figures/`

- `paperA/analysis.json` is the complete output of `eval/analyze.py`: every test, CI and summary. `paperB/analysis.json` is the same for Paper B's analyses.
- They are the machine-readable source behind the `05_numbers.md` files; `paper_b/05_numbers.md` cites its file as "AJ".
- The figure images themselves live in `paper_*/latex/figures/` and `presentation/figures/`.

### `data/`: local only, not in git

On Machine B only, about 200 MB.

| Subfolder | Contents |
|---|---|
| `transitions/` | One JSONL file per episode, every tick: observation, action, reward, raw values |
| `m4/` | Training runs (checkpoints, per-episode logs, interim reports); `_discarded/` holds the four failed attempts, kept as evidence |
| `eval/` | The three evaluation blocks (main, edge, seed-1) and `analysis.json` |
| `m3/` | Runbook validation, fault smokes, steady checks |
| `calibration/`, `diag/`, `paperB/` | Calibration runs and the Paper B diagnosis runs |
| `logs/` | Event logs, chain logs, archived alerts |

Everything the papers and slides need has been exported from it into the committed CSVs. It is still the only copy of the raw evidence. **Keep a private backup** (a zip on a shared drive), and **never delete it or commit it**. A reviewer or the guide may ask for it as the artifact.

---

## 4. How the pieces connect

```
 Machine A (cluster)                    Machine B (controller)
 Online Boutique, Prometheus,  ◄─────── env/ + agents/ + Locust        one tick every 20 s
 Chaos Mesh, Kubernetes API             (train.py, run_eval.py, chain_*.sh)
                                                │
                                                ▼
                                 data/transitions/, data/m4/, data/eval/       raw evidence (local)
                                                │  eval/analyze.py, eval/paperb_analysis.py
                                                ▼
                                 figures/paperA|B/analysis.json                every computed result (in git)
                                                │  eval/papera_export.py, eval/paperb_export.py
                                                ▼
                                 paper_a|b/figure_data/*.csv                   the numbers, as tables (in git)
                       ┌────────────────────────┼─────────────────────────┐
                       ▼                        ▼                         ▼
           paper_*/05_numbers.md     paper_*/make_figures.py     presentation/make_review_figures.py
           (IDs for every number)    → paper_*/latex/figures/    → presentation/figures/
                       └────────────────────────┴─────────────────────────┘
                                                ▼
                                   the papers (Overleaf) and the review slides
```

**If two documents disagree, this order decides:**
1. `config/` for locked parameters.
2. The CSVs and `analysis.json` for results.
3. `paper_*/05_numbers.md`.
4. `docs/PREREGISTRATION.md` for what was planned, changed and run.
5. `CLAUDE.md` for how the system works.
6. Everything else (explainers, Q&A, reports, slides).

Report a disagreement rather than fixing it silently (§6).

---

## 5. Team workflows

### 5.1 Getting the repository

```
git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git
cd self-healing-cloud-infrastructure
git pull                       # every time before you start work
```

To read, write papers or make slides, you need nothing else. To run the tests, also:
```
python3.11 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
pytest -q && ruff check .
```

### 5.2 Writing a paper

1. Open `paper_a/README.md` or `paper_b/README.md`. Everyone reads `01_story_and_claims.md` first.
2. Create the Overleaf project from a zip of `paper_*/latex/` (steps in the paper's README). **The text is written in Overleaf, not in this repository.**
3. Quote a number only from `05_numbers.md`, and keep its ID in a comment, e.g. `% N10`, so the fact-check pass is quick.
4. For figures, use `latex/figures/*.pdf` as they are, or redraw them with `make_figures.py` after changing only its CONFIG block (`08_making_figures.md`).
5. Disclose AI assistance as described in `07_format_and_submission.md`.

### 5.3 Preparing the final review

```
pip install matplotlib numpy
python presentation/make_review_figures.py     # R01–R17 → presentation/figures/
```

- Slide order, titles and talking points: `presentation/README.md`. Questions and answers: `docs/FINAL_REVIEW_QA.md`. Fill in the team-roles table there (K1); it is left blank on purpose.
- For a talk about one paper only, use `paper_*/slides/` (or run `python paper_a/make_figures.py --slides`, and the same for paper_b).

### 5.4 Checking a number

Follow this path:
1. Find the number's ID in `paper_*/05_numbers.md`.
2. Follow its source column to the CSV in `paper_*/figure_data/`, and find the row.
3. For a statistic not in a CSV, look it up in `figures/paperA/analysis.json` (or paperB).

If a document shows a different value, the CSV wins (§4). Fix the document and say so in the commit message.

### 5.5 Changing something in git

- **Branches.** Small documentation fixes can go straight to `main` after `git pull`. Code changes go on a branch with a pull request that a teammate reads.
- **Commit messages.** Use Conventional Commits: `docs(paperB): …`, `fix(eval): …`, `feat(…)`. Write what changed and why.
- **Code changes** need `pytest -q` and `ruff check .` to pass. Anything under `env/` also needs a check on the real cluster (`CLAUDE.md` §9), so leave `env/` alone unless the project lead agrees.
- **Never commit:**
  - `data/`, `config/kube/` or any kubeconfig (`.gitignore` blocks them; don't force it);
  - passwords or tokens;
  - an edited generated or locked file (§2).
- **Never** force-push `main` or rewrite its history. The pre-registration's credibility rests on the commit timestamps.
- **The repository is public.** Anything you push can be read by anyone, including reviewers of a double-anonymous venue. Check `07_format_and_submission.md` before submitting.

---

## 6. Rules that are never broken

From `CLAUDE.md` §4, in one place:
1. **Real cluster only.** No simulator, no synthetic data, no fabricated or "filled-in" metric, figure or log line.
2. **Locked values stay locked.** No change to `config/contract.yaml`, `calibration.json`, `golden_live.json` or `eval/schedules/` without written approval and a `CONTRACT-CHANGE:` commit.
3. **Generated files are regenerated**, never edited. The data behind figures is never edited, rounded or filtered.
4. **Nothing touches the cluster without asking** the person responsible for it: no `kubectl apply/delete/scale`, training, evaluation or calibration run.
5. **Report results as obtained**, including the negative ones: HPA was faster on surges, H5 is not established, F2-currency was never learned, only two seeds, no uniform-replay ablation.
6. **The authors write the papers.** AI help with software, analysis and figures is disclosed.

If something looks inconsistent, raise it with the team before changing it. Most apparent contradictions have a recorded reason: `CLAUDE.md` §2 for deviations, the pre-registration amendments for protocol changes, and `docs/FINAL_REVIEW_QA.md` §15 for known weak spots.

---

## 7. Design documents vs the repository as built

`CLAUDE.md` §3 and `PLAN.md` show the layout as *designed*. A few names differ from what was built; nothing is missing.

| Named in the design | As built |
|---|---|
| `agents/probe.py` | Greedy probes are part of `agents/train.py` (`--probe-every`, `--probe-episodes`) |
| `config/runs/*.yaml` | One run setting was needed: `config/runs/edge_degraded.env` |
| `tests/test_masked_sac.py`, `tests/test_per_buffer.py` | Both are in `tests/test_sac_per.py` |
| `scripts/run_eval_interleaved.py` | Interleaving is built into `eval/run_eval.py` |
| `figures/` holding all figures (`PLAN.md` M5) | Figures live with their users: `paper_*/latex/figures/`, `presentation/figures/`; `figures/` keeps the analysis JSON |
| Tianshou, 3 seeds, HPA at 70 % CPU (`PLAN.md`) | In-house SAC (approved 10 Oct), 2 seeds, HPA at 60 % of the pod limit: see `docs/FINAL_REVIEW_QA.md` §15 |

---

## 8. Cleanup of 25 Oct 2026

The repository was swept for duplicates and contradictions before the final review.

**Removed.** Every number in the four documents was checked to be present in the paper packs. All of it remains in the git history.
- `docs/CAPSTONE_DEFENSE_NOTES.md`: early notes from 2 Oct with values that later changed (client p99, 40 users). Replaced by `PROJECT_EXPLAINER.md` and `FINAL_REVIEW_QA.md`.
- `docs/paperA_methods_pack.md`: the first draft of `paper_a/04_methodology_facts.md`.
- `docs/paperB_outline.md`, `docs/paperB_results.md`: the working files `paper_b/` was built from. They still named the dropped SEAMS target.
- `paper_b/latex/skeleton_preview.pdf`: compiled on 14 Oct, before the skeleton changed. Compile `main.tex` in Overleaf for a current preview.
- 18 figure images in `figures/paperA/` and `figures/paperB/`: copies of the files in `paper_*/latex/figures/`. The raw-data figure scripts now write there directly.

**Checked and found consistent.**
- The committed CSVs are identical to a fresh export from `data/`.
- Both `analysis.json` files are identical to the analysis outputs in `data/`.
- Results quoted across `README.md`, the explainers, the Q&A, the paper packs and the slides agree (recoveries, MTTRs, CIs, test statistics, decision latency, 196 tests).
- Every command in `README.md` matches the scripts' arguments.

To read a removed file:
```
git log --oneline -1 -- docs/paperB_results.md       # the commit that removed it
git show <that-commit>^:docs/paperB_results.md
```
