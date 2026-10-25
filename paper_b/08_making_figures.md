# 08 — Making the Figures Yourselves (from the data in this folder)

The PDFs in `latex/figures/` are the reference figures. Every one of them can be redrawn by the team from `paper_b/figure_data/*.csv`. These CSVs hold the recorded results (per-run statistics, per-tick latencies, bootstrap estimates, fault-smoke peaks), exported from the raw testbed logs by `eval/paperb_export.py`. You don't need the raw logs, the cluster or the project's Python environment.

Two ways to do it:
- **A. Run the script** `paper_b/make_figures.py` (about 5 minutes). It draws all four data figures exactly as in the reference PDFs. Change labels, sizes and colours in its CONFIG block.
- **B. Draw them in your own tool** (Excel, Google Sheets, pgfplots in Overleaf, matplotlib from scratch), following the per-figure recipes below.

Either way, **the data is never edited**: no removed points, no rounding before plotting, no smoothing.

Fig. 1 (the testbed diagram) has no data. It is drawn by hand; its spec is in `03_figures_tables.md`.

---

## A. Run the script

### On your laptop
```
git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git
cd self-healing-cloud-infrastructure
python -m pip install matplotlib        # Python 3.9 or newer
python paper_b/make_figures.py
```
Output goes to `paper_b/latex/figures/`: `b1_decomposition`, `b2_timeseries_treatA`, `b3_estimator_noise` and `b4_detectability`, each as `.pdf` (vector, used by LaTeX) and `.png` (preview). Upload the PDFs to Overleaf's `figures/` folder.

For slides: `python paper_b/make_figures.py --slides` writes 600-dpi PNGs to `paper_b/slides/` and leaves the paper figures alone. Ready-made copies are already in `paper_b/slides/`.

### Without installing anything (Google Colab)
1. Open colab.research.google.com and create a new notebook.
2. Run `!git clone https://github.com/zaidbinshams/self-healing-cloud-infrastructure.git`
3. Run `!python self-healing-cloud-infrastructure/paper_b/make_figures.py`
4. Download the PDFs from `self-healing-cloud-infrastructure/paper_b/latex/figures/` (Files panel on the left).

### What you may change (CONFIG block at the top of the script)
- **Display names.** `CLIENT`, `SERVER`, `PING` set the label, colour and marker of each signal; `QUANTILE_STYLE` sets the p90/p95/p99 lines of Fig. b3; `SHORT` sets the service names in Fig. b4's row labels. The names must match the text (e.g. if the text says "server-side p99", the legend must too).
- **Size.** `COL_W_IN` is the one-column width; `b1_decomposition` uses twice that, for `figure*`. Font sizes are in `plt.rcParams`.
- **Axis labels** are set in each `fig_…` function (`set_xlabel` / `set_ylabel`).

### How exact is it?
Fig. b2 and b4 come out pixel-identical to the reference PDFs. Fig. b1 and b3 differ by a fraction of a pixel in a few marker positions, because the CSVs round values to 4 significant digits. That changes nothing you can see or read off.

---

## B. Recipes (if you draw them in another tool)

The same encoding in every figure:

| Signal | Name in paper | Colour | Marker |
|---|---|---|---|
| client-side p99 (Locust, on Machine B) | client p99 | orange `#eb6834` | square |
| server-side p99 / p95 (frontend request logs) | server p99 / server p95 | blue `#2a78d6` | circle |
| ping p99 on the B→A link | ping p99 | green `#1baf7a` | triangle |

