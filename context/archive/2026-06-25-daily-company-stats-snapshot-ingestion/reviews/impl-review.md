<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Daily company-stats snapshot ingestion

- **Plan**: context/changes/daily-company-stats-snapshot-ingestion/plan.md
- **Scope**: All phases (1–3 completed; Phase 4 pending)
- **Date**: 2026-06-26
- **Verdict**: NEEDS ATTENTION (all findings resolved in triage)
- **Findings**: 2 critical  3 warnings  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING — significant architecture pivot undocumented (fixed) |
| Scope Discipline | WARNING — append-only constraint reversed (documented) |
| Safety & Quality | FAIL → PASS after fixes (F1 guard, F2 lint) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | FAIL → PASS after F2 lint fix |

## Findings

### F1 — Unconditional delete when rows list is empty

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: company_stats_main.py:84-86
- **Detail**: fetch_listing_page() returns {} on HTTP/parse failure. When both markets fail, rows = [] and delete_company_daily_stats_for_date runs unconditionally, wiping today's data. Job exits 0, no alert.
- **Fix**: Added `if not rows: raise RuntimeError(...)` guard before the delete call.
- **Decision**: FIXED — company_stats_main.py; tests updated

### F2 — Unused BigQueryError import (lint failure)

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: company_stats_main.py:17
- **Detail**: BigQueryError imported but unused after per-row try/except was removed. ruff F401.
- **Fix**: Removed unused import.
- **Decision**: FIXED — company_stats_main.py

### F3 — No alert on total listing-page scrape failure

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: company_stats_main.py:40-49
- **Detail**: If bankier changes HTML structure, job exits 0 with 0 rows processed, no send_alert.
- **Decision**: SKIPPED — covered by F1 fix (empty rows → raise → outer except → send_alert)

### F4 — Plan drift undocumented

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Plan Adherence + Scope Discipline
- **Location**: plan.md (Overview, Phase 1-2, Performance, "What We're NOT Doing")
- **Detail**: Three undocumented pivots: (1) append-only → delete+replace, (2) per-ISIN JSON API → HTML listing pages, (3) --task-timeout=1800s rationale stale (job runs in 6s).
- **Decision**: FIXED — plan.md updated with "Implemented Architecture" section, pivot notes, corrected timeout

### F5 — delete_company_daily_stats_for_date missing job.errors check

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:1364-1368
- **Detail**: Pattern inconsistency vs add_watchlist_ticker which checks job.errors after .result().
- **Decision**: FIXED — added job.errors check for consistency

### F6 — Phase 4 runbook --task-timeout=1800s stale

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: plan.md Phase 4 contract
- **Detail**: 1800s timeout justified by ~15min sequential DML loop. Actual runtime ~6s.
- **Decision**: FIXED — updated to standard 300s in plan.md

### F7 — Per-row BigQueryError isolation lost

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Architecture
- **Location**: company_stats_main.py
- **Detail**: Plan Phase 3 specified per-ticker try/except BigQueryError. Batch insert is all-or-nothing. Acceptable trade-off for the performance gain.
- **Decision**: ACCEPTED — documented in plan's "Implemented Architecture" section
