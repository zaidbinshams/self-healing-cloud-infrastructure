# Paper A — Writing Package (start here)

Everything needed to write **Paper A**, the main capstone paper, in one place:
- the story and claims;
- what goes in each section;
- every figure and table with the exact numbers;
- the equations and the algorithm, already typeset;
- the methodology facts;
- verified references;
- the template.

> **Ground rule: the authors write every sentence of the paper** (title, abstract, captions, all text). Nothing here is paper prose to copy. The files give facts, numbers, structure and pointers. AI assistance with software, analysis, figures, literature search and this pack is disclosed (`07_format_and_submission.md`).

## The paper in one breath

We trained a reinforcement-learning agent **online on a live Kubernetes cluster** to remediate faults with 12 discrete actions (restart, scale, roll back to a golden template, scale from zero).
- **Guards:** hard action masks and a one-action-in-flight lock.
- **Setting:** a 20 s control loop, with the controller across a wireless edge link.
- **Pre-registered evaluation (50 faults/NULL episodes per policy):**
  - it recovers 35/40 faults vs 0/40 for default Kubernetes and 10/40 for HPA;
  - it is non-inferior to a scripted SRE runbook on MTTR;
  - it never acts when nothing is wrong.
- **Warm start:** starting from runbook data is necessary; without it the agent recovers 15/40 and acts in every fault-free episode.
- **Replication:** a second training seed replicates the result.
- **Limits:** HPA is faster on load surges, one fault/target pair was never learned, and the edge-network margin could not be established with 16 episodes.

**Format:** IEEE conference (IEEEtran), **≤ 7 pages** (guide's limit). The guide picks the venue; reformat then.

## Folder map

| File / folder | What it gives you | Use it for |
|---|---|---|
| `01_story_and_claims.md` | story, RQs, contributions, **may / must-not claim**, what results mean, limitations, title ingredients, abstract points | agreeing the storyline; abstract; discussion |
| `02_section_guide.md` | section by section: page budget, ordered content points, which figure/table and numbers | writing each section |
| `03_figures_tables.md` | each figure/table: what it shows, how to read it, caption facts, data file; how to regenerate | figures, captions, Results |
| `04_methodology_facts.md` | testbed, loop, MDP, agent hyper-parameters, baselines, protocol, amendments table, versions, threats | Testbed, Method, Design sections |
| `05_numbers.md` | **every number you may quote**, with an ID and source file | quoting results correctly |
| `06_references.md` | each reference: what it does, how we differ, where to cite; verification status | Related Work, citations |
| `07_format_and_submission.md` | template rules, AI disclosure, artefact, final checklist | formatting and submitting |
| `latex/` | `main.tex` skeleton: sections with TODO points, **equations (1)–(12), Algorithm 1, Fig. 1 (TikZ), Tables I (faults), II (hyper-parameters), III (main results), IV (actions), V (seed 1), VI (hypotheses) already filled**, figure slots; `refs.bib`; `figures/` (a1–a5, vector PDF) | the actual paper |
| `08_making_figures.md` + `make_figures.py` | **how to draw every figure yourselves** from the CSVs (one command, or recipes for Excel/Sheets/pgfplots), labelling and caption rules, checks against the numbers | making your own figures |
| `figure_data/` | CSV behind every figure and table (per-episode rows, summaries, all test statistics, KM curves, training returns, action sequences, latency context) | redrawing figures, checking numbers |

## Results at a glance (details and sources in `05_numbers.md`)

| Hypothesis (pre-registered) | Outcome |
|---|---|
| H1 lower MTTR than Kubernetes and HPA | **Supported** on F1–F3 (and F4 vs Kubernetes); **not on F4 vs HPA** (HPA faster) |
| H2 non-inferior to the runbook (20 s margin); lower SLA penalty | MTTR **non-inferior** (CI −20.0 to +0.9 s); SLA penalty **tie** |
| H3 warm start helps | **Supported** (training return p = 6.5×10⁻⁹; evaluation MTTR p = 4.4×10⁻⁵) |
| H4 safety | **Supported** (0/10 NULL actions in evaluation, 0/30 in training, 0 violations) |
| H5 edge gap within the margin | **Not established** (CI upper bound 20.15 s vs 20 s; n = 16) |
| Seed-1 replication | H2 holds within the block (CI −0.7 to +19.9 s); 35/40; 0/10 NULL; runbook anchor shows no drift |

## Reading order (~1.5 h for the team)
1. `01_story_and_claims.md` (everyone).
2. `03_figures_tables.md` with `latex/figures/*.pdf` open.
3. Your section in `02_section_guide.md` + `05_numbers.md`.
4. `08_making_figures.md` before drawing figures.
5. `07_format_and_submission.md` before formatting.

## Suggested split (4 writers)

| Writer | Sections | Main sources |
|---|---|---|
| 1 | III Testbed, IV Method (explain the equations), Fig. 1 | `04`, `latex/main.tex` |
| 2 | V Design, VI Results part 1 (Table II, Figs. 2–3, Table IV) | `03`, `05` |
| 3 | VI Results part 2 (H3–H5, seed 1, Table VII), VII Discussion | `01`, `03`, `05` |
| 4 | I Introduction, II Related Work, VIII Conclusion, abstract (last) | `01`, `06` |

## How to compile
- **Overleaf:** New Project → Upload Project → a zip of `paper_a/latex/`. Compiler: pdfLaTeX. Red `[TODO: …]` marks text you write. (The skeleton was not compiled on Machine B, which has no TeX. If Overleaf reports an error, it will be in the TikZ figure or the algorithm block; fix it there or tell the project lead.)
- Remove `\nocite{*}` when your text cites with `\cite{key}`.

## If a number or figure must change
Never edit numbers by hand. The project lead regenerates everything from recorded data (commands in `03_figures_tables.md`, "Regenerating").
