# Watchlist Admin View — Score Column, Sentiment Period Info & Drill-Down Popup — Implementation Plan

## Overview

Give the admin full analytical context in the "Obserwowane" (My Wallet) view: a role-aware **Score** column, a **real 7-day period label** on the sentiment bar (explicit date range + count of days with data), and **click-through** from each sentiment bucket (Pozytywny / Neutralny / Negatywny) to a popup listing the matching watchlist announcements. Bar counts and popup contents are computed by **one server-side bucketing path** so they can never drift. Sentiment and score stay admin-only (PUL-82 invariant).

## Current State Analysis

- **Score column**: the my-wallet table is hardcoded to the user variant — head built from `_USER_COLS` (`static/index.html:2280-2281`) and `renderTable(data, 'user', 'my-wallet-table-body')` (`static/index.html:2592`), with skeleton (`2579`) and error colspan (`2597`) also on `_USER_COLS`. But `renderTable(data, r, containerId)` is **already role-aware** (`static/index.html:3831, 3864-3870`) and the backend `/announcements/my-wallet` already returns `analysis_score` for admins (`src/api.py:458-489`, BQ `db/bigquery.py:1659-1719` selects `a.analysis_score`). So the Score column is purely a frontend wiring fix.
- **Sentiment bar**: `fetchWlSentimentSummary()` (`static/index.html:2604-2636`) aggregates **client-side** — it re-fetches `/announcements/my-wallet?page=1&page_size=100` (a second BQ query, PUL-82 F3), filters a hardcoded 7-day cutoff in JS, buckets by exact Polish sentiment string, and renders the hardcoded label `"Ostatnie 7 dni"` (`2628`). It's admin-gated in JS (`2609`), cosmetic only.
- **Sentiment storage & drift**: sentiment is **not a column** — it lives inside `structured_analysis` (a JSON `STRING`; `db/bigquery.py:61`), shape from `src/analyzer.py:45-58` (`{pozytywny, negatywny, neutralny}`, default `neutralny`). No query in the repo filters sentiment in SQL (zero `JSON_VALUE` hits). **Verified against prod (last 90d, approved rows)**: `neutralny` 2177, `<NULL>` 842, `pozytywny` 216, `negatywny` 101, plus **English drift** `neutral` 96 / `positive` 27 / `negative` 4. `JSON_VALUE` on the STRING column is **lax** — malformed JSON yields NULL, not a query error. The current JS bar silently drops the NULL + English rows.
- **No summary/drill-down endpoint exists.** Only `/announcements`, `/announcements/my-wallet`, `/api/public/top-announcements` serve announcements.
- **doLogout (F1, PUL-82)**: clears the sentiment bar + `_watchlistFetched` but NOT `#my-wallet-table-body`, `#wl-tickers-list`, or `_wlData` — a prior admin's rows can flash on same-document relogin.

## Desired End State

- Admin sees the **Score** column in Obserwowane; user does not (verified: user table unchanged).
- The sentiment bar shows the **actual 7-day date range and the count of days with data**, computed server-side.
- Clicking each bucket opens a modal listing **exactly** the announcements counted in that bucket (same window, same normalized bucketing), bounded at 200.
- User role never receives the summary or drill-down data (both endpoints return 403 for non-admin).
- Relogin as a different user shows no flash of the previous admin's wallet rows/sentiment/score.

### Key Discoveries

- `renderTable` is already role-aware — Part 1 is passing `role` instead of `'user'` (`static/index.html:2592, 2280-2281`).
- Mirror BQ template: `list_announcements_for_watchlist` (`db/bigquery.py:1659-1719`) — same watchlist INNER-JOIN + `_build_filter_clauses(approved_only=True, ...)` (`db/bigquery.py:1408-1439`).
- Admin+per-user endpoint is a new shape: combine `Depends(_require_admin)` (`src/api.py:123-126`) with `Depends(_get_user_id)` (`src/api.py:129-135`). Cached admin GET template: `/admin/portfolio/treemap` (`src/api.py:513-517`).
- Per-user cache convention: `_PERF_CACHE` + `_perf_get(key, ttl)`/`_perf_set` (`src/api.py:76-96`), keys like `f"treemap:{user_id}"`.
- Bound with `_FETCH_SAFETY_CAP = 200` (`db/bigquery.py:36`).
- Reusable modal shell `#modal-overlay` + `openModal`/`closeModal` (`static/index.html:1155-1165, 3917-3983`).
- Parameterized queries only (`ScalarQueryParameter`); f-strings only for table refs / whitelisted clause fragments.

