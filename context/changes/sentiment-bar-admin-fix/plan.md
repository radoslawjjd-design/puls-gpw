# Sentiment Bar Admin Fix — Implementation Plan

## Overview

Fix the faro-v2 "Sentyment 7 dni" bar on the Obserwowane view: today it renders for every
logged-in user but can never show real data, because `GET /announcements/my-wallet` strips
`sentiment` unconditionally and never returns `analysis_score`. After this change, the
endpoint mirrors the `/announcements` admin/user split (admin gets sentiment + score), and
the frontend fetches/renders the bar only for the admin role.

Tracking: Linear PUL-82, GitHub #141 (shipped-in: PR #135).

## Current State Analysis

Root cause verified in session 2026-07-17 (see `change.md` Notes):

- `fetchWlSentimentSummary()` (`static/index.html:1844`) fetches
  `/announcements/my-wallet?page=1&page_size=100`, filters to the last 7 days, and counts
  `structured_analysis.sentiment` values + averages `analysis_score`. No role check — every
  user sees the bar shell with zeros.
- `GET /announcements/my-wallet` (`src/api.py:415`) has NO admin branch: every role goes
  through `AnnouncementUser` (no `analysis_score` field, `extra="ignore"`) and
  `structured_analysis.pop("sentiment")` (`src/api.py:432`).
- `GET /announcements` (`src/api.py:307-331`) already implements the exact target pattern:
  `role == "admin"` → `AnnouncementAdmin` (has `analysis_score`, keeps sentiment), else →
  `AnnouncementUser` + sentiment pop.
- `list_announcements_for_watchlist` (`db/bigquery.py:1588`) SELECTs only
  `company, ticker, event_type, structured_analysis, published_at` — **no `analysis_score`
  column**, so the API layer alone cannot fix the average score.
- App convention: sentiment/score are admin-only (announcement modal gates at
  `static/index.html:3190`).
- E2E mock `_FAKE_WATCHLIST_ANNOUNCEMENT` (`tests/e2e/conftest.py:150`) has
  `structured_analysis: None` — insufficient for a populated-bar assertion.

## Desired End State

- Admin on Obserwowane sees the bar with real counts (pozytywny/neutralny/negatywny),
  average score, and announcement count.
- Regular user never sees the bar and the browser issues no sentiment fetch.
- User-role response contract unchanged: no `sentiment`, no `analysis_score` — guarded by a
  unit regression test.

### Key Discoveries:

- Mirror pattern exists 1:1 at `src/api.py:307-331` — no new design needed.
- `AnnouncementAdmin.analysis_score` already defined (`src/api.py:168`).
- `analysis_score` is not a BQ reserved keyword — plain column addition to the SELECT
  (lessons.md reserved-keyword rule checked, backticks not required).
- Docstring of `list_announcements_for_watchlist` says "Same returned column set as
  `list_announcements_user`" — update it, since the column set changes.

## What We're NOT Doing

- No change to the bar's aggregation window (7 days) or fetch size (`page_size=100`).
- No change to `GET /announcements` or any other endpoint.
- No sentiment exposure for regular users anywhere (product decision stands).
- No caching changes; `/announcements/my-wallet` has no perf-cache today and gains none.
- Not making the bar configurable or per-wallet.

## Implementation Approach

Smallest change that restores the designed behavior: one column added to the BQ SELECT, one
role branch in the endpoint copied from `/announcements`, one early-return in the frontend
function, tests on both levels. Two phases, each independently green and committable.

## Phase 1: Backend — admin branch on /announcements/my-wallet

### Overview

Make the endpoint return sentiment + score to admins while keeping the user contract
byte-identical.

### Changes Required:

#### 1. BQ query — add score column

**File**: `db/bigquery.py`

**Intent**: `list_announcements_for_watchlist` must also return `analysis_score` so the API
layer can expose it to admins. Exposure control stays in the API layer — the DB function
returns the column for every caller.

