# 07 — Format, Template, Anonymization, AI Statement, Submission

## Venue facts (checked on the official call, 13 Oct 2026; re-check a week before submitting)

Source: <https://conf.researchr.org/track/seams-2027/seams-2027-research-track>

| Item | Requirement |
|---|---|
| Track | SEAMS 2027 Research Track, **short paper**: "ongoing research, new research ideas, or preliminary results without a mature evaluation" |
| Length | **6 pages of content + 1 page of references** (figures and tables count as content) |
| Template | **IEEE conference proceedings template**: LaTeX `IEEEtran`, class options `conference`, 10 pt, **no** compsoc options |
| Review | **double-anonymous**: no author names or affiliations; refer to your own prior work in the third person; no identifying links or acknowledgments |
| Deadline | **23 October 2026, AoE (UTC−12)**; one round |
| Submission site | HotCRP: <https://seams27.hotcrp.com/> |
| Later dates | notification 3 Dec 2026; revisions due 5 Jan 2027; decision on revisions 15 Jan 2027; camera-ready 29 Jan 2027; conference 26–27 Apr 2027, Dublin (with ICSE 2027) |
| AI policy | IEEE: AI-generated **text** must be disclosed in the acknowledgments, citing the AI system used; ACM: AI tools cannot be authors; editing-only use needs no disclosure; **fabricated or hallucinated references → desk reject** |
| Artifacts | optional; may go to the artifact track (availability, reusability, reproducibility) |

## Getting the template

- **Easiest:** use our skeleton, `latex/main.tex` (already IEEEtran `conference`, 10 pt, anonymous author block). Upload `paper_b/latex/` to Overleaf as a zip (New Project → Upload Project); it compiles with pdfLaTeX.
- **Official sources** (if you want a fresh copy to compare):
  - Overleaf gallery, IEEE official templates: <https://www.overleaf.com/gallery/tagged/ieee-official> ("IEEE Conference Template", IEEEtran 1.8b);
  - CTAN package `IEEEtran`: <https://ctan.org/pkg/ieeetran>.
  - Avoid old copies of `IEEEtran.cls` (e.g. v1.7a with a "compsocconf" option); use the current 1.8b.

## Formatting rules to keep

- Two columns, 10 pt, IEEE fonts as set by the class. **Do not** change margins, font size or spacing to gain room.
- **Figures:** vector PDF (ours are), single column = `\columnwidth`, full width = `figure*` with `\textwidth`. Captions below figures; table captions above tables (IEEE style). Every figure/table referenced in the text before it appears.
- **Units:** a thin space before units (`70\,ms`), consistent "ms" and "s".
- **Citations:** `\cite{key}` with IEEEtran numeric style; cite as references, not as nouns ("as shown in [3]", not "[3] shows").
- **Abbreviations** defined at first use (SLA, SLO, MAPE-K, CV, RTT, CPU, K8s).
- **Page check:** content ends on page 6; references only on page 7.
- **Delete** the temporary `\nocite{*}` before submitting.

## Anonymization checklist (review version)

- [ ] Author block reads "Anonymous Author(s)" / "Anonymous Institution" (already in the skeleton).
- [ ] No names, university, city, course, guide, or capstone mentions anywhere (text, figures, PDF metadata).
- [ ] No GitHub or other identifying links. For the artifact, use an anonymized mirror (e.g. <https://anonymous.4open.science>); the project lead prepares it.
- [ ] Machine names, hostnames and IPs do not appear (figures were checked; keep it that way in Fig. 1).
- [ ] PDF metadata: Overleaf → Menu → check that title/author fields are empty or anonymous (`\hypersetup{pdfauthor={}}` if hyperref is used).
- [ ] Acknowledgment: keep only the anonymous AI statement; thank people only in the camera-ready version.

## AI-assistance statement (write it yourselves; content points)

Place it in the Acknowledgment section (anonymous wording such as "The authors…"). It should state:
1. **Tool:** Claude Code (Anthropic), model Claude Opus 5.5; cite it as the AI system, as IEEE asks.
2. **What it was used for:** developing the experiment and measurement software; data analysis scripts; producing the figures; literature search assistance and reference verification; checking numbers against the data.
3. **What it was not used for:** the paper text was written by the authors.
4. The authors take responsibility for all content and verified every reference and number.

Do not overstate or understate. If you later use a grammar tool, editing-only use does not require disclosure (per the call); disclose anything beyond that.

## Artifact (optional but recommended for SEAMS)

What goes in (prepared by the project lead on request):
- the analysis and figure scripts (`eval/paperb_*.py`);
- the CSV data (`paper_b/figure_data/`);
- the recorded raw inputs (diag runs, calibration ticks, request capture, runbook episodes; anonymized);
- a README with the exact commands.

Link it in the paper as an anonymized URL in a footnote or the Conclusion.

## Submission steps (HotCRP)

1. One author creates an account on <https://seams27.hotcrp.com/>. Register the paper early (title, abstract, authors), even before the PDF is final.
2. Select the research track and the **short paper** category; choose topics (self-healing; monitoring/runtime models; ML/RL for adaptation; cloud/fog/edge).
3. Declare conflicts of interest as the form asks.
4. Upload the anonymized PDF (≤ 6 + 1 pages). Check the HotCRP page-count/format report.
5. Re-download the uploaded PDF and check fonts, figures and anonymity.
6. Submit before **23 Oct 2026, 23:59 AoE** (that is 24 Oct, 17:29 in India).

## Final checklist before submitting

- [ ] Every number appears in `05_numbers.md` (or a CSV) and matches it.
- [ ] Every figure and table referenced in the text; captions written; Fig. 1 drawn.
- [ ] Every reference opened and verified by an author; no `VERIFY` notes left in `refs.bib` for cited entries; `\nocite{*}` removed.
- [ ] No claims from the "must NOT claim" list in `01_story_and_claims.md`.
- [ ] The server-side p95 is stated as the SLA signal; client-side p99 also reported.
- [ ] Threats section mentions: single application/node/link, closed-loop load, the capture disturbance, F2 one-worker marginal, cluster retuning before the reported runs.
- [ ] 6 + 1 pages; IEEE template unmodified; anonymous; AI statement present.
- [ ] Fact-check pass done (send the draft to the project lead to be checked against the data).