### Fig. 2 — `b1_decomposition`: noise sources per run (full page width)
- **File:** `fig_b1_decomposition.csv`, 6 rows (one per 10-minute run). Columns: `run`, `cv_client_p99`, `cv_server_p99`, `r_client_vs_ping_p99`, `r_server_vs_ping_p99`.
- **Plot:** two panels side by side, sharing the y axis, which lists the runs **top to bottom in CSV order**.
  - Left panel, titled "Tick-to-tick variability": one orange square at `cv_client_p99` and one blue circle at `cv_server_p99` per run. x label "CV of tick p99".
  - Right panel, titled "Coupling to network RTT": the same with `r_client_vs_ping_p99` and `r_server_vs_ping_p99`, plus a thin vertical line at x = 0. x label "Pearson r with ping p99".
- **Legend:** one legend above both panels, two entries: "client p99 (Locust)", "server p99 (frontend logs)".

### Fig. 3 — `b2_timeseries_treatA`: one run after the fix
- **File:** `fig_b2_timeseries_treatmentA.csv`, 30 rows (ticks 0–29). Columns: `tick`, `client_p99_ms`, `ping_p99_ms`, `server_p99_ms`.
- **Plot:** three lines with small markers against `tick`: client (orange squares), ping (green triangles), server (blue circles). No legend: put each name ("client p99", "ping p99 (B→A)", "server p99") at the right end of its line.
- **Axes:** x "tick (20 s)", 0–29 (leave room on the right for the labels); y "latency (ms)", linear.

### Fig. 4 — `b3_estimator_noise`: sampling noise vs tail samples
- **Files:** `fig_b3_estimator_noise.csv` (20 rows = 5 window lengths × 4 quantiles) and `fig_b3_fit.csv` (one row: `a`, `b`).
- **Plot:** use rows with `quantile` 0.9, 0.95 and 0.99 (leave out 0.5). For each quantile, one line through its 5 points, x = `expected_tail_samples_k`, y = `bootstrap_sampling_cv`, sorted by x. p90 green triangles, p95 blue circles, p99 orange squares. Label each line ("p90", "p95", "p99") at its left end.
  - Fit: a dashed grey line y = a·k^b for k from 1.5 to 300, labelled "fit: CV = 0.24·k^−0.41".
  - A light horizontal line at y = 0.10 (the 10 % reference).
- **Axes:** both **logarithmic**. x "expected tail samples per window, k = n(1−q)"; y "sampling CV (bootstrap)", ticks 0.03, 0.05, 0.1, 0.2.

### Fig. 5 — `b4_detectability`: can each signal see the fault?
- **File:** `fig_b4_detectability.csv`, 8 rows = 4 fault smokes × 2 signals. Columns: `smoke`, `fault`, `target`, `severity`, `signal`, `threshold_ms`, `peak_over_threshold`.
- **Plot:** one row per smoke, **top to bottom in CSV order**, labelled "F4 surge ×3.0", "F4 surge ×2.5", "F2 cart, 1 worker", "F2 currency, 1 worker". Per row: an orange square at the client p99 `peak_over_threshold` and a blue circle at the server p95 one. A vertical line at x = 1 labelled "threshold".
- **Legend** above the plot, with the thresholds in it: "client p99 (threshold 800 ms)", "server p95 (threshold 70 ms)".
- **Axes:** x "fault peak / calibrated threshold". Right of 1 = detected.

### Tables II–IV
They are already filled in `main.tex`. If you rebuild or restyle them, take the values from `table_stalls.csv` (Table II), `table_threshold_inflation.csv` (Table III), and `table_runbook_view_detection.csv` plus `table_runbook_view_fault_free.csv` (Table IV). `figure_data/README.md` lists every column.

---

## Numbering and labels

| In the paper | File | LaTeX label | Width |
|---|---|---|---|
| Fig. 1 | `testbed.pdf` (to draw) | `fig:testbed` | one column |
| Fig. 2 | `b1_decomposition.pdf` | `fig:decomposition` | full width (`figure*`) |
| Fig. 3 | `b2_timeseries_treatA.pdf` | `fig:timeseries` | one column |
| Fig. 4 | `b3_estimator_noise.pdf` | `fig:estimator` | one column |
| Fig. 5 | `b4_detectability.pdf` | `fig:detect` | one column |

