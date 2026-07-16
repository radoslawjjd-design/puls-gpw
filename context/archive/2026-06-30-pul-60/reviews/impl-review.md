<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: pul-60 — Performance

- **Plan**: context/changes/pul-60/plan.md
- **Scope**: All phases (1–6)
- **Date**: 2026-06-30
- **Verdict**: APPROVED (after fixes)
- **Findings**: 0 critical / 3 warnings / 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS (407/407 tests) |

## Findings

### F1 — _applyUrlState() bypasses _watchlistFetched guard

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality (Reliability/Performance)
- **Location**: static/index.html ~line 1486
- **Detail**: showMyWalletView() correctly guards fetchWatchlistTickers() but _applyUrlState() (popstate = back/forward) called both fetches unconditionally. Every browser back/forward to Obserwowane fired an extra GET /watchlist bypassing the guard. E2E test only covered tab-click navigation.
- **Fix**: Wrapped both fetches in `_applyUrlState()`'s my-wallet branch with `if (!_watchlistFetched) { ... }`, mirroring showMyWalletView().
- **Decision**: FIXED

### F2 — Migration --execute does not check job.errors

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (Data Safety)
- **Location**: scripts/migrate_announcements_partition.py lines 87–89
- **Detail**: After job.result(), script logged "done" without checking job.errors. BQ DML jobs can populate job.errors without raising an exception. Silent failure on step 2 would leave the un-partitioned table in place. Every other BQ write in the codebase checks job.errors.
- **Fix**: Added `if job.errors: raise RuntimeError(f"Migration step {i} failed: {job.errors}")` after job.result().
- **Decision**: FIXED

### F3 — 90-day default silently hides old admin announcements

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality (Reliability)
- **Location**: db/bigquery.py list_announcements_admin → _build_filter_clauses
- **Detail**: The 90-day default applies to all three callers (admin, user, watchlist). Admin auditing announcements older than 90 days got zero results silently. User path is fine (recency makes sense); admin path needs full history access.
- **Fix A (applied)**: `list_announcements_admin` now passes `from_dt=from_dt if from_dt is not None else datetime.min`, bypassing the 90-day default. Admin sees full history by default; explicit from_dt still overrides. Safe because Phase 5 added PARTITION BY published_at.
- **Decision**: FIXED (Fix A)

### F4 — Fixture name _clear_ac_cache stale after pul-60

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_api.py line 18
- **Detail**: Fixture cleared both _AC_CACHE and _PERF_CACHE but was still named _clear_ac_cache.
- **Fix**: Renamed to _clear_caches.
- **Decision**: FIXED

### F5 — E2E login helper parameter naming (already compliant)

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: tests/e2e/test_watchlist_guard.py line 16
- **Detail**: Agent reported parameter as live_server_url but actual file already used base_url — already consistent with seed. No change needed.
- **Decision**: DISMISSED (already correct)

### F6 — asyncio.gather without return_exceptions=True

- **Severity**: OBSERVATION — no action required
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: src/api.py lines 437–451
- **Detail**: Default behavior (propagate first exception) is correct for stateless BQ reads. In-flight threads complete but results are discarded. Acceptable pattern.
- **Decision**: SKIPPED
