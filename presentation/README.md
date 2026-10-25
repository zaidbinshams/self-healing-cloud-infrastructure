# Final Review Presentation — Figures

Seventeen slide-ready figures for the final capstone review, numbered **R01–R17**. Every one is drawn by `make_review_figures.py`:
- the charts come from the committed CSVs in `paper_a/figure_data/` and `paper_b/figure_data/`;
- the diagrams and tables (R01–R03, R08, R11, R16, R17) take their facts from the documents named in the table below.

The PNGs and PDFs in `figures/` are the reference copies. You can regenerate all of them in under a minute, without the cluster, the raw `data/` folder or the project environment.

These figures are for the **review slides**. The paper figures are separate:
- `paper_a/make_figures.py` and `paper_a/08_making_figures.md`;
- `paper_b/make_figures.py` and `paper_b/08_making_figures.md`.

Their `--slides` versions (`paper_a/slides/`, `paper_b/slides/`) are column-sized and suit a paper talk. The R-figures are drawn at slide size (12 × 5.6 in, 15 pt text) and suit the review deck.

---

## 1. Regenerate

### On your laptop
```
git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git
cd self-healing-cloud-infrastructure
python -m pip install matplotlib numpy        # Python 3.9 or newer
python presentation/make_review_figures.py
```
This writes `presentation/figures/RNN_<name>.png` (200 dpi, 2400 px wide, for slides) and `.pdf` (vector) for each figure, overwriting the shipped ones.

### Without installing anything (Google Colab)
1. Open colab.research.google.com and create a new notebook.
2. `!git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git`
3. `!python self-healing-cloud-infrastructure/presentation/make_review_figures.py`
4. Download from `self-healing-cloud-infrastructure/presentation/figures/` in the Files panel.

### Only one figure
```
python -c "import sys; sys.path.insert(0, 'presentation'); import make_review_figures as m; m.r07_km()"
```
The function names are in the table below.

### Putting them on slides
- **Format.** PowerPoint, Keynote and Google Slides: insert the PNG. PowerPoint and Keynote also take the PDF (vector, sharp at any size).
- **Size and layout.** Fill the slide width under the title. The figures are 16:9-friendly and have no title inside them; the slide title carries the message.
- **Never** screenshot a PDF, stretch a figure in one direction, or retype numbers from memory. Take numbers from §3.

---

## 2. The figures, in suggested slide order

The order follows the 15-minute talk outline in `docs/PROJECT_EXPLAINER.md` §16.
- "Number IDs" refer to `paper_a/05_numbers.md` (A-…) and `paper_b/05_numbers.md` (B-…).
- Say those numbers, not numbers read off the chart.

