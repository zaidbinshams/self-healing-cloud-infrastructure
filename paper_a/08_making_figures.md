# 08 — Making the Figures Yourselves (from the data in this folder)

Every figure in the paper can be drawn by the team from `paper_a/figure_data/*.csv`. These CSVs are the recorded results (per-episode rows, summaries, survival curves, training returns), exported from the raw testbed logs by `eval/papera_export.py`. You don't need the raw logs, the cluster or the project's Python environment.

Two ways to do it:
- **A. Run the script** `paper_a/make_figures.py` (about 5 minutes). It draws all five plots in the paper's style. Change labels, sizes and colours in its CONFIG block.
- **B. Draw them in your own tool** (Excel, Google Sheets, pgfplots in Overleaf, matplotlib from scratch), following the per-figure recipes below.

Either way, **the data is never edited**: no removed points, no rounding before plotting, no extra smoothing.

---

## A. Run the script

### On your laptop
```
git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git
cd self-healing-cloud-infrastructure
python -m pip install matplotlib numpy        # Python 3.9 or newer
python paper_a/make_figures.py
```
Output goes to `paper_a/latex/figures/`: `a1_km`, `a2_mttr_by_fault`, `a3_training`, `a4_edge` and `a5_seed1`, each as `.pdf` (vector, used by LaTeX) and `.png` (preview). Upload the PDFs to Overleaf's `figures/` folder.

### Without installing anything (Google Colab)
1. Open colab.research.google.com and create a new notebook.
2. Run `!git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git`
3. Run `!python self-healing-cloud-infrastructure/paper_a/make_figures.py`
4. Download the PDFs from `self-healing-cloud-infrastructure/paper_a/latex/figures/` (Files panel on the left).

### What you may change (CONFIG block at the top of the script)
- **Display names.** `STYLE` and `TRAIN_STYLE` map each policy id to its label, colour, line style and marker. If the text says "PER-SAC" and "runbook", the figures must use the same names.
- **Size.** `COL_W_IN` is the one-column width; `a2_mttr_by_fault` uses twice that, for `figure*`. Font sizes are in `plt.rcParams`.
- **Axis labels** are set in each `fig_…` function (`set_xlabel` / `set_ylabel`).

---

## B. Recipes (if you draw them in another tool)

Policy ids in the CSVs and the names used in the paper:

| id in CSV | name in paper | colour | line / marker |
|---|---|---|---|
| `sac_per` (and `sac_per_s0` in training.csv) | PER-SAC (warm) | blue `#2a78d6` | solid, circle |
| `sac_per_s1` | PER-SAC (warm, seed 1) | same blue | dotted, circle |
| `runbook` | runbook | orange `#eb6834` | dashed, square |
| `sac_per_cold` (`sac_per_cold_s0`) | PER-SAC (cold) | green `#1baf7a` | dash-dot, triangle |
| `k8s_hpa` | Kubernetes + HPA | yellow `#eda100` | dotted, diamond |
| `k8s_default` | Kubernetes | pink `#e87ba4` | dash-dot-dot, down-triangle |

### Fig. 2 — `a1_km`: time to recovery (Kaplan–Meier)
- **File:** `km_curves.csv`. Columns: `policy`, `t_s` (time since injection, s), `p_not_recovered`, `n` (episodes, 40).
- **Plot:** for each policy, a **step function** (stairs, "post" steps). Start at (0, 1.0), go through each (`t_s`, `p_not_recovered`) row in order, then extend flat to x = 360. Kubernetes has no rows: it is a flat line at 1.0 (no episode recovered).
- **Axes:** x "time since injection (s)", 0–360; y "P(not yet recovered)", 0–1.
- **Labels:** put the policy names at the right end of each line, or use a legend.

