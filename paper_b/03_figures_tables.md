# 03 — Figures and Tables

> The four data figures (b1–b4) are **final, vector PDFs ready for IEEE** (`latex/figures/`). They are drawn from recorded data with a colour-blind-safe palette, and are readable in grayscale through marker shapes and direct labels. **You do not need to redraw them.** If you want a different style, the raw data is in `figure_data/` (CSV), and the "Redraw notes" below say how. The only figure still to make is **Fig. 1 (testbed diagram)**: spec below.
>
> Captions: write them yourselves from the "Caption facts". IEEE style: the figure caption goes below the figure, the table caption above the table. Refer to every figure/table in the text before it appears.

## Overview and placement

| # in paper | File | Width | Section | Shows |
|---|---|---|---|---|
| Fig. 1 | `figures/testbed.pdf` (**to draw**) | single column | §2 | testbed and the three measurement points |
| Table I | in `main.tex` | single column | §2 | the three signals |
| Fig. 2 (b1) | `b1_decomposition.pdf` | **full width** (`figure*`) | §4.1 | CV and network coupling per run |
| Fig. 3 (b2) | `b2_timeseries_treatA.pdf` | single column | §4.1 | 30 ticks after the runtime fix |
| Table II | in `main.tex` | single column | §4.1 | stall events before/after |
| Fig. 4 (b3) | `b3_estimator_noise.pdf` | single column | §4.2 | sampling CV vs tail samples |
| Table III | in `main.tex` | single column | §4.3 | threshold inflation |
| Fig. 5 (b4) | `b4_detectability.pdf` | single column | §4.3 | fault peaks vs thresholds |
| Table IV | in `main.tex` | single column | §4.3 | runbook-view detection |

**If you run out of space:** drop Fig. 3 (b2) first (Fig. 2 carries the same message), then shrink Table III to the final run only.

---

## Fig. 1 — testbed and measurement points (TO DRAW)

**Tool:** draw.io / diagrams.net (free), PowerPoint, or TikZ. Export as **PDF (vector)**, single column (~8.8 cm wide), white background, ≥ 8 pt text. Save as `latex/figures/testbed.pdf` and replace the placeholder box in `main.tex` (the comment above it gives the line).

**Layout (left → right):**

```
 ┌───────────── Machine B (controller host) ─────────────┐        Wi-Fi hotspot        ┌──────────── Machine A (cluster) ─────────────┐
 │  [Load generator]  ──requests──────────────────────────────────────────────────────▶  [frontend] → [8 backend services + redis]   │
 │     ▲ client p99 (M1)                                  │      (wireless edge link)    │     ▲ server p95/p99 from request logs (M2) │
 │  [Controller: Monitor → Analyze → Plan → Execute] ◀── telemetry: Prometheus, K8s API ──  [Prometheus/cAdvisor]  [Kubernetes API]   │
 │        │ actions (restart / scale / restore) ───────────────────────────────────────▶                                           │
 │  [ping prober] ─────── ICMP, ping p99 (M3) ────────────────────────────────────────▶                                           │
 └────────────────────────────────────────────────────────┘                              └──────────────────────────────────────────────┘
```

**Must show:**
1. Two boxes: "Controller host" (load generator, controller with the M-A-P-E loop, ping prober) and "Cluster (single-node Kubernetes)" (frontend, backend services, Prometheus, Kubernetes API, fault injector).
2. The wireless link between them, drawn as a dashed or zig-zag line labelled "Wi-Fi hotspot (edge link)".
3. **The three measurement points**, marked M1/M2/M3, each in its own colour or marker shape:
   - **M1** at the load generator (client-side, includes the link);
   - **M2** at the frontend (server-side, request logs);
   - **M3** on the link (ping).
4. Arrows: requests (load generator → frontend); telemetry (cluster → controller); actions (controller → Kubernetes API).
5. A small label "20 s control tick" on the controller box.

**Caption facts:** controller and load generator on one host, the cluster on another, connected by a Wi-Fi hotspot; the same requests are measured at M1 (client), M2 (server) and M3 (network path) in the same 20 s windows.

---

## Fig. 2 (b1) — decomposition: `b1_decomposition.pdf` (full width)

**What it shows:** six 10-min runs (rows, in the order they were done). Left panel: tick-to-tick **coefficient of variation (CV)** of the per-tick p99 latency, client-side (orange squares) and server-side (blue circles). Right panel: **Pearson correlation** of each signal with the per-tick ping p99 (network RTT).

**How to read it:**
- Top four rows (before the runtime fix): client and server points sit together, at high CV, with low correlation to ping → the variability came from inside the cluster.
- "U40, runtime fix": the server point drops to 0.14, the client point stays at 0.60, and client correlation with ping jumps to 0.95 → once the cluster is stable, the client signal is dominated by the network.
- Last row (Wi-Fi power-save off): client CV falls to 0.35, but the correlation stays high (0.87).

**Row meanings** (also in `04_methodology_facts.md` §5):

| Row | Load | Cluster state |
|---|---|---|
| U50 baseline | 50 users | before tuning |
| U50 repeat (+host metrics) | 50 users | same config, 12 min later, with host-level metrics added |
| U50, recommendation 500m | 50 users | recommendationservice CPU limit raised to 500m |
| U40 | 40 users | as above |
| U40, runtime fix | 40 users | + cartservice .NET thread-pool minimum 32 (removes stalls) |
| U40, runtime fix + Wi-Fi tuned | 40 users | + Wi-Fi power saving off on both machines |

