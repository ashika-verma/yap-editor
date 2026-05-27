# Tighten Rewrite — Baseline Eval Scores

Captured 2026-05-27 against the **current** pipeline (segment-based keep/drop + Continuity Guard).
These numbers are the Task 8 gate: new pipeline must hit **coherence ≥ 85 AND preservation ≥ 92**.

## Per-fixture

| Fixture | Cut% | Coherence | Preservation | Conciseness | Avg |
|---------|------|-----------|--------------|-------------|-----|
| 0fae1b9d_20260516_175938 | 39% | 85 | 92 | 90 | 89.0 |
| 55f23d95_20260520_145230 | 36% | 85 | 92 | 88 | 88.3 |
| 8e4f610b_20260520_144257 | 57% | 85 | 92 | 90 | 89.0 |
| c258c69a_20260520_145808 | 48% | 85 | 92 | 90 | 89.0 |
| d0576cca_20260520_145458 | 32% | 85 | 92 | 90 | 89.0 |

## Means

| Metric | Score |
|--------|-------|
| Coherence | **85.0** |
| Preservation | **92.0** |
| Conciseness | 89.6 |
| Audio | 99.2 |
| **Average** | **88.9** |

## Gate (Task 8)

Pass requires: coherence ≥ 85 **AND** preservation ≥ 92 (mean across same 5 fixtures).
Conciseness is informational only.

## Notes

- All 5 fixtures scored exactly coherence=85 / preservation=92 — consistent baseline.
- Primary coherence weakness across fixtures: abrupt transitions between major thematic blocks
  when connective segments were dropped. New holistic approach should fix this.
- Eval results file: `eval_results/2026-05-27_173908.json`
