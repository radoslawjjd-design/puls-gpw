<!-- PLAN-REVIEW-REPORT -->
# Plan Review: F-02 BigQuery Schema `announcements` + Python Client

- **Plan**: context/changes/bigquery-schema/plan.md
- **Mode**: Deep
- **Date**: 2026-06-02
- **Verdict**: REVISE → SOUND (all findings resolved)
- **Findings**: 0 critical, 2 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|---|---|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

5/5 paths ✓ (pyproject.toml, main.py, uv.lock, db/ new, scripts/test_bq.py new), 2/2 symbols ✓, brief↔plan ✓

## Findings

### F1 — Streaming insert + DML UPDATE/DELETE incompatibility

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 (insert_announcement / save_analysis) + Phase 3 (test script)
- **Detail**: insert_rows_json() (streaming) + DML UPDATE/DELETE nie mogą operować na wierszu w buforze. Test script (INSERT → DELETE natychmiast) nie sprzątnie. Produkcja krucha przy szybkim uruchomieniu.
- **Fix A ⭐ Recommended**: DML INSERT zamiast streaming insert — `client.query("INSERT INTO ... VALUES ...")` natychmiast spójne z DML UPDATE/DELETE.
- **Decision**: FIXED (Fix A) — insert_announcement() używa DML INSERT; zaktualizowano kontrakt w Phase 2.

### F2 — Automated verification: `python -c` zamiast `uv run python -c`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 kryterium 1.2, Phase 2 kryterium 2.1
- **Detail**: `python -c` nie znajdzie pakietów z uv venv. /10x-implement dostałby ImportError.
- **Fix**: Zamień `python -c` na `uv run python -c` w 1.2 i 2.1 (+ Progress section).
- **Decision**: FIXED — zaktualizowano kryteria 1.2, 2.1 i odpowiednie wpisy w ## Progress.
