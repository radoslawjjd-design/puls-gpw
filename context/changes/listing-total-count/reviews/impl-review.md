<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X-Total-Count on the listing endpoints (PUL-77)

- **Plan**: `context/changes/listing-total-count/plan.md`
- **Scope**: Phases 1–2 of 2
- **Date**: 2026-08-04
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING — one out-of-scope fix, forced (F1) |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (automated); browser check pending |

## Findings

### F1 — A pre-existing flake would have blocked the release PR

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: `tests/test_gpw_archive.py` (`test_archive_paces_consecutive_fetches`)
- **Detail**: The full suite went red once during this ticket and green on a
  re-run. The cause is not this change: the test does
  `patch("src.gpw_archive.time.monotonic", side_effect=[100.0, 100.0, 100.2, 100.2])`,
  which reaches into the **shared stdlib `time` module**, so every other thread
  alive in the session draws from that four-value list — the uvicorn server the
  session-scoped E2E fixture leaves running above all. Exhaust it and the test
  dies with `StopIteration`, but only on unlucky ordering and timing.
- **Fix**: Patch the module *name* in `src.gpw_archive` rather than an attribute
  on the module object, so only that module's own lookups are affected.
  - Strength: Removes the shared-state race rather than making it rarer. Other
    threads keep the real `time`.
  - Tradeoff: Touches a test unrelated to this ticket, which is normally exactly
    what a review objects to.
  - Confidence: HIGH — the mechanism is exact and the fix is local.
  - Blind spot: Three sibling tests patch `src.gpw_archive.time.sleep` the same
    way. They are not racy today because `sleep` has no `side_effect` list to
    exhaust, so nothing forced them into this diff — but they carry the same
    shape and would be worth the same treatment next time that file is touched.
- **Decision**: FIXED — justified because `Tests` is a required status check on
  master, so a flake here does not merely annoy: it blocks this release.

### F2 — The ticket's cache warning does not apply

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `src/api.py` (all three endpoints)
- **Detail**: The ticket says *"Remember perf caches on these endpoints — the
  cached value must include the count."* None of the three uses `_perf_get` /
  `_perf_set`; the caches in this file serve the portfolio, treemap, calendar and
  autocomplete paths. Recorded so a later reader does not go looking for a bug
  that was never there — and so it is noticed if caching is ever added, because
  then the count genuinely would have to travel with the cached value.
- **Decision**: NO CHANGE NEEDED.

### F3 — The return-type change is the whole cost of the ticket

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architecture
- **Location**: `db/bigquery.py`, ~25 test sites
- **Detail**: Moving four functions from `list[dict]` to `ListingPage` touched
  every mock and every test that consumed their return value. The alternatives
  were worse: a second COUNT query doubles the scan on every request (the thing
  the ticket set out to avoid), and smuggling the total inside the row dicts would
  have leaked a `_total` key into Pydantic model construction. `NamedTuple` keeps
  the churn mechanical — a fake returns `([...], 7)` and never imports from `db`.
- **Decision**: ACCEPTED.

## Success Criteria

| Check | Result |
|-------|--------|
| Every listing query carries `COUNT(*) OVER()` | PASS |
| Total independent of page_size | PASS |
| A page past the end reports the true total | PASS |
| An empty first page costs no second query | PASS |
| All three endpoints send `X-Total-Count` | PASS |
| `Access-Control-Expose-Headers` names it | PASS |
| Response bodies unchanged | PASS — asserted as a plain array |
| `uv run pytest --tb=short` | PASS — 1072 passed, twice |
| `uv run ruff check` | PASS |
| Browser network panel | PENDING — human check |

## Notes

- **The window semantics were verified against production, not assumed.** The unit
  tests mock the BigQuery client, so they prove the SQL is *sent*, not that it
  *means* what we think. Run for real: a separate `COUNT(*)` over the approved
  announcements returns 4094, and `COUNT(*) OVER()` read off the first page of 20
  returns 4094 as well — the window is evaluated before `ORDER BY` and `LIMIT`,
  which is the entire premise of the design.
- **The empty-page fallback is the one piece the ticket did not ask for.** Without
  it the header reports 0 for any page past the end — visible in exactly the place
  the number is displayed, and self-inflicted, since the count is the thing the UI
  uses to know which pages exist.
- **The filter lives in one place.** Each function builds a single `body` fragment
  holding `FROM`/`JOIN`/`WHERE`, used by both the page query and the fallback
  COUNT, so the two cannot drift into disagreeing about what they are counting.
- No other callers exist outside `src/api.py` and the tests — checked across
  `scripts/` and the entry points.