**Observations to state** (numbers N1–N16 in `05_numbers.md`):
- before the fix: CVs 0.29–0.73, r ≤ 0.37;
- after the fix: server CV 0.14/0.19 vs client 0.60/0.35; client r 0.95/0.87.

**Caption facts:** six 30-tick runs (20 s ticks); CV across ticks of p99 latency measured at the client (Locust) and at the server (frontend request logs); right: correlation with the ping p99 on the same windows.

**Raw data:** `figure_data/fig_b1_decomposition.csv`. **Redraw notes:** a dot plot or grouped bars per run, two panels; never a dual y-axis.

---

## Fig. 3 (b2) — time series after the fix: `b2_timeseries_treatA.pdf`

**What it shows:** the "U40, runtime fix" run, 30 consecutive ticks: client p99 (orange), ping p99 (green), server p99 (blue), in ms.

**How to read it:** wherever the green ping line spikes (ticks 11, 13, 15, 22, 24, 26, 29: ping p99 > 200 ms), the orange client line spikes with it; the blue server line stays between 63 and 119 ms throughout.

**Observations to state:** client p99 median 169 ms vs server 88 ms; ping p99 spikes up to 305 ms coincide with client p99 spikes up to 577 ms; server range 63–119 ms (N11–N14, N17).

**Caption facts:** per-tick p99 latency at client and server, and ping p99 on the B→A link, for the run after the runtime fix; 30 ticks of 20 s.

**Raw data:** `figure_data/fig_b2_timeseries_treatmentA.csv` (tick, client/ping/server p99, request counts).

---

## Table II — stall events (in `main.tex`, filled)
Before vs after the runtime fix (U40, ~5 min each): requests, slow (> 500 ms), events, events/min, median duration, events with a ping spike. Source: `figure_data/table_stalls.csv`. **Caption facts:** sub-second stall events from per-request server logs; slow requests grouped into events; ping checked during each event.

---

## Fig. 4 (b3) — estimator noise: `b3_estimator_noise.pdf`

**What it shows:** log-log plot. x = expected number of tail samples per window, k = n(1−q); y = sampling CV of the nearest-rank quantile (bootstrap). One line per quantile (p90 triangles, p95 circles, p99 squares), points = window lengths 5, 10, 20, 40, 60 s (left to right within each line). Dashed line: fitted CV = 0.24·k^−0.41. Light horizontal grid line at CV = 0.1.

**How to read it:**
- all quantiles fall near one curve, so k (not the quantile per se) governs sampling noise;
- the 10 % line is crossed near k ≈ 8;
- at our 20 s tick (third point of each line): p95 at k ≈ 41 (CV 0.058), p99 at k ≈ 8 (CV 0.097).

**Observations to state:** N22–N31.

**Caption facts:** 37,933 server-side requests over 15.3 min at steady state; windows of 5–60 s; 400 bootstrap resamples per window; fit over q ≥ 0.9.

**Raw data:** `figure_data/fig_b3_estimator_noise.csv` (all cells incl. q = 0.5 and the observed window-to-window CV), `figure_data/fig_b3_fit.csv`.

---

## Table III — threshold inflation (in `main.tex`, filled)
Two runs × two signals: CV, threshold from the same rule, threshold/median. Optional extra columns from `figure_data/table_threshold_inflation.csv`: held-out breach rate (0–2.2 %), and a third run. **Caption facts:** threshold = ceil_10ms(1.5 × q95) fitted on the first half of a steady run, evaluated on the second half.

---

## Fig. 5 (b4) — detectability: `b4_detectability.pdf`

**What it shows:** four fault smoke tests at the final cluster state (rows). x = fault peak before the remedy ÷ the signal's calibrated threshold (server p95 threshold 70 ms; client p99 threshold 800 ms). Vertical line at 1 = threshold. Blue circles server, orange squares client.

**How to read it:** right of the line = detected. Every blue point is at or right of 1 (1.01–2.83); every orange point is left of 1 (0.27–0.71).

**Observations to state:** N44–N48; F2 with one stress worker is marginal even server-side (1.01–1.10×), so say so.

**Caption facts:** thresholds from the same calibration rule on the same steady run; the peak is the maximum per-tick value between injection and the oracle remedy.

**Raw data:** `figure_data/fig_b4_detectability.csv`.

---

## Table IV — runbook view (in `main.tex`, filled)
Detection within the first two fault ticks (latency over threshold OR failure ratio > 1 %) per fault type and signal, plus fault-free ticks over threshold. Source: `figure_data/table_runbook_view_detection.csv`, `table_runbook_view_fault_free.csv`. **Caption facts:** 140 recorded runbook episodes (20 without exploration, 120 with 25 % random actions); fault-free ticks are those before any fault and before any action; "latency only" detection is in the CSV if you want to show that F3 is caught through failures.

---

## Optional (only if space remains)
- **Transport table:** `figure_data/table_transport.csv` (2.96 → 0 connect timeouts per 100 ticks). One sentence in the text is usually enough.
