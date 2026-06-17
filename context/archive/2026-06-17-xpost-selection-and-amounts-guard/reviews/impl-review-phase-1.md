<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X-post Selection & Amounts Guard

- **Plan**: context/changes/xpost-selection-and-amounts-guard/plan.md
- **Scope**: Phase 1 of 2
- **Date**: 2026-06-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Automated criteria re-verified: 1.1 (40 passed), 1.2 (165 passed), 1.3 (new files ruff-clean).
Manual criteria 1.4–1.6 confirmed against real BigQuery (2026-06-17 `ranek` replay + query round-trip).

## Findings

### F1 — Dedup-before-drop can discard a ticker with a valid numbered row at lower priority

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — matches plan; edge case rare
- **Dimension**: Plan Adherence
- **Location**: src/post_selection.py:62-79
- **Detail**: A ticker is marked seen on its first (top) occurrence, then dropped if that row is a number-less `wyniki_*`. A lower-priority numbered `wyniki` row for the same ticker is then skipped, so the company vanishes. Faithful to the plan's dedup→drop order; the corner is in the plan's algorithm, not the code. Real-world likelihood low (TOW case was 7 uniformly number-less cover-notes); Phase 2 belt is a second net.
- **Fix**: None now — matches plan intent. A per-company "prefer numbered results row" rule would be a plan change, better as a follow-up.
- **Decision**: SKIPPED — matches plan

### F2 — New src import lands under the file's E402 block

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — pre-existing convention
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:35
- **Detail**: `from src.post_selection import select_top_companies` is flagged E402 like the sibling `from src.exceptions import BigQueryError` directly above it. Follows the file's established convention; adds no new lint class. Repo already carries 51 pre-existing ruff errors.
- **Fix**: Leave as-is (matches sibling import). Repo-wide E402 cleanup is out of scope.
- **Decision**: SKIPPED — matches sibling

### F3 — Manual Progress row 1.4 title reworded to match reality

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — transparency note
- **Dimension**: Plan Adherence
- **Location**: plan.md Progress 1.4
- **Detail**: Convention says "do not rename step titles." Appended "; TOW correctly dropped as number-less wyniki_finansowe" because the original assertion ("yields TOW and ASB") became factually wrong once Defect B's drop is live — TOW is correctly absent. The reword documents verified reality rather than masking a failed check.
- **Fix**: Keep the reworded title (accurate).
- **Decision**: KEPT — reworded title retained per recommendation
