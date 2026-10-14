# Paper B — Writing Package (start here)

This folder has everything the team needs to write **Paper B** without access to the analysis tools: why the paper exists, what it claims, what goes in each section, every figure and table with the exact numbers, the methodology facts, verified references, the template and the submission checklist.

> **Ground rule: the authors write every sentence of the paper** (title, abstract, captions, all text). Nothing in this folder is paper prose to copy. The files give **facts, numbers, structure and pointers**; turn them into your own sentences. Papers must disclose AI assistance; the plan is to disclose AI help with software, analysis, figures and literature search, while the text is yours (see `07_format_and_submission.md`).

## What the paper is, in one breath

A self-healing controller (runbook or RL agent) watches an SLA latency signal and acts when it is breached. We show, on a real Kubernetes testbed whose controller sits across a wireless edge link, that **where and how that signal is measured decides which faults the controller can see**. Client-side tail latency mixes in network noise, forces a loose threshold and hides latency-only faults. A server-side p95, computed over enough requests, does not. We separate the noise sources, give a sampling rule for the quantile window, and show the effect on actual remediation decisions.

**Target venue:** SEAMS 2027 research track, **short paper (6 pages + 1 page of references, IEEE format, double-anonymous)**. **Deadline: 23 Oct 2026, AoE.** Details: `07_format_and_submission.md`.

## Folder map

| File / folder | What it gives you | Use it for |
|---|---|---|
| `01_story_and_claims.md` | why we write it, the gap, research questions, contributions, what we may and may not claim, title ingredients, **abstract content points** | agreeing the storyline; the abstract |
| `02_section_guide.md` | section by section: purpose, length, paragraph-level content points, which figure/table, which numbers | writing each section |
| `03_figures_tables.md` | every figure and table: placement, what it shows, how to read it, what to say about it, caption facts, raw data; **the Fig. 1 drawing spec** (the one figure still to draw) | figures, captions, the Results text |
| `04_methodology_facts.md` | testbed, versions, the control loop, exactly how each signal and analysis was produced; glossary | the Setting and Method sections |
| `05_numbers.md` | **every number you may quote**, with an ID, meaning and source | quoting results correctly |
| `06_references.md` | verified related work: what each paper does, where to cite it, how we differ | Related Work + citations |
| `07_format_and_submission.md` | template sources, IEEE/SEAMS rules, anonymization, AI disclosure, artifact, submission steps, final checklist | formatting and submitting |
| `latex/` | `main.tex` skeleton (compiles; headings, figure/table slots, numbers already in tables), `refs.bib` (verified), `figures/` (b1–b4 as vector PDF), `skeleton_preview.pdf` | the actual paper |
| `figure_data/` | raw CSV data behind every figure and table | redrawing figures in any tool, checking numbers |

## Reading order (about 1.5 h for the whole team)

1. `01_story_and_claims.md`: everyone, before writing anything.
2. `03_figures_tables.md` with the PDFs in `latex/figures/` open: the figures *are* the results.
3. Your section's part of `02_section_guide.md`, plus `05_numbers.md`.
4. `07_format_and_submission.md` before you start formatting.

## Suggested split (4 writers) and timeline to 16 Oct

| Writer | Sections | Needs |
|---|---|---|
| 1 | §2 Setting, §3 Method, draw Fig. 1 | `04_methodology_facts.md`, Fig. 1 spec |
| 2 | §4 Results (RQ1, RQ2) | `03`, `05` |
| 3 | §4 Results (RQ3), §5 Guidelines | `03`, `05` |
| 4 | §1 Introduction, §6 Threats, §7 Related Work, §8 Conclusion, abstract (last) | `01`, `06` |

| When | Milestone |
|---|---|
| 14 Oct | everyone reads; agree RQs/claims (`01`); Overleaf project set up from `latex/` |
| 15 Oct | full first draft of every section; Fig. 1 drawn |
| 16 Oct morning | abstract and title; internal read-through against `05_numbers.md`; page count ≤ 6 + 1 |
| 16 Oct | draft to the guide; send it back through the project lead for a fact-check pass (numbers, figures, references) |
| by 23 Oct (AoE) | revisions from the guide, anonymization, AI statement, submission via HotCRP |

## How to compile

- **Overleaf (recommended):** New Project → Upload Project → upload a zip of `paper_b/latex/` (it contains `main.tex`, `refs.bib`, `figures/`). Compiler: pdfLaTeX. It compiles as is; red `[TODO: …]` marks the text you write.
- **Local:** `latexmk -pdf main.tex` (TeX Live) inside `paper_b/latex/`.
- The skeleton contains a temporary `\nocite{*}`. **Delete it** once your text cites references with `\cite{key}`, so only cited references are listed.

## If a number or figure must change

All data and figures are generated from recorded testbed data by scripts. Never edit numbers by hand:

```
python -m eval.paperb_analysis    # recompute all analyses → data/paperB/analysis.json (copy to figures/paperB/)
python -m eval.paperb_figures     # redraw b1–b4 (figures/paperB/*.pdf)
python -m eval.paperb_export      # refresh paper_b/figure_data/*.csv
```

Those scripts need the raw data on Machine B. Ask the project lead, who runs them.
