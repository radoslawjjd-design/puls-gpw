<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Daily company-stats snapshot ingestion — Implementation Plan

- **Plan**: `context/changes/daily-company-stats-snapshot-ingestion/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-25
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 1 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL → fixed |
| Plan Completeness | PASS |

## Grounding

12/12 paths ✓, 6/6 symbols ✓, brief↔plan ✓ — verified `db/bigquery.py` (companies schema,
`add_watchlist_ticker`, `list_distinct_tickers`/`list_distinct_companies`/
`list_tickers_missing_from_companies`, `_get_client`, `ensure_schema_current`),
`src/company_profile.py`, `src/http_client.py`, `src/parser.py`, `main.py`, `post_main.py`,
`.github/workflows/deploy.yml`, `context/foundation/infra.md`, `src/logging_setup.py`.

## Findings

### F1 — Sequential per-ticker work may exceed the 300s job timeout

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Phase 1 (insert function) + Performance Considerations
- **Detail**: The per-ticker loop does one HTTP fetch + one synchronous BQ DML query per
  company, sequentially. Phase 4 originally committed to reusing the existing 300s Cloud Run Job
  timeout verbatim. A few hundred companies at ~1-3s BQ overhead each (plus HTTP latency) could
  approach or exceed that budget. A Cloud Run timeout kill is a SIGTERM/SIGKILL that bypasses the
  outer try/except + `send_alert` — a timed-out run would produce no failure signal at all.
- **Fix A ⭐ Recommended**: Raise the Cloud Run Job's `--task-timeout` in Phase 4's provisioning
  runbook instead of reusing 300s verbatim.
  - Strength: Zero code change; Cloud Run Jobs support up to 24h timeouts.
  - Tradeoff: A genuinely stuck run burns more wall-clock time before being killed.
  - Confidence: HIGH — documented Cloud Run Jobs behavior.
  - Blind spot: actual `companies` row count unverified — confirm before provisioning.
- **Fix B**: Batch the dedup-check + insert into one query pair instead of N per-ticker queries.
  - Strength: Cuts BQ round-trips from ~2N to ~2.
  - Tradeoff: More code; changes Phase 1's insert-function contract.
  - Confidence: MEDIUM — HTTP fetch time still unbatched.
  - Blind spot: real BQ query latency in this project/region unverified.
- **Decision**: FIXED (Fix A) — plan now sets `--task-timeout=1800s` in Phase 4's provisioning
  runbook, adds a pre-provisioning row-count check, and Performance Considerations/Progress
  updated to match.

### F2 — Fetch-failure skip relies on a DEBUG-level log invisible at production INFO level

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2 + Phase 3 contracts
- **Detail**: `fetch_daily_stats` mirrored `fetch_company_profile`'s failure shape, which logs at
  DEBUG (`src/company_profile.py:31`) — invisible at the project's production INFO log level
  (`src/logging_setup.py:8`). Phase 3's entrypoint deferred to that inner log instead of logging
  the skip itself, so a real fetch failure would be silently invisible in production —
  contradicting the plan's own promise that a failed fetch "is skipped and logged."
- **Fix**: Log fetch failures at `logger.warning(...)` (not `.debug`) inside `fetch_daily_stats`
  — only the return-`None` behavior needs to mirror `fetch_company_profile`, not its log level.
- **Decision**: FIXED — Phase 2 contract now specifies `logger.warning(...)` for this path,
  explicitly calling out the deviation from `fetch_company_profile`'s DEBUG level and why.

### F3 — REQUIRED-mode columns can't be added later via the additive-ALTER path

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 1, table schema
- **Detail**: `fetched_at` (TIMESTAMP, REQUIRED) is fine at initial creation, but
  `ensure_schema_current()`'s additive `ALTER TABLE ADD COLUMN` only succeeds for NULLABLE
  columns in BigQuery — a future REQUIRED field added the same way would crash at job startup
  instead of no-op.
- **Fix**: Add a one-line guard comment above the new `_SCHEMA` list.
- **Decision**: FIXED — Phase 1 contract now includes the guard-comment instruction.
