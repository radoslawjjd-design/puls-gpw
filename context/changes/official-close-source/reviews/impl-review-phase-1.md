<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Official GPW close as the source for `company_daily_stats`

- **Plan**: `context/changes/official-close-source/plan.md`
- **Scope**: Phase 1 of 8
- **Date**: 2026-07-28
- **Commits**: `9466a2a` (phase), `f301e6c` (lint cleanup), `4a6fcc4` (review fixes)
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Success criteria

All four automated criteria verified on the committed tree: `uv run pytest --ignore=tests/e2e`
630 passed, `uv run ruff check .` clean, `uv run tach check` OK, and the four-site SQL assertion
set is complete. Manual criteria carry observable evidence — live `bq show --schema` output for
1.5/1.7, the round-trip's UPDATE-branch flip (`source` bankier→gpw, `kurs_odn` 99.5→100.0) for
1.6, and both a deletion and a test for 1.8.

## Blast radius

Two live writers into `company_daily_stats` remain after `seed_companies.py` was cut out, and both
were checked rather than assumed:

- `company_stats_main.py:87` supplies neither new key, so each of the 18 daily ticks writes NULL
  into both columns. Harmless: it only ever writes *today's* `snapshot_date`, while the self-heal
  (Phase 6) and the corrective pass (Phases 7–8) write earlier dates through the narrow primitive.
  The write sets never overlap, so nothing a later phase stamps can be erased by the daily job.
- `scripts/backfill_historical_closes.py:333` uses the insert-only path, which by construction has
  no MATCHED branch and cannot update.

`source` is not a BigQuery reserved keyword, and rather than trust that list the round-trip executed
a real `UPDATE SET source = S.source` against the live table — the reserved-keyword lesson
(`lessons.md:211-235`) is satisfied by execution, not by reading.

## Findings

### F1 — The seed_companies guard is evadable by aliasing

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `tests/test_seed_companies.py:20-29`
- **Detail**: The guard asserts `not hasattr(seed, name)` for three writer names. A future
  `from db.bigquery import merge_company_daily_stats as _m` binds a different attribute name and
  slips through — the exact clobber the test exists to prevent would return silently.
- **Fix**: Add a source-level assertion that the script body never names `company_daily_stats`,
  which no alias can dodge.
- **Decision**: FIXED

### F2 — Type check uses a 5-char prefix slice

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `scripts/test_bq_company_stats_merge.py:47-49`
- **Detail**: `field_type.startswith(expected_type[:5])` compared "STRIN"/"FLOAT" prefixes to bridge
  FLOAT vs FLOAT64. It worked, but the slice is unexplained cleverness inside a script whose entire
  job is being an unambiguous check.
- **Fix**: Map each column to a set of accepted type names and use `in`.
- **Decision**: FIXED

### F3 — Lint cleanup touched 8 files outside this change

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `f301e6c`
- **Detail**: The commit edits `_run_generate_post.py`, two scripts, four test files and
  `pyproject.toml` — none in the plan. User-directed at the Phase 1 gate after the baseline of 33
  ruff findings made "linting passes" unachievable as written; isolated in its own commit with no
  behaviour change.
- **Fix**: None needed — recorded so a later reader of this branch isn't puzzled by the extra files.
- **Decision**: ACCEPTED

## Note on `change.md` status

Left at `implementing` rather than advanced to `impl_reviewed`. This review covers Phase 1 of 8;
flipping the change-level status would misrepresent the remaining seven phases.