**Contract**: Add `a.analysis_score` to the SELECT list (`db/bigquery.py:1612-1614`).
Update the docstring ("Same returned column set as `list_announcements_user`" no longer
holds — name the delta). Cheap regression assert on the query string in unit tests
(lessons.md: mocked BQ tests don't verify SQL syntax).

#### 2. Endpoint — role branch

**File**: `src/api.py`

**Intent**: Mirror the `/announcements` admin/user split in `announcements_my_wallet`
(`src/api.py:415-439`): admin serializes rows through `AnnouncementAdmin` with
`structured_analysis` parsed but NOT stripped; user path stays exactly as today
(`AnnouncementUser` + `pop("sentiment")` — `extra="ignore"` silently drops the new
`analysis_score` key, so the user branch needs no code change beyond what exists).

**Contract**: Response for `role == "admin"`: list of `AnnouncementAdmin.model_dump()`
including `analysis_score` and `structured_analysis.sentiment`. Response for user role:
unchanged field set (regression-locked by test).

#### 3. Unit tests — role contract

**File**: `tests/test_api.py`

**Intent**: Lock the contract for both roles on `/announcements/my-wallet`.

**Contract**: With `list_announcements_for_watchlist` patched to return a row containing
`structured_analysis` (with `sentiment`) and `analysis_score`:
- admin request → response items contain `analysis_score` and
  `structured_analysis.sentiment`;
- user request → response items contain neither `analysis_score` nor
  `structured_analysis.sentiment` (regression guard for the strip behavior).
Follow the existing patch-style of the `/announcements` role tests in this file.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- New role-contract tests for my-wallet green (admin gets sentiment+score, user gets neither)
- Query-string regression assert covers `analysis_score` in the watchlist SELECT

#### Manual Verification:

- Round-trip on real BQ (lessons.md rule for hand-written SQL): run the my-wallet endpoint
  locally against prod BQ (or `scripts/test_bq.py`-style check) and confirm
  `analysis_score` comes back non-null for at least one watchlist announcement

**Implementation Note**: After completing this phase and all automated verification passes,
pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Frontend gate + E2E

### Overview

Hide the bar (and skip its fetch) for non-admins; prove both role behaviors in the browser.

### Changes Required:

#### 1. Role gate in the bar function

**File**: `static/index.html`

**Intent**: `fetchWlSentimentSummary()` (`static/index.html:1844`) must early-return (and
keep the box hidden) when `role !== 'admin'` — no request is issued for regular users. The
existing call sites (initial view load, after add/remove ticker — `static/index.html:1565`,
`1658`, `1984`, `2005`) stay untouched; the gate lives inside the function so no call-site
audit is needed.

**Contract**: For `role !== 'admin'`: no `/announcements/my-wallet` fetch from this
function, `#wl-sentiment-summary` stays `display:none`. For admin: behavior as designed
(bar renders counts + avg score once real data flows after Phase 1).

#### 2. E2E mock enrichment

**File**: `tests/e2e/conftest.py`

**Intent**: `_FAKE_WATCHLIST_ANNOUNCEMENT` (`tests/e2e/conftest.py:150`) needs a real
`structured_analysis` (with `sentiment`) and an `analysis_score` so the admin bar has data
to render; `published_at` must be within 7 days of "now" for the bar's cutoff filter —
use a dynamic timestamp (e.g. `datetime.now(timezone.utc) - timedelta(days=1)`), not a
hardcoded date that will silently age out of the window.

**Contract**: Mock row gains `"structured_analysis": {"sentiment": "pozytywny", ...}` and
`"analysis_score": <number>`; existing tests that consume this mock must stay green (they
assert on company/ticker, not on analysis fields — verify, don't assume).

#### 3. E2E tests — both roles

**File**: `tests/e2e/test_watchlist_sentiment.py` (new) or the existing watchlist e2e file
if one covers the Obserwowane view — implementer picks based on current file layout.

**Intent**: Two tests: (a) admin with PKO in watchlist sees `#wl-sentiment-summary`
visible with "Pozytywny: 1" and avg score; (b) regular user with the same watchlist never
sees the element. Follow `/10x-e2e` hard rules: role/label/text locators, no
`waitForTimeout`, independent setup/cleanup per test.

**Contract**: Assertions on visible text of `#wl-sentiment-summary` (admin) and
`not_to_be_visible()` / zero-count (user). Login helpers already exist in the e2e suite
(`_ADMIN_KEY` / `_USER_KEY` pattern, e.g. `tests/e2e/test_x_post_history.py:5-13`).

### Success Criteria:

#### Automated Verification:

- Full e2e suite passes: `uv run pytest tests/e2e -q`
- New admin test asserts populated bar (counts + avg score visible)
- New user test asserts the bar is absent

#### Manual Verification:

- On prod after deploy: admin sees populated bar on Obserwowane; regular user does not see
  the bar and devtools network tab shows no sentiment-driven my-wallet fetch beyond the
  table's own

---

## Testing Strategy

### Unit Tests:

- my-wallet admin: `analysis_score` + `structured_analysis.sentiment` present
- my-wallet user: both absent (regression guard)
- Query-string assert: `analysis_score` in watchlist SELECT

### Integration Tests:

- E2E admin: bar visible + populated; E2E user: bar absent

### Manual Testing Steps:

1. Local run against real BQ: my-wallet as admin returns non-null `analysis_score`
2. Prod after deploy: admin sees populated bar; user account sees no bar

## References

- Change notes / root cause: `context/changes/sentiment-bar-admin-fix/change.md`
- Mirror pattern: `src/api.py:307-331`
- Lessons applied: BQ SQL round-trip + query-string regression (`context/foundation/lessons.md`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — admin branch on /announcements/my-wallet

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q` — 6de79ac
- [x] 1.2 New role-contract tests for my-wallet green (admin gets sentiment+score, user gets neither) — 6de79ac
- [x] 1.3 Query-string regression assert covers `analysis_score` in the watchlist SELECT — 6de79ac

#### Manual

- [x] 1.4 Round-trip on real BQ: my-wallet returns non-null `analysis_score` for admin — 6de79ac

### Phase 2: Frontend gate + E2E

#### Automated

- [x] 2.1 Full e2e suite passes: `uv run pytest tests/e2e -q` — 36cba23
- [x] 2.2 New admin test asserts populated bar (counts + avg score visible) — 36cba23
- [x] 2.3 New user test asserts the bar is absent — 36cba23

#### Manual

- [ ] 2.4 Prod verification: admin sees populated bar, user sees no bar and no extra fetch