- The b1–b4 file names are fixed; the figure numbers are assigned by LaTeX in order of appearance. If you drop Fig. 3 for space (the first candidate, see `03`), the later figures renumber themselves.
- Refer to figures with `Fig.~\ref{fig:estimator}`; never type a number by hand.
- Captions: write them from the "Caption facts" in `03_figures_tables.md`.

## Labelling rules (all figures)

1. **Units on every axis**, in brackets: "latency (ms)", "tick (20 s)".
2. **Names match the text exactly.** Say "server-side p95" in the text if the legend says "server p95". Say p99 where the figure shows p99: b1 and b2 show p99; b4 shows the final SLA signal, server p95.
3. **No titles inside the figure.** The caption carries the message. Panel titles (b1) are fine.
4. **Every caption says** what one mark is (a run, a tick, a window length, a fault smoke), n (6 runs, 30 ticks, 37,933 requests, 4 smokes), and which threshold applies (b4: 70 ms server, 800 ms client).
5. **Readable in print.** Fonts 7 pt or larger at final size; colour plus marker shape for every series, so the figure works in grayscale; vector PDF, not screenshots.
6. **Sizes.** One-column figures: `\includegraphics[width=\columnwidth]`. Fig. 2 is full width: `figure*` with `\textwidth`. Draw at the final size; don't scale a large drawing down.
7. **Honesty.** Keep the log scales in b3 (the power law is the point). Do not drop runs from b1 or ticks from b2. Keep the line at 1 in b4. If you change a design, the numbers it shows must still match `05_numbers.md`.

## Checking your figure against the numbers

- **Fig. 2:** row "U40, runtime fix": server CV 0.14, client CV 0.60, client r 0.95 [N11–N13]. Before the fix every CV is between 0.29 and 0.73 [N1, N2].
- **Fig. 3:** server p99 stays within 63–119 ms; ping spikes up to 305 ms line up with client spikes up to 577 ms [N17].
- **Fig. 4:** at 20 s windows, p95 sits at k ≈ 41, CV 0.058, and p99 at k ≈ 8.3, CV 0.097 [N24, N25]; the fit crosses 0.10 near k ≈ 8 [N27].
- **Fig. 5:** server 2.83, 2.11, 1.10, 1.01; client 0.65, 0.39, 0.27, 0.71 [N44–N47].
- If anything differs, check that you used the right rows (quantile filter in b3, signal column in b4) and the CSV order.

---

## Using the figures in the presentation

- **Use the slide PNGs** in `paper_b/slides/` (600 dpi, same design as the paper), or the PDFs if your slide tool takes vector images (PowerPoint and Keynote do). Never screenshot the paper PDF.
- **One figure per slide, filling most of the slide.** The figures were drawn for a 3.4-inch column; scaled up to slide width, the 7–8 pt labels become about 20 pt, which is readable from the back of a room.
- **The slide title states the finding, not the figure name.** For example: "Client latency tracks the Wi-Fi, server latency does not" for Fig. 3; "Only the server-side signal sees the latency-only faults" for Fig. 5 (F1 and F3 are caught by both signals, so don't say the client side never detects faults).
- **Say the encoding once** at the first figure: "orange squares are client-side, blue circles are server-side". It is the same in every figure.
- **Slides don't need "Fig. n" numbers.** If you reuse a paper figure, drop the number, keep the axis labels and legend, and put a one-line takeaway under it.
- **Point at one thing per figure:** b1 the "runtime fix" row; b2 the spikes that line up; b3 the 10 % line at k ≈ 8; b4 the line at 1.
- **Fig. 1 for slides:** draw the testbed diagram once (draw.io) and use the same file in the paper and on the slides.
- Best order for a short talk: Fig. 3 (b2) first, because it is the most intuitive; then Fig. 5 (b4), the consequence; then Fig. 4 (b3), the rule.