## What We're NOT Doing

- No configurable window (fixed 7 days). No window picker UI.
- No pagination inside the drill-down popup (single bounded list, cap 200).
- No sort wiring for the my-wallet Score column (static column).
- No data backfill / re-analysis of the English/NULL sentiment rows (normalized at read time only; data-quality cleanup is a separate concern).
- No change to the my-wallet table's own scope (still 90-day, paginated) — only the 7-day bar aggregates.
- No removal of the vestigial `X-API-Key`/`X-Client-Id` headers (PUL-82 F4 — belongs to the DROP client_id chore).
- No *broad* cache invalidation. **Deviation (Phase 2):** the sentiment-summary/drill-down per-user caches ARE invalidated on watchlist add/remove (`_invalidate_wl_sentiment`) — the bar is refetched right after a mutation and must reflect the new watchlist, so a 60s stale empty summary is unacceptable (the original client-side bar had no cache). Other endpoints keep their short-TTL-only staleness bound.

## Implementation Approach

Server owns bucketing. Two new BQ functions share a **single normalization SQL fragment** (module-level constant) so the summary counts and the drill-down list apply byte-identical bucketing:

- Normalization: `LOWER(IFNULL(JSON_VALUE(structured_analysis,'$.sentiment'),''))` mapped `positive→pozytywny`, `negative→negatywny`, and **everything else (incl. `neutral`, `''`, NULL, unknown) → `neutralny`** (the analyzer's own default). No approved announcement in the watchlist window can fall outside the three buckets.
- Both functions filter the same window (`published_at >= now-7d`, `approved_only=True`) over the user's watchlist via the existing INNER-JOIN subquery.

Frontend consumes the summary endpoint for the bar (counts + period label), wires bucket spans as clickable, and renders the drill-down list in the shared modal. doLogout is hardened to clear all wallet state.

Phases are ordered by independence: Phase 1 ships alone; Phase 2 replaces the client-side aggregation; Phase 3 builds on Phase 2's normalization constant; Phase 4 hardens logout once all state exists.

## Critical Implementation Details

- **Shared normalization is load-bearing.** The success criterion "popup shows exactly the announcements the bucket counted" holds only if the summary's `GROUP BY <normalize>` and the list's `WHERE <normalize> = @bucket` use the *same* SQL expression. Define it once (e.g. `_SENTIMENT_BUCKET_SQL`) and interpolate it into both queries; never inline two copies.
- **Admin-gate at the dependency, not via model stripping.** These endpoints return sentiment/score by definition, so they must be unreachable by the user role — guard with `_require_admin`. Do not add `sentiment`/`analysis_score` as fields on any user-facing model.
- **`JSON_VALUE` on the STRING column is lax** (verified) — malformed JSON → NULL, folded to `neutralny`. No `SAFE.` prefix (unsupported for `JSON_VALUE`), and no try/parse needed in SQL.
- **Do not parameterize the INTERVAL.** The 7-day window is f-string-interpolated from the module constant `_WL_SENTIMENT_WINDOW_DAYS` (matching `db/bigquery.py:1433, 1742`); BigQuery rejects a query parameter in the `INTERVAL` slot of `TIMESTAMP_SUB`. Only `user_id`, `bucket`, and `limit` are bound params.
- **days-with-data** = `COUNT(DISTINCT DATE(published_at))` over the matched rows (server UTC); the date range is the window `[now-7d, now]`. Return both so the label can read e.g. "Ostatnie 7 dni (13–20 lip) · 5 dni z danymi".

---

## Phase 1: Score Column for Admin (frontend-only)

### Overview

Make the my-wallet table render role-aware so admins get the Score column (and Analiza/Źródło parity via `_ADMIN_COLS`), while users see the unchanged 5-column table.

### Changes Required

#### 1. My-wallet table render wiring

**File**: `static/index.html`

**Intent**: Replace the hardcoded `'user'`/`_USER_COLS` references in the my-wallet view with the live `role`, mirroring how the announcements table selects columns. Backend already returns `analysis_score` for admins on this endpoint, so no API change.

**Contract**: In `_buildMyWalletViewContent` the head is built from `role === 'admin' ? _ADMIN_COLS : _USER_COLS` (currently `_USER_COLS`, `~2280-2281`); `fetchMyWalletAnnouncements` calls `renderTable(data, role, 'my-wallet-table-body')` (currently literal `'user'`, `~2592`); the skeleton (`~2579`) and the error-row `colspan` (`~2597`) use `(role === 'admin' ? _ADMIN_COLS : _USER_COLS).length`. Score column stays static (no sort wiring). `renderTable` (`3831`) and its admin-gated `data-*` emission (`3866, 3868`) are unchanged.

### Success Criteria

#### Automated Verification:

- Lint/format passes for the static assets (whatever the repo runs, e.g. `make lint` / prettier check).
- Existing test suite passes: `pytest` (no backend change, must stay green).

#### Manual Verification:

- As **admin**, open Obserwowane → the table shows the Score column populated; clicking a row still opens the modal with score/sentiment.
- As **user**, the table shows the original 5 columns with no Score and no score in the modal.
- Skeleton and error states render with the correct column count for each role.

**Implementation Note**: After automated verification passes, pause for human confirmation of the manual checks before Phase 2.

---

## Phase 2: Sentiment Summary Endpoint + Period Info

### Overview

Move the sentiment aggregation server-side: one admin+per-user endpoint returns normalized bucket counts, average score, the 7-day window bounds, and days-with-data. The bar consumes it and renders a real period label, eliminating the second `/my-wallet` fetch and the >100-row undercount.

### Changes Required

#### 1. BQ aggregation function + shared normalization constant

**File**: `db/bigquery.py`

**Intent**: Add `summarize_watchlist_sentiment(user_id, days=7)` returning per-bucket counts, avg `analysis_score`, and days-with-data over the user's watchlist in the window. Introduce the module-level `_SENTIMENT_BUCKET_SQL` normalization fragment that Phase 3 will reuse.

**Contract**: New constant `_SENTIMENT_BUCKET_SQL` mapping `positive→pozytywny`, `negative→negatywny`, else `neutralny` over `LOWER(IFNULL(JSON_VALUE(a.structured_analysis,'$.sentiment'),''))`. New module constant `_WL_SENTIMENT_WINDOW_DAYS = 7`. Function mirrors `list_announcements_for_watchlist`'s watchlist INNER-JOIN (`db/bigquery.py:1659-1719`) + `_build_filter_clauses(approved_only=True)`, window `published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {_WL_SENTIMENT_WINDOW_DAYS} DAY)` — **f-string-interpolated, matching the existing pattern at `db/bigquery.py:1433, 1742`; do NOT bind the interval as a query parameter (BQ rejects a param in the INTERVAL slot; `grep "INTERVAL @"` = 0 hits in the repo). The day count is a fixed internal constant, not user input.** `GROUP BY` the bucket expression; also select `AVG(a.analysis_score)` and `COUNT(DISTINCT DATE(a.published_at))`. Only `user_id` is a `ScalarQueryParameter`. Returns a dict: `{counts: {pozytywny, neutralny, negatywny}, avg_score, days_with_data, window_from, window_to, total}`. Wrap in try/except → `BigQueryError`.

#### 2. Summary endpoint

**File**: `src/api.py`

**Intent**: Expose the aggregation as an admin+per-user GET, cached briefly.

**Contract**: `GET /announcements/my-wallet/sentiment-summary`, deps `role = Depends(_require_admin)` + `user_id = Depends(_get_user_id)`. Cache key `f"wl-sentiment-sum:{user_id}"` via `_perf_get(..., ttl=60)`/`_perf_set`. Response model (new, admin-only) carries the dict from the BQ function. 403 for user role (via `_require_admin`), 401 without a JWT session (via `_get_user_id`).

#### 3. Bar consumes the endpoint

**File**: `static/index.html`

**Intent**: Rewrite `fetchWlSentimentSummary()` to call the summary endpoint instead of re-fetching `/my-wallet?page_size=100` and bucketing in JS; render the real period label.

**Contract**: `fetchWlSentimentSummary()` (`~2604-2636`) keeps its `role !== 'admin'` early return, fetches `/announcements/my-wallet/sentiment-summary`, and renders counts + avg score + a period label built from `window_from`/`window_to`/`days_with_data` (replacing the hardcoded `"Ostatnie 7 dni"` at `~2628`). Bucket count spans get stable hooks (ids/data-attrs) for Phase 3 to attach click handlers. **Preserve the substrings the E2E suite asserts** (`tests/e2e/test_watchlist_sentiment.py:46-48`): the label must still start with `"Ostatnie 7 dni"` (the richer date-range/days text follows it), bucket counts render as `"Pozytywny: N"` etc., and the average as `"Śr. score: N"` with the score **rounded to an integer** (matching the current `Math.round`). The `avg_score` from the endpoint is a float — round it in the render.

#### 4. E2E conftest: patch the new BQ function

**File**: `tests/e2e/conftest.py`

**Intent**: The E2E harness patches every `src.api.*` BQ call for `live_server_url` (e.g. `list_announcements_for_watchlist` at line 462); the new summary endpoint needs its BQ function patched too, or `test_watchlist_sentiment.py` will hit real BQ and fail.

**Contract**: Add `patch("src.api.summarize_watchlist_sentiment", ...)` to the `live_server_url` patch stack, returning a fixed summary dict consistent with the seeded PKO row so the bar renders `"Pozytywny: 1"` and `"Śr. score: 85"` — i.e. `{counts: {pozytywny: 1, neutralny: 0, negatywny: 0}, avg_score: 85, days_with_data: <n>, window_from: ..., window_to: ..., total: 1}`.

### Success Criteria

#### Automated Verification:

- `pytest` passes, including new tests: summary endpoint returns 403 for user role and 200 with the expected shape for admin; BQ function folds English/NULL sentiment into the correct buckets (mock BQ per the conftest pattern).
- Existing E2E suite `tests/e2e/test_watchlist_sentiment.py` stays green (conftest patches the new `summarize_watchlist_sentiment`; render preserves the asserted substrings).
- Lint passes.

#### Manual Verification:

- As admin, the bar shows counts matching a hand-check of the watchlist's last 7 days, an average score, and a label like "Ostatnie 7 dni (13–20 lip) · N dni z danymi".
- Only **one** `/my-wallet`-family request fires the sentiment data (no duplicate 100-row fetch).
- As user, no sentiment bar and no summary request (403 if forced).
- A watchlist with a NULL/English-labelled announcement now counts it (folded to the right bucket) rather than dropping it.

**Implementation Note**: Pause for human confirmation before Phase 3.

---

## Phase 3: Sentiment Drill-Down Popup

### Overview

Clicking a bucket opens a modal listing the matching watchlist announcements from the same 7-day window, using the shared normalization so contents equal the bar count. Bounded at 200.

### Changes Required

#### 1. BQ list function (shares normalization)

**File**: `db/bigquery.py`

**Intent**: Add `list_watchlist_by_sentiment(user_id, bucket, days=7, limit=_FETCH_SAFETY_CAP)` returning the announcement rows whose normalized sentiment equals `bucket`, newest first, bounded.

**Contract**: Same watchlist INNER-JOIN + window (`INTERVAL {_WL_SENTIMENT_WINDOW_DAYS} DAY`, f-string-interpolated as in Phase 2 — not a bound param) + `approved_only=True` as Phase 2's function, plus `WHERE _SENTIMENT_BUCKET_SQL = @bucket` (the **same** constant). Select the fields the modal needs (company, ticker, event_type, published_at, structured_analysis, analysis_score). `ORDER BY a.published_at DESC LIMIT @limit`. `bucket`/`user_id`/`limit` parameterized (the day count is the interpolated constant). Returns list of row dicts.

#### 2. Drill-down endpoint

**File**: `src/api.py`

**Intent**: Admin+per-user list endpoint per bucket.

**Contract**: `GET /announcements/my-wallet/sentiment/{bucket}` with `bucket` validated to `pozytywny|neutralny|negatywny` (422/400 otherwise), deps `_require_admin` + `_get_user_id`. Parse `structured_analysis` via `_parse_structured_analysis` (`src/api.py:138-144`) and return admin rows (score + sentiment allowed — admin-only endpoint). Cache key `f"wl-sentiment-list:{user_id}:{bucket}"` ttl 60. Include a `truncated` flag when the row count hits the cap.

#### 3. Clickable buckets + list modal

**File**: `static/index.html`

**Intent**: Wire each bucket span to fetch the list endpoint and render the results in the shared `#modal-overlay` as a list; reuse existing modal open/close.

**Contract**: Click handlers on the Phase 2 bucket hooks call the drill-down endpoint and populate a list body in `#modal-overlay` (`1155-1165`) via a new render helper (mirroring `openModal`'s announcement branch styling: `.modal-section`, `.sentiment-badge`, `.score`). Rows are read-only (optionally each row links to the full announcement modal). Show a subtle "pokazano pierwsze 200" note when `truncated`. Reuse `closeModal()` (`3972-3983`) for close/backdrop/Escape. Empty bucket → friendly empty state.

### Success Criteria

#### Automated Verification:

- `pytest` passes, including: drill-down returns 403 for user, 422 for an invalid bucket, and a bounded list for admin.
- Consistency is guaranteed **by construction** (both BQ functions interpolate the same `_SENTIMENT_BUCKET_SQL` constant), not by a behavioral test — the repo mocks all `db.bigquery.*` at the API layer, so comparing two mocked returns proves nothing. Lock it with a **structural** assertion instead: both query-builder outputs embed the identical `_SENTIMENT_BUCKET_SQL` fragment.
- Lint passes.

#### Manual Verification:

- Clicking each bucket opens the modal listing exactly the announcements counted in the bar for that bucket (counts match).
- Modal closes via ✕, backdrop, and Escape; opening/closing doesn't break the underlying table.
- User role gets 403 if the endpoint is called directly; no bucket is clickable for users (bar hidden).
- A bucket with >200 matches shows the truncation note (or confirm none realistically hits it).

**Implementation Note**: Pause for human confirmation before Phase 4.

---

## Phase 4: doLogout Cleanup (F1)

### Overview

Harden `doLogout` so a relogin as a different user can't flash the previous admin's wallet rows, sentiment, or score — important now that more admin-only state lives in this view.

### Changes Required

#### 1. Clear wallet state on logout

**File**: `static/index.html`

**Intent**: Extend `doLogout` to reset the remaining my-wallet state. It **already** calls `closeModal()` (`static/index.html:1279`) and already clears `_watchlistFetched` (`1289`) + the sentiment bar (`1290`) — so those are done. The gap is the table body, ticker list, row data, and the **once-built role-dependent head** (impl-review F1).

**Contract**: `doLogout` additionally empties `#my-wallet-table-body` and `#wl-tickers-list`, resets `_wlData = []` (and `_watchlistTickers`/`wlPage` as appropriate), and **resets `_myWalletViewBuilt = false`** so the next login rebuilds the my-wallet head for its own role. Without this, an admin→user same-document relogin leaves the stale admin head (Score column, `_ADMIN_COLS`) over a user body — the head is built once (`static/index.html:2303-2305`) and never re-rendered, unlike the announcements head which `renderHeaders(role)` rebuilds per fetch. No new `closeModal()` call is needed (already present); no change to auth/session logic.

### Success Criteria

#### Automated Verification:

- `pytest` / existing E2E (if any cover logout) stay green.
- Lint passes.

#### Manual Verification:

- Log in as admin A, open Obserwowane (rows + bar visible), log out, log in as user B (or admin B) → no flash of A's rows, tickers, sentiment, or score; any open popup is gone.
- Same-document admin→user relogin (no page reload): user's Obserwowane shows the 5-column head with **no Score column** (the head rebuilds for the user role; impl-review F1).

**Implementation Note**: Final phase — confirm the full flow end-to-end.

---

## Testing Strategy

### Unit Tests

- BQ normalization: rows with `positive`/`negative`/`neutral`/`''`/NULL/unknown fold to the correct Polish bucket; approved-only filter respected; window bound correct.
- Endpoints: `_require_admin` → 403 for user on both new endpoints; `_get_user_id` → 401 without JWT; invalid bucket → 422.
- Summary shape: counts + avg_score + days_with_data + window bounds present.

### Integration Tests

- Consistency lock (structural, not behavioral): assert both `summarize_watchlist_sentiment` and `list_watchlist_by_sentiment` embed the identical `_SENTIMENT_BUCKET_SQL` fragment. A behavioral count-vs-rowcount comparison is not meaningful here — BQ is mocked at the API layer, so it would only compare fixture returns.
- Follow the conftest BQ-mocking pattern — new BQ-backed endpoints need all `db.bigquery.*` functions they call mocked in `live_server_url` fixtures, not just startup hooks. Concretely: `tests/e2e/conftest.py` must patch `src.api.summarize_watchlist_sentiment` (Phase 2) and `src.api.list_watchlist_by_sentiment` (Phase 3), or `tests/e2e/test_watchlist_sentiment.py` breaks.

### Manual Testing Steps

1. Admin: Score column present + populated; user: absent.
2. Admin: bar label shows real date range + days-with-data; single fetch.
3. Admin: each bucket click → popup list matches the bar count; close via ✕/backdrop/Escape.
4. User: no bar, endpoints 403.
5. Relogin as different user: no state flash.

## Performance Considerations

- Two new per-user BQ queries, both bounded (aggregate is a single GROUP BY over a 7-day watchlist slice; list capped at 200). Short-TTL per-user cache (60s) absorbs repeat opens. Net effect vs today: Phase 2 removes one 100-row `/my-wallet` fetch (F3), so admin view-open BQ load is roughly neutral-to-lower.

## Migration Notes

- No schema or data migration. English/NULL sentiment values are normalized at read time only. `structured_analysis` and `watchlist.client_id` are untouched (client_id DROP remains a separate human-only chore).

## References

- Research: `context/changes/watchlist-admin-score-sentiment-drilldown/research.md`
- Prior art: `context/archive/2026-07-17-sentiment-bar-admin-fix/` (PUL-82 — admin-only invariant, F1/F2/F3)
- BQ template: `db/bigquery.py:1659-1719` (`list_announcements_for_watchlist`)
- Endpoint templates: `src/api.py:458-489` (my-wallet), `513-517` (cached admin GET), `123-135` (guards)
- Modal: `static/index.html:1155-1165, 3917-3983`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Score Column for Admin

#### Automated

- [x] 1.1 Lint/format passes for static assets — e6f86f6
- [x] 1.2 Existing test suite passes (pytest, no backend change) — e6f86f6

#### Manual

- [ ] 1.3 Admin sees populated Score column; row modal shows score/sentiment
- [ ] 1.4 User sees original 5-column table, no score
- [ ] 1.5 Skeleton + error states use correct column count per role

### Phase 2: Sentiment Summary Endpoint + Period Info

#### Automated

- [x] 2.1 pytest passes incl. summary 403-for-user / 200-for-admin shape + normalization folding tests — dd4f2bd
- [x] 2.2 E2E test_watchlist_sentiment.py stays green (conftest patches summarize_watchlist_sentiment; render preserves substrings) — dd4f2bd
- [x] 2.3 Lint passes — dd4f2bd

#### Manual

- [ ] 2.4 Bar counts match a hand-check; avg score + real period label render
- [ ] 2.5 Only one sentiment data fetch fires (no duplicate 100-row fetch)
- [ ] 2.6 User: no bar, no summary request (403 if forced)
- [ ] 2.7 NULL/English-labelled watchlist announcement now counted, not dropped

### Phase 3: Sentiment Drill-Down Popup

#### Automated

- [x] 3.1 pytest passes incl. drill-down 403-for-user, 422-invalid-bucket, bounded list — 1cb163c
- [x] 3.2 Structural consistency assertion: both BQ fns embed the identical _SENTIMENT_BUCKET_SQL fragment — 1cb163c
- [x] 3.3 Lint passes — 1cb163c

#### Manual

- [x] 3.4 Each bucket click opens modal listing exactly the counted announcements
- [ ] 3.5 Modal closes via ✕/backdrop/Escape without breaking the table
- [ ] 3.6 User gets 403 on direct call; no clickable bucket for users
- [ ] 3.7 >200-match bucket shows truncation note (or confirmed unreachable)

### Phase 4: doLogout Cleanup (F1)

#### Automated

- [x] 4.1 pytest / existing logout coverage stays green
- [x] 4.2 Lint passes

#### Manual

- [x] 4.3 Relogin as different user shows no flash of prior wallet rows/tickers/sentiment/score; open popup gone
- [x] 4.4 Same-document admin→user relogin: user head has no Score column (head rebuilt for role; impl-review F1)
