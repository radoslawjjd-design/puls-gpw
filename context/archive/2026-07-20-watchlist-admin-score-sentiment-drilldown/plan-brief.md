# Watchlist Admin View — Score, Sentiment Period & Drill-Down — Plan Brief

> Full plan: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
> Research: `context/changes/watchlist-admin-score-sentiment-drilldown/research.md`

## What & Why

Give the admin full analytical context in "Obserwowane" (My Wallet): a role-aware **Score** column, a **real 7-day period label** on the sentiment bar, and **click-through** from each sentiment bucket to the announcements behind it. Today the score column is missing, the period label is a hardcoded "Ostatnie 7 dni", and the buckets aren't clickable. Sentiment/score stay admin-only (PUL-82).

## Starting Point

The my-wallet table is hardcoded to the user variant even though the backend already returns `analysis_score` for admins, and `renderTable` is already role-aware. The sentiment bar aggregates **client-side** (a second `/my-wallet?page_size=100` fetch, JS bucketing, hardcoded label) and silently drops rows whose sentiment is NULL or English-labelled. No summary or drill-down endpoint exists; sentiment lives inside the `structured_analysis` JSON string, never queried in SQL before.

## Desired End State

Admin sees a populated Score column; the bar shows the actual date range + days-with-data; clicking a bucket opens a modal listing exactly the announcements that bucket counted. User role never receives sentiment/score (both new endpoints 403). Relogin as another user shows no flash of the prior admin's wallet data.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Bar↔popup consistency | One server endpoint for both | Counts and list share one BQ bucketing path so they can't drift; also fixes PUL-82 F2/F3 | Plan |
| Sentiment drift (NULL/EN) | Normalize EN→PL, NULL→neutralny | Verified prod drift (842 NULL, 127 English); fold at read time so nothing vanishes | Plan (prod check) |
| Time window | Fixed 7 days | Matches current intent; simplest; meets period-info requirement | Plan |
| Drill-down bound | Cap at 200 (`_FETCH_SAFETY_CAP`), note if exceeded | A 7-day single-bucket watchlist slice is realistically tiny | Plan |
| Score column sort | Static (no sort) | My-wallet head has no sort infra; keeps Part 1 to a 2-line fix | Plan |
| doLogout F1 | Fix in this change | We're expanding admin-only wallet state; prevent cross-user leak flash | Plan |
| SQL sentiment filter | `JSON_VALUE` (lax, no SAFE) | Verified: malformed JSON → NULL, no query error across 3.7k rows | Research (prod check) |

## Scope

**In scope:** role-aware Score column; server-side sentiment summary (counts + avg score + window bounds + days-with-data); drill-down endpoint + modal; shared SQL normalization; doLogout cleanup.

**Out of scope:** configurable window / picker; popup pagination; Score column sorting; sentiment data backfill; my-wallet table scope change; removing vestigial API-key headers; cache invalidation on watchlist mutation.

## Architecture / Approach

Server owns bucketing. Two new BQ functions (`summarize_watchlist_sentiment`, `list_watchlist_by_sentiment`) share a single module-level normalization SQL constant (`_SENTIMENT_BUCKET_SQL`) so summary counts and drill-down lists are byte-identical in logic. Both mirror the existing `list_announcements_for_watchlist` watchlist INNER-JOIN + `_build_filter_clauses(approved_only=True)`, over a fixed 7-day window. Two new admin+per-user endpoints (`_require_admin` + `_get_user_id`, per-user `_PERF_CACHE` keys, 60s TTL). Frontend: role-aware render, bar consumes the summary endpoint, bucket spans become clickable into the shared `#modal-overlay`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Score column | Role-aware my-wallet table (admin gets Score) | Trivial; ensure user table unchanged |
| 2. Summary endpoint + period info | Server-side counts + real date-range/days label; kills double fetch | Normalization correctness vs prod drift |
| 3. Drill-down popup | BQ list fn + endpoint + list modal, shares normalization | Count↔list consistency; modal reuse |
| 4. doLogout cleanup | No cross-user state flash on relogin | Small; verify nothing else depends on stale state |

**Prerequisites:** none beyond current codebase + prod BQ access (already verified).
**Estimated effort:** ~1–2 sessions across 4 phases (Phase 1 minutes; Phase 3 is the bulk).

## Open Risks & Assumptions

- Normalization folds all non-`positive`/`negative` values (incl. unknown) to `neutralny` — acceptable given the analyzer's own default, but it changes displayed counts vs today's exact-match bar.
- Per-user cache isn't invalidated on watchlist add/remove; 60s TTL is the staleness bound (matches existing endpoints).
- `days_with_data` and the date range are computed in server UTC; label copy is the implementer's call.

## Success Criteria (Summary)

- Admin sees the Score column; user does not.
- Bar shows the real 7-day date range + count of days with data.
- Each bucket click lists exactly the announcements counted, bounded; user role is 403 on both endpoints.
