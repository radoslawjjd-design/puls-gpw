<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Backfill Historical Daily Closes (stooq)

- **Plan**: `context/changes/backfill-historical-closes/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-24
- **Verdict**: REVISE → **SOUND** (after triage fixes)
- **Findings**: 1 critical, 1 warning, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING (fixed in triage) |
| Plan Completeness | FAIL (mechanical; fixed in triage) |

## Grounding

6/6 paths ✓, 7/7 symbols ✓, brief↔plan ✓. Deep verification: 5/5 risky claims CONFIRMED by code — (1) merge functions resolve table-name constants at call time → round-trip monkeypatch feasible (`db/bigquery.py:2461-2463`, `:2677-2679`); (2) `.result()` + `.errors` pattern in place; `num_dml_affected_rows` precedent at `scripts/migrate_owner_identity.py:96`; (3) blast radius clean — only 4 readers of both tables, all date-bounded/deduped/NULL-safe; zero readers of `wartosc_obrotu`/`liczba_transakcji`; (4) unit-test mock pattern directly copyable (`tests/test_bigquery.py:1234,1274`, `tests/test_etf_bigquery.py:151`); (5) http_client env names confirmed (`src/http_client.py:12-15`).

Side discovery: the "7-day ETF quote expiry" memory is explained — it is a freshness **filter** in `list_user_portfolio_positions` (`db/bigquery.py:832`, `:842`), not a table/partition expiry. The cheap `bq show` check in Phase 3.2 stays as a formality.

## Findings

### F1 — Phase 3 "Automated (none)" breaks the Progress contract

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 — Success Criteria
- **Detail**: Phase 3 block had an `#### Automated Verification:` heading with a "(none — …)" pseudo-bullet while Progress (correctly) has no Automated subsection for Phase 3 — an orphan bullet the `/10x-implement` parser may choke on.
- **Fix**: Remove the Automated heading + pseudo-bullet from the Phase 3 block (operational phases carry Manual-only criteria).
- **Decision**: FIXED

### F2 — No fallback when the PoW solver breaks

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 — stooq session bootstrap
- **Detail**: Bootstrap parses `c`/`d` constants out of stooq's inline challenge JS; a served variant breaks the solver and stalls the multi-day rollout with no workaround.
- **Fix A ⭐ Recommended**: Keep the solver as default, add `--cookie "<Cookie header>"` escape hatch (browser-sourced cookie).
  - Strength: Rollout can't be blocked by an anti-bot change; ~10 lines of CLI.
  - Tradeoff: Slightly more code; cookies expire and need refreshing per emergency run.
  - Confidence: HIGH — challenge verified live today; c/d format simple to parse.
  - Blind spot: Post-verification cookie lifetime unknown.
- **Fix B**: Manual cookie only (no solver).
  - Strength: Less code, zero solver-breakage risk.
  - Tradeoff: Every run (incl. daily-limit resumes) needs a manual browser step.
  - Confidence: MED — cookie portability browser→httpx untested.
  - Blind spot: Cookie may be bound to UA/IP fingerprint.
- **Decision**: FIXED (Fix A)

### F3 — Derived `zmiana_*` on ex-dividend days is "raw"

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Blind Spots
- **Location**: Phase 2 — derived fields
- **Detail**: Raw consecutive closes show the mechanical drop on ex-div days; scraper rows compute change vs GPW's dividend-adjusted reference — semantics differ on those days.
- **Fix**: Documented in Critical Implementation Details (accepted, consistent with the raw-prices decision).
- **Decision**: FIXED

### F4 — `get_latest_company_stats_fetched_at` LIMIT 1 without ORDER BY

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Blind Spots
- **Location**: `db/bigquery.py:2747`
- **Detail**: If the backfill fills a recent scraper-outage gap, the treemap "as of" timestamp for that date may arbitrarily show the backfill's `fetched_at`. Cosmetic, outage-days only.
- **Fix**: Noted in plan as known/accepted; no query change.
- **Decision**: FIXED

### F5 — `--limit` interplay with resume-skip unspecified

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 2 — CLI contract
- **Detail**: If `--limit` counted resume-skipped tickers, successive runs would burn the cap on skips and never reach new tickers.
- **Fix**: CLI contract now states `--limit` counts only tickers actually fetched.
- **Decision**: FIXED
