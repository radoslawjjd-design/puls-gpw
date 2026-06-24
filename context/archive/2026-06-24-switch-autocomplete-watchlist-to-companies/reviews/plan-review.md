<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Switch Autocomplete + Watchlist Validation to Companies

- **Plan**: context/changes/switch-autocomplete-watchlist-to-companies/plan.md
- **Mode**: Deep
- **Date**: 2026-06-24
- **Verdict**: SOUND
- **Findings**: 0 critical, 0 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | PASS |

## Grounding

8/8 paths verified, 6/6 symbols verified, brief↔plan consistent.

- `db/bigquery.py:1189-1219` (`list_distinct_tickers`/`list_distinct_companies`), `:463-519` (`companies` CRUD/`upsert_company`), `:105` (`_table_ref` signature) — all match plan's claims exactly.
- `src/company_profile.py` (`fetch_company_profile`, `_BANKIER_BASE_URL`, `CompanyProfile`) and `src/api.py:211-263` (autocomplete + watchlist guard) — match.
- `main.py:74` confirms the per-announcement `upsert_company` call the plan relies on for ongoing freshness.
- Blast-radius sweep: repo-wide grep for `list_distinct_tickers`/`list_distinct_companies` found exactly the 5 expected hits (`db/bigquery.py`, `src/api.py`, and the 3 test files the plan names). All 3 test-file call sites patch by full dotted name (`src.api.list_distinct_tickers`) with `return_value`/`side_effect`, not query-string assertions — confirms the plan's "none of those three files need any edits" claim.
- `scripts/seed_companies.py` matches the stated convention to mirror (`load_dotenv()` early, `--dry-run` flag, docstring invocation) exactly.
- `tests/test_bigquery.py:659-699` query-string assertions (`"SELECT DISTINCT ticker"`, `"LIMIT 500"`) confirmed as the exact ones the plan says need rewriting.
- Progress↔Phase mechanical contract: single `## Progress` heading; all 3 `## Phase N` blocks have matching `### Phase N` sections; every Success Criteria bullet (1.1-1.4, 2.1-2.7, 3.1-3.8) has a matching checkbox.

## Findings

None. The plan is well-grounded and internally consistent:

- The frame (`frame.md`) caught a gap the prior plan (PUL-53, archived) explicitly warned about and under-measured — 272 currently-active tickers that would silently break if the read-path switch shipped without a backfill first.
- The 3-phase sequencing (find gap → close gap → switch path) correctly gates the regression-risk switch behind a verified-closed gap.
- The backfill script is idempotent (relies on existing MERGE-based `upsert_company`) and safely re-runnable — no rollback story needed since it's purely additive.
- Phase 2's strict "coverage gap closes to 0" check (2.6) is exactly the rigor frame.md flagged as missing from PUL-53's weaker "row count in expected range" check — and the plan correctly keeps the looser range-check only where it's appropriately diagnostic (Phase 1's pre-backfill sanity check on the newly-written query).
- Fallback-row handling for per-ticker hop failures is concrete and matches `upsert_company`'s actual parameter order.
- No premature abstraction — `profile_url_for_ticker()` is a single-purpose URL builder reusing existing module state; no new patterns introduced where existing ones (seed script convention, MERGE upsert) already fit.
