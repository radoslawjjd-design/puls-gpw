<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Companies Dictionary Table (ticker, name, hop_url, isin)

- **Plan**: context/changes/companies-dictionary-table/plan.md
- **Scope**: Phase 3 of 4 — Wire Phase A: pipeline write + startup hooks + test mocks
- **Date**: 2026-06-23
- **Verdict**: APPROVED
- **Findings**: 0 critical · 1 warning · 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Best-effort-swallow test landed in a new `tests/test_main.py`, not `tests/test_bigquery.py`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: tests/test_main.py (new file)
- **Detail**: The plan's Contract for this phase said: *"tests/test_bigquery.py — add a unit test asserting main.py's new best-effort try/except swallows a BigQueryError from upsert_company without propagating."* The implementation instead created a brand-new `tests/test_main.py` that monkeypatches every `main.py` collaborator and calls `main.main()` directly (`test_upsert_company_failure_does_not_abort_pipeline`, `test_happy_path_upserts_company`).

  This is a deliberate, well-reasoned deviation rather than an oversight: `tests/test_bigquery.py`'s established pattern mocks `db.bigquery._get_client` only — it has no way to exercise `main.py`'s local `try/except BigQueryError` block, which lives in `main.py`, not `db/bigquery.py`. Testing the actual swallow behavior requires mocking `main.py`'s own module-level imports, which is exactly what the new file does. The plan's stated location was impractical for what the Contract asked to prove.
- **Fix**: Accept `tests/test_main.py` as the correct home and add a one-line addendum to Phase 3's Contract in plan.md noting the location change and why — no code change needed.

### F2 — Lint success criterion unverifiable in this environment

- **Severity**: ⚪ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: N/A (environment, not code)
- **Detail**: `uv run ruff check main.py src/api.py tests/test_bigquery.py tests/e2e/conftest.py tests/test_main.py` fails with `Failed to spawn: ruff — program not found`. `ruff` is not installed in this local venv and is not listed in `pyproject.toml` dev dependencies, so this is a pre-existing environment gap, not something introduced by this phase. Full unit suite (57 passed) and full E2E suite (47 passed) both ran clean.
- **Fix**: Run lint wherever it's actually available (CI, or `uv add --dev ruff` locally) before merging; not a phase-3 regression.

## Evidence

- `uv run pytest tests/test_bigquery.py tests/test_main.py -q` → 57 passed
- `uv run pytest tests/e2e -q` → 47 passed
- `git show a6884d5` reviewed in full: `main.py`, `src/api.py`, `tests/e2e/conftest.py`, `tests/test_main.py` (new), `plan.md` (progress checkboxes only)
- Verified `upsert_company`/`create_companies_table_if_not_exists`/`ensure_companies_schema_current` signatures in `db/bigquery.py` match all new call sites in `main.py` and `src/api.py`
- Verified the `_create_watchlist_table` → `_init_dimension_tables` rename in `src/api.py` has no other call sites (grep matches were unrelated test names containing the same substring)
- Confirmed `parsed.ticker` is guaranteed truthy at the `upsert_company` call site (the `if not parsed.ticker: continue` guard runs earlier in the same loop iteration)

## Decisions

### F1 — Best-effort-swallow test landed in a new `tests/test_main.py`

- **Decision**: FIXED — added an addendum to Phase 3's Contract in plan.md documenting the location change and why (test_bigquery.py's mocking pattern can't reach main.py's local try/except).

### F2 — Lint success criterion unverifiable in this environment

- **Decision**: FIXED — installed `ruff>=0.15.18` as a dev dependency; added `[tool.ruff.lint.per-file-ignores]` for `main.py`/`src/api.py` (E402, documenting the deliberate load_dotenv-before-import convention); removed the stale unused `httpx` import in `tests/e2e/conftest.py` (predated this phase, from the pagination feature). `ruff check` on all phase-3-touched files now passes clean. Repo-wide `ruff check .` surfaces 38 pre-existing errors in unrelated files (test_scraper.py, test_post_generator.py, etc.) — out of scope for this review, flagged as a separate backlog item.
