# 07 — Format and Submission Checklist

## Format (until the guide picks a venue)
- IEEE conference template (IEEEtran, `conference`, 10 pt, two columns), **≤ 7 pages including references** (the guide's limit).
- If the chosen venue is double-anonymous: anonymous author block, no repository links that identify you, own prior work (Paper B) in the third person.
- Figures are vector PDF. They are drawn at one-column width (3.4 in), except Fig. 3, which is full width. Fonts are embedded (Type 42).
- Compile: Overleaf, pdfLaTeX + BibTeX. Remove `\nocite{*}` once the text cites with `\cite{}`.

## AI-assistance disclosure (keep the team's rule)
- The paper text is written by the authors.
- AI assistance (Claude Code) was used for software, data analysis, figure generation, literature search and the structure/fact pack in this folder. Disclose it in the venue's required place and wording (IEEE: acknowledgments section, naming the tool and what it was used for).
- Every reference must have been opened by a team member; the statuses in `06_references.md` show which still need it.

## Artefact (if the venue allows)
- Code (`env/`, `agents/`, `eval/`, `scripts/`), `config/` (contract, calibration, golden snapshot), `eval/schedules/eval_v1.json`, `docs/PREREGISTRATION.md`, `paper_a/figure_data/`.
- Raw transitions (`data/`) are not in the repository. Offer them on request, or as an archive, after anonymization.

## Final checklist
- [ ] Every number in the text matches `05_numbers.md` (one person reads the whole paper against it).
- [ ] The H1 F4 result (HPA faster) and H5 (not established) are stated plainly, in the Results and in Table VI.
- [ ] Claims stay inside "What we may claim" (`01_story_and_claims.md`).
- [ ] The limitations paragraph covers every item in the `01` list.
- [ ] The pre-registration and its 8 amendments are mentioned, with dates in the artefact.
- [ ] References are opened and the VERIFY fields resolved; uncited entries are removed.
- [ ] Page count ≤ 7; figures are readable in print (grayscale check: line styles and markers differ per policy).
- [ ] The AI disclosure is in place.
