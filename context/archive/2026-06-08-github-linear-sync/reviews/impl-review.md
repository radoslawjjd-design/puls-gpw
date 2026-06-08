<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: GitHub ↔ Linear Sync

- **Plan**: context/changes/github-linear-sync/plan.md
- **Scope**: Phase 1 + Phase 2 of 2
- **Date**: 2026-06-08
- **Verdict**: APPROVED (all findings fixed during triage)
- **Findings**: 0 critical  3 warnings  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING (3 findings — all fixed) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (2.6 pending scheduler propagation) |

## Findings

### F1 — No concurrency guard on 5-minute schedule

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: .github/workflows/sync-linear-to-github.yml:5
- **Detail**: Cron fires 288×/day with no concurrency group — slow runs stack up rather than cancel.
- **Fix**: Added `concurrency: group: linear-github-sync, cancel-in-progress: true` block.
- **Decision**: FIXED via Fix

### F2 — Linear API failure silently exits 0

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: .github/workflows/sync-linear-to-github.yml:72-80
- **Detail**: Both Linear failure branches called sys.exit(0) with no GHA annotation — sync could be silently broken.
- **Fix A ⭐**: Replaced bare `WARNING:` prints with `::warning::` GHA annotations before exit 0.
- **Decision**: FIXED via Fix A

### F3 — Unguarded bulk reopens on Linear state regression

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: .github/workflows/sync-linear-to-github.yml:98-109
- **Detail**: No rate-limit on reopens — a bad bulk state change in Linear propagates to all GitHub issues within 5 minutes.
- **Fix A ⭐**: Added `reopen_count` counter; emits `::warning::` annotation if >2 issues reopened in one run.
- **Decision**: FIXED via Fix A

### F4 — Truncation warning prints to stdout, not stderr

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: .github/workflows/sync-linear-to-github.yml:88
- **Detail**: Bare `print()` instead of `file=sys.stderr` with GHA annotation — doesn't appear in Actions summary.
- **Fix**: Replaced with `::warning::` annotation to stderr.
- **Decision**: FIXED

### F5 — 401/403 from Linear treated identically to transient errors

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: .github/workflows/sync-linear-to-github.yml:72-76
- **Detail**: Auth failures (never self-heal) treated same as network hiccups (transient).
- **Fix**: Added specific `urllib.error.HTTPError` handler that raises on 401/403, falls through to soft-fail for other HTTP errors.
- **Decision**: FIXED