### Fig. 3 — `a2_mttr_by_fault`: MTTR per episode (full page width)
- **File:** `episodes_main.csv`. Use rows with `fault` in F1–F4. Columns: `policy`, `fault`, `mttr_censored_s`, `recovered`.
- **Plot:** four panels side by side (F1, F2, F3, F4), with a shared y axis listing the five policies. One dot per episode at x = `mttr_censored_s` (small vertical jitter so dots don't overlap). A short vertical bar marks the **median** per policy and fault. A dotted vertical line at x = 360 marks censoring (not recovered = 360).
- **Axes:** x "MTTR (s)", 0–378.

### Fig. 4 — `a3_training`: training return
- **File:** `training.csv`. Columns: `run` (`sac_per_s0`, `sac_per_s1`, `sac_per_cold_s0`), `episode`, `return`.
- **Plot:** for each run, the **10-episode rolling mean** of `return` in row order (first point at episode 10). The cold run has 100 episodes; the others have 200.
- **Axes:** x "training episode"; y "return (10-episode mean)".

### Fig. 5 — `a4_edge` and Fig. 6 — `a5_seed1`: paired medians (dumbbells)
- **Files:** `summary_main.csv` with `summary_edge.csv` (Fig. 5); `summary_main.csv` with `summary_seed1.csv` (Fig. 6). Column `mttr_median_censored_s`, rows F1–F4.
- **Fig. 5 pairs:** runbook main → runbook edge; `sac_per` main → `sac_per` edge.
- **Fig. 6 pairs:** runbook main → runbook seed-1 block; `sac_per` (main) → `sac_per_s1` (seed-1 block).
- **Plot:** x = fault (F1–F4), with the runbook slightly left and PER-SAC slightly right of each tick. Hollow marker = first condition, filled marker = second, joined by a vertical line.
- **Axes:** y "median MTTR (s)". The legend states both the policy and what hollow/filled mean.
- **Note:** a median of 360 would mean that fault was mostly not recovered. Here the highest is PER-SAC F2 (~230–240 s).

### Fig. 1 — architecture
TikZ code in `latex/main.tex` (inside `\begin{figure}`). Edit the node text there, or redraw it in draw.io / PowerPoint and export a PDF. Keep the two-machine split, the wireless link, and the arrows for load, telemetry, actions and faults.

---

## Labelling rules (all figures)

1. **Units on every axis**, in brackets: "MTTR (s)", "time since injection (s)".
2. **Names are the same everywhere.** Policy and fault names in figures match the text and tables exactly (F1–F4 as defined in Table I).
3. **No titles inside the figure.** The caption carries the message. Panel labels (F1–F4) are fine.
4. **Every caption says:**
   - what one mark is (one episode, a median, a rolling mean);
   - n (episodes per policy, or per fault);
   - the censoring at 360 s where MTTR is plotted;
   - the condition (main evaluation, netem edge subset, or seed-1 block).
   - Caption facts per figure are in `03_figures_tables.md`.
5. **Readable in print.**
   - Fonts are 7 pt or larger at final size.
   - Lines differ by style and marker as well as colour, so the figure survives grayscale printing.
   - Use vector PDF, not screenshots.
6. **Sizes.**
   - One-column figures: `\includegraphics[width=\columnwidth]`.
   - Fig. 3 is full width: `figure*` with `\textwidth`.
   - Draw at the final size (one column ≈ 3.4–3.5 in wide). Don't scale a large drawing down, or the fonts shrink.
7. **Referencing.** Each figure has a `\label{fig:…}` in `main.tex`. Refer to figures in the text with `Fig.~\ref{fig:km}`; never type the number by hand.
8. **Honesty.**
   - Do not cut the y axis to exaggerate a difference.
   - Do not drop censored (360 s) episodes.
   - Do not add smoothing beyond the stated 10-episode window.
   - If you change a figure's design, the numbers it shows must still match `05_numbers.md`.

## Checking your figure against the numbers
- **Fig. 2:** the plateaus are PER-SAC 0.125 (5/40 not recovered), runbook 0.05, HPA 0.75, cold 0.625, Kubernetes 1.0.
- **Fig. 3:** the medians are PER-SAC F1 59, F2 240, F3 60, F4 60 s; runbook 80, 89, 50, 80 s.
- **Fig. 5:** PER-SAC edge F1 99, F2 229, F3 49, F4 69 s; runbook edge 78, 178, 59, 69 s.
- If anything differs, recheck your filtering first: final valid episodes only (the CSVs already hold only those), and the correct `policy` and `fault` rows.