| Slide | Figure (file, function) | Suggested slide title | What to say (one or two sentences) | Number IDs | Source |
|---|---|---|---|---|---|
| Problem | **R03** `R03_faults_and_cures` · `r03_faults` | "Four faults Kubernetes does not heal" | Each fault leaves pods running, so Kubernetes sees nothing wrong; each has one correct action. NULL episodes test for false alarms. | — | CLAUDE.md §5.10, §5.2 grid; `config/contract.yaml` `episode` |
| Setting | **R01** `R01_architecture` · `r01_architecture` | "A real cluster, controlled over Wi-Fi" | Machine A runs the cluster; Machine B runs load, telemetry, the policy and the fault injector. The two credentials are least privilege. | A-N23c (30 users) | CLAUDE.md §2 |
| The loop | **R02** `R02_control_loop` · `r02_tick` | "One decision every 20 seconds" | Measure for 20 s, collect within a 3 s deadline, decide, dispatch without waiting; learning gets 1 s between ticks. | A-N22 | CLAUDE.md §5.7; `contract.yaml` `clock` |
| Measuring right | **R14** `R14_client_vs_server_latency` · `r14_paperb_timeseries` | "Client latency tracks the Wi-Fi; server latency does not" | The orange client line spikes exactly when ping (green) spikes; the blue server line stays flat. So the SLA signal is measured server-side. | B-N11–N13, B-N17 | `paper_b/figure_data/fig_b2_timeseries_treatmentA.csv` |
| Measuring right | **R15** `R15_what_each_signal_detects` · `r15_paperb_detect` | "Only the server-side signal sees surges and hot pods" | Right of the line is "detected". Client-side p99 missed all four of these smokes; server-side p95 caught all four. (F1 and F3 are caught by both, so don't say the client never detects.) | B-N44–N47 | `paper_b/figure_data/fig_b4_detectability.csv` |
| Result 1 | **R04** `R04_faults_recovered` · `r04_recovered` | "The agent recovers 35 of 40 faults" | Kubernetes 0, HPA 10 (all surges), runbook 38, agent 35, cold agent 15. | A-N1–N4 | `paper_a/figure_data/summary_main.csv` |
| Result 1 | **R05** `R05_recovery_by_fault` · `r05_matrix` | "Where each policy succeeds" | Darker means more recovered. The agent's 5 misses are all one fault: F2 on currencyservice. | A-N2–N4, A-N7 | `summary_main.csv` |
| Result 2 | **R07** `R07_time_to_recovery` · `r07_km` | "As fast as the runbook, far faster than Kubernetes" | The fraction still broken over time. Agent and runbook drop together (log-rank p 0.95); HPA and Kubernetes stay high. | A-N6, A-N10, A-N19b | `paper_a/figure_data/km_curves.csv` |
| Result 2 | **R06** `R06_mttr_by_fault` · `r06_mttr` | "Recovery time per fault" | Agent faster on F1 and F4, slower on F2; pooled medians 79.1 vs 79.6 s. Not-recovered episodes count as 360 s. | A-N5, A-N6 | `summary_main.csv` |
| What it learned | **R08** `R08_actions_learned` · `r08_actions` | "The agent found the right action for each fault without being told the fault" | Read across a row: runbook and agent pick the same cure in every row except F2-currency, where the agent did nothing; the cold agent acts many times. | A-N13, A-N16 | `paper_a/figure_data/actions_main.csv` |
| Warm start | **R09** `R09_warm_vs_cold_training` · `r09_training` | "The runbook warm start is essential" | Both warm seeds start near their final level; the cold run stays far below for its whole 100 episodes. | A-N18, A-N28 | `paper_a/figure_data/training.csv` |
| Warm start | **R10** `R10_false_alarms` · `r10_fault_free` | "No false alarms, except the cold agent" | On fault-free episodes the warm agent did nothing, like the runbook; the cold agent acted in all 10. | A-N14, A-N16 | `summary_main.csv` (NULL rows) |
| Scorecard | **R11** `R11_hypotheses` · `r11_hypotheses` | "Pre-registered hypotheses: 2 supported, 2 partly, 1 not established" | Go row by row; read out the failures (HPA wins F4; H5 not established) yourself, before the examiner does. | A-N8–N11, A-N14, A-N15, A-N17–N20 | `docs/PREREGISTRATION.md` (hypotheses); `05_numbers.md` (evidence) |
| Robustness | **R12** `R12_robustness` · `r12_robustness` | "Same picture on a degraded link and a second seed" | Hollow = main result, filled = the extra block. Most medians move by one or two ticks; the large moves are runbook F2 on the degraded link (89 → 178 s) and agent F1 (59 → 99 s on the link, 59 → 90 s with seed 1). F2 stays the agent's slowest fault. | A-N21b, A-N24–N27 | `paper_a/figure_data/summary_edge.csv`, `summary_seed1.csv` |
| Practicality | **R13** `R13_decision_latency` · `r13_latency` | "Decisions take under a second" | p95 0.89 s against a 4 s target and a 20 s tick; 2.02 s under netem. | A-N22, A-N23 | `paper_a/figure_data/context.csv` |
| Process | **R16** `R16_plan_vs_delivered` · `r16_plan` | "Plan vs delivered" | Everything planned was done except the third seed and the uniform-replay ablation; three extras were added. | A-N28 | `PLAN.md`; `docs/PREREGISTRATION.md` (Amendments 6–8, "Not run") |
| Process | **R17** `R17_timeline_oct_2026` · `r17_timeline` | "October 2026, milestone by milestone" | The pre-registration was committed on 10 Oct, before any training run (its git timestamp is the registration time); all live-cluster runs are inside the shaded band. | — | `reports/rolling_project_log.md`; `docs/PREREGISTRATION.md` (dates) |

**If you have fewer slides,** keep R03, R01, R04, R07, R08, R09, R11 and R12; then R14 or R15 for the measurement story. Use R07 as the one summary figure (`docs/FINAL_REVIEW_QA.md` L3).

**Where honest findings go.** R11 already lists them. If you add an "Honest findings" slide (recommended, FINAL_REVIEW_QA L4), use:
- R06 for F2 being slow;
- R05 for the F2-currency misses;
- R11 for HPA on F4 and H5.

---

## 3. Check-values (does your figure show the right thing?)

After regenerating, or if you redraw a figure in another tool, check these against the figure. All come from the `05_numbers.md` files.

- **R04:** Kubernetes 0/40, HPA 10/40, runbook 38/40, PER-SAC warm 35/40, cold 15/40.
- **R05:**
  - PER-SAC F1 10, F2 5, F3 10, F4 10.
  - Runbook F1 10, F2 8, F3 10, F4 10.
  - Cold F1 0, F2 0, F3 5, F4 10.
  - HPA F4 10, all others 0.
  - Kubernetes 0 everywhere.
- **R06:** PER-SAC / runbook medians F1 59 / 80 s, F2 240 / 89 s, F3 60 / 50 s, F4 60 / 80 s.
- **R07:** curves level at 5/40 (PER-SAC), 2/40 (runbook), 25/40 (cold), 30/40 (HPA), 40/40 (Kubernetes) not recovered at 360 s.
- **R09:** warm runs end near −2 to −3; cold reaches about −16 at its worst; cold run stops at episode 100.
- **R10:** 0/10 for every policy except cold (10/10).
- **R12:**
  - Degraded link, PER-SAC / runbook: F1 99 / 78, F2 229 / 178, F3 49 / 59, F4 69 / 69 s.
  - Seed-1 PER-SAC recovered 35/40.
- **R13:** main median 0.33 s, p95 0.89 s; netem median 1.44 s, p95 2.02 s.
- **R14:** server p99 within 63–119 ms; client spikes up to 577 ms line up with ping spikes up to 305 ms.
- **R15:** server 2.83, 2.11, 1.10, 1.01; client 0.65, 0.39, 0.27, 0.71.

If anything differs, check that `paper_a/figure_data/` and `paper_b/figure_data/` are unmodified (`git status`), then ask before changing anything.

---

## 4. Conventions (the same in every figure)

| Thing | Encoding |
|---|---|
| PER-SAC (warm) | blue `#2a78d6`, solid line, circle |
| Runbook | orange `#eb6834`, dashed line, square |
| PER-SAC (cold) | aqua `#1baf7a`, dash-dot line, triangle |
| Kubernetes + HPA | yellow `#eda100`, dotted line, diamond |
| Kubernetes (default) | pink `#e87ba4`, dash-dot-dot line, down-triangle |
| Paper B signals (R14, R15) | client-side = orange square, server-side = blue circle, ping = aqua triangle |
| Status (R11, R16) | ✓ green = supported/met, ◐ amber = partly, ✗ red = not established; always an icon plus a word, never colour alone |
| Counts (R05) | one blue ramp, light = few, dark = many |

- **Same colours as the paper figures.** A policy keeps its colour on every slide.
- **Identity is never colour alone.** Every series also has a line style or marker, and most are labelled directly on the plot, so the slides work for colour-blind viewers and on a poor projector.
- **No titles inside figures.** Units on every axis. MTTR axes say how not-recovered episodes are counted (360 s, the censoring limit).
- **Say the encoding once,** at the first chart (R04 or R07): "blue is our agent, orange is the runbook".

---

## 5. Changing a figure

- **Labels, colours, sizes:** the CONFIG block at the top of `make_review_figures.py` (`STYLE`, `TRAIN_STYLE`, `W`, `H`, `plt.rcParams`). Axis labels are set inside each `rNN_…` function.
- **Wording in diagrams and tables** (R01–R03, R08 headers, R11, R16, R17): edit the strings in that function. Every fact must still match the source column above. Don't round or improve numbers.
- **Never edit the CSVs,** drop points, smooth, or change an axis to make a result look better. Keep the 360 s censoring and the zero baseline on the bar charts. If a figure looks wrong, the check-values in §3 tell you whether the data or the drawing is at fault.
- **A figure you redraw yourself** (draw.io for R01, for example) must carry the same facts. Name it with the same R-number so the team talks about one numbering.

---

## 6. What the figures do not show (be ready for it)

- **Episodes.** R04–R08 and R10 cover the main evaluation only: 40 fault + 10 NULL episodes per policy.
  - R12 covers the edge block (20 per policy) and the seed-1 block (50 per policy).
- **Seeds.** There are two training seeds, not three (R16). Say so before you are asked.
- **The uniform-replay ablation was not run,** so no figure can show that prioritised replay itself helps. Do not claim it.
- **R09 shows training return, not MTTR.** Its smoothing is a 10-episode moving mean, which is why the curves start at episode 10.
