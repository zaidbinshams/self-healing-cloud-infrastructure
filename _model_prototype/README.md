# PER-iSAC prototype (synthetic)

A preliminary masked discrete SAC + PER agent for the self-healing Online Boutique project, run end to end on a
**synthetic toy fault simulator** so the code, data formats, metrics and figures can be exercised before M4.

- Start with `REPORT.md`: expected results, the two simulated test runs, findings and recommendations.
- The mathematical formulation is kept in a separate document.

**Every result here is synthetic** (`data_origin: SYNTHETIC_TOY_SIMULATOR`, red watermark on every figure).
Do not present it as cluster data. Keep this folder out of the main repo's `env/`, `agents/` and `eval/`
(CLAUDE.md §4.1). The pure-math pieces (`core.py`, `masked_sac.py`, `per_buffer.py`, `runbook.py`, the tests)
are written to the contract and can be ported once the M4 Tianshou route is decided.
