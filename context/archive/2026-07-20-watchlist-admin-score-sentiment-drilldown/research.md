---
date: 2026-07-20T00:00:00Z
researcher: Radek
git_commit: a9194fe1236b66b7e4509d3cde2915855ecb78a4
branch: master
repository: puls-gpw
topic: "PUL-87 — Watchlist admin view: score column + sentiment bar period info + sentiment drill-down popup"
tags: [research, codebase, my-wallet, watchlist, sentiment, admin-role, bigquery, modal]
status: complete
last_updated: 2026-07-20
last_updated_by: Radek
---

# Research: PUL-87 — Watchlist admin view (score column + sentiment period info + drill-down popup)

**Date**: 2026-07-20
**Researcher**: Radek
**Git Commit**: a9194fe1236b66b7e4509d3cde2915855ecb78a4
**Branch**: master
**Repository**: puls-gpw

## Research Question

PUL-87 (GitHub #155) adds full analytical context to the admin's "Obserwowane" (My Wallet) view. Three parts:
1. **Score column for admin** — make the my-wallet table render role-aware (it's hardcoded as the user variant).
2. **Sentiment bar period info** — replace the hardcoded "Ostatnie 7 dni" label with a real date range + count of days with data.
3. **Drill-down popup** — clicking a sentiment bucket (Pozytywny/Neutralny/Negatywny) opens a modal listing the matching watchlist announcements from that window.

Constraint: sentiment/score stay **admin-only** (PUL-82 convention — must never reach the `user` role).

## Summary

The three parts are three very different sizes of work:

- **Part 1 (score column)** is a ~2-line frontend fix. The backend already returns `analysis_score` for admins on `/announcements/my-wallet`, and `renderTable()` is already role-aware. The my-wallet view just hardcodes the `'user'` literal in two spots (table head build + render call). Change those to the live `role`.
- **Part 2 (period info)** touches the existing **client-side** sentiment aggregation (`fetchWlSentimentSummary`). There is no backend summary endpoint today — the bar re-fetches `/announcements/my-wallet?page_size=100` and buckets in JS with a hardcoded 7-day cutoff. The "explicit date range + days-with-data" metadata can be computed **client-side from the same rows** (cheapest, no backend change) or via a **new server endpoint** (fixes the known >100-row undercount, PUL-82 F2). This is the main scope decision for the plan.
- **Part 3 (drill-down)** is the real feature: a new **BQ function** (watchlist join + sentiment filter + window, bounded), a new **admin-gated + per-user endpoint** (first of its kind — combines `_require_admin` with `_get_user_id`), a **modal** reusing the shared `#modal-overlay` shell, and a **per-user cache key** following the existing `f"<resource>:{user_id}:…"` shape. The one net-new engineering pattern is **filtering sentiment in SQL** (`JSON_VALUE(structured_analysis,'$.sentiment')`), which the codebase has never done — sentiment lives inside a JSON `STRING` column, not a real column.

All routes live in `src/api.py`; all BQ in `db/bigquery.py`; all UI in `static/index.html`.

## Detailed Findings

### Part 1 — Score column for admin (frontend-only)

- Column definitions: `_ADMIN_COLS` (has **Score** at `col-score`, Analiza, Źródło) vs `_USER_COLS` (no Score) — `static/index.html:2020-2036`.
- `renderTable(data, r, containerId)` is **already role-aware** — branches on `r === 'admin'` for the score/sentiment/source cells — `static/index.html:3831`, cells at `3835, 3864-3870`. Row `data-*` attrs (`data-score`, `data-sc`, `data-url`) are only emitted for admin (`3866, 3868`), so the modal can't leak them either.
- The my-wallet view hardcodes the **user** variant in two places:
  - Table head built from `_USER_COLS` — `static/index.html:2280-2281` (inside `_buildMyWalletViewContent`).
  - Render call passes the literal `'user'` — `static/index.html:2592` (`renderTable(data, 'user', 'my-wallet-table-body')`), and skeleton uses `_USER_COLS.length` at `2579`, error colspan at `2597`.
- **Fix**: pass the live `role` instead of `'user'` at those spots, mirroring the announcements table which drives headers via `renderHeaders(r)` (`static/index.html:2076-2093`). Backend already returns `analysis_score` for admins on this endpoint (see below), so no API change is needed for Part 1.
- Sorting: `_ANN_SORT_KEYS` maps the "Score" header to `analysis_score` (`static/index.html:2051`); the my-wallet head is currently static (no sort wiring) — decide whether admin score column in my-wallet needs sort parity with the announcements table.

### Part 2 — Sentiment bar period info (currently client-side)

- `fetchWlSentimentSummary()` — `static/index.html:2604-2636`:
  - Admin-gated in JS: `if (role !== 'admin') { box.style.display='none'; return; }` (`2609`) — cosmetic; the backend strip is the real guard.
  - Re-fetches `GET /announcements/my-wallet?page=1&page_size=100` (`2611`) — a **second** call independent of the table fetch (PUL-82 F3, accepted).
  - Hardcoded 7-day cutoff computed in JS: `Date.now() - 7*86400000` (`2616-2617`), buckets sentiment + averages score in JS (`2619-2626`).
  - Renders the hardcoded label **`"Ostatnie 7 dni"`** at `static/index.html:2628` — the string PUL-87 item #2 replaces.
- **Known limitation to respect (PUL-82 impl-review F2)**: the 100-row cap silently undercounts a watchlist with >100 announcements in 7 days. If Part 2 stays client-side, the "days with data" / date-range metadata is derived from the same (capped) rows. Moving aggregation server-side would fix F2 and F3 (single query, exact counts) — this is the scope fork for planning.
- Cheapest client-side option: compute `min(published_at)`/`max(published_at)` and the count of **distinct calendar days** present among `recent` rows, and render that in place of the static string — no backend change.

### Part 3 — Drill-down popup (BQ + endpoint + modal + cache)

**BQ layer** — mirror `list_announcements_for_watchlist(user_id, page, page_size, from_dt, to_dt)` at `db/bigquery.py:1659-1719`:
- SQL (`1683-1695`): announcements `a` INNER JOIN `(SELECT ticker FROM watchlist WHERE user_id=@user_id LIMIT 200)` on `a.ticker=w.ticker`, `_build_filter_clauses(approved_only=True, from_dt, to_dt)`, `ORDER BY a.published_at DESC`, `LIMIT @page_size OFFSET @offset`. Selects `a.analysis_score` and `a.structured_analysis`.
- **Sentiment is NOT a column** — it lives inside `structured_analysis`, a JSON `STRING` column (`db/bigquery.py:61`; shape in `src/analyzer.py:48-58`, values coerced to `{pozytywny, negatywny, neutralny}`, default `neutralny`). Repo-wide grep for `JSON_VALUE`/`JSON_EXTRACT` returns **zero hits** — no query filters sentiment in SQL today.
  - New drill-down needs either `WHERE JSON_VALUE(a.structured_analysis,'$.sentiment') = @sentiment` (net-new pattern; watch NULL/absent → treat as neutralny) **or** fetch-then-filter in Python (matches the current client-side approach, but wastes rows and re-introduces the 100-cap problem).
- **Time window**: `_build_filter_clauses` (`db/bigquery.py:1408-1439`) binds `published_at >= @from_dt` / `<= @to_dt` as `TIMESTAMP` params; when `from_dt` omitted it defaults to `TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)`. For an explicit "last N days" window bind a param (or reuse `BETWEEN @start AND @end`, cf. `fetch_top_n_for_window` at `db/bigquery.py:1376`).
- **Bounding**: use `LIMIT @limit` (bound INT64, cf. `list_top_announcements_public` at `db/bigquery.py:1744-1747`) or the paginated `LIMIT @page_size OFFSET @offset` idiom.
- **Parameterization**: always `ScalarQueryParameter`/`ArrayQueryParameter`; f-strings only for structural table refs + whitelisted clause fragments (`db/bigquery.py:1696-1703`). Wrap in try/except → `BigQueryError` (`src/exceptions.py`).
- **Tables**: `watchlist` (`db/bigquery.py:462-469`: `ticker`, `added_at`, `user_id` NULLABLE = PUL-74 canonical identity; `client_id` legacy pending DROP). `announcements` (`db/bigquery.py:48-67`: DAY-partitioned on `published_at`, clustered on `ticker`; `structured_analysis STRING`, `analysis_approved BOOL`, `analysis_score FLOAT64`; no `user_id` — scoping is only via the watchlist join).

**Endpoint** — `src/api.py`:
- Handler to mirror: `announcements_my_wallet` — `src/api.py:458-489`. Uses `role: Role = Depends(_get_role)` (`103-120`) + `user_id: str = Depends(_get_user_id)` (`129-135`, JWT-only per PUL-74).
- Field stripping is **per-endpoint**: admin branch maps rows through `AnnouncementAdmin` (`src/api.py:147-166`, has `analysis_score` + full `structured_analysis`); user branch pops `sentiment` and maps through `AnnouncementUser` (`214-221`, `ConfigDict(extra="ignore")`, no `analysis_score` field) — `src/api.py:471-486`.
- **Admin-only guard**: `_require_admin` — `src/api.py:123-126` (raises 403 if `role != "admin"`). Templates: `GET /admin/portfolio/treemap` (`src/api.py:513-517`, admin-gated **and** cached — closest template), `GET /admin/x-posts` (`491-500`).
- The drill-down is the **first endpoint that is both admin-gated and per-user** — combine `Depends(_require_admin)` (403 for user) with `Depends(_get_user_id)` (JWT identity for the watchlist join). Because it's admin-gated, the response can safely include sentiment/score without model stripping — but keep the never-leak invariant in mind.

**Cache** — `src/api.py`:
- `_PERF_CACHE` (`src/api.py:76-96`): `dict[str, tuple[value, ts]]`, `_perf_get(key, ttl)` / `_perf_set(key, data)` (TTL passed per call), `_perf_invalidate_portfolio` prefix-scan (`91-96`).
- Per-user key convention: `f"positions:{user_id}:{portfolio_id}"` ttl30 (`573`), `f"treemap:{user_id}"` ttl60 (`719`), `f"calendar:{user_id}:{portfolio_id}:{year}:{month}"` ttl300 (`780`). Suggested drill-down key: `f"wl-sentiment:{user_id}:{sentiment}:{window_days}"`, short TTL. Role need not be in the key (admin-only endpoint). **Caveat**: watchlist add/remove (`src/api.py:440,453`) does not invalidate any my-wallet cache — short TTL is the only staleness bound.
- Note `/announcements/my-wallet` is **currently uncached**.

**Modal** — `static/index.html` (shared `#modal-overlay` shell):
- Markup: `#modal-overlay.modal-overlay` (role="dialog", aria-modal) → `.modal-box` → `.modal-header` (`#modal-title`, `#modal-close`) + `#modal-meta.modal-meta` + `#modal-body.modal-body` — `static/index.html:1155-1165`.
- CSS: `.modal-overlay` (`384-388`, mobile bottom-sheet `394-397`), `.modal-box` (`389-393`), `.modal-header` (`398-403`), `.modal-close` (`404-410`), `.modal-meta` (`411-414`), `.modal-body` (`415-419`), `.modal-section`/`h4` (`430-434`), `.sentiment-badge` + `.sentiment-{pozytywny|negatywny|neutralny}` (`435`), `.score.high/.mid/.low/.neutral` (`437-444`); dark-theme overrides at `862, 867, 879, 912`.
- Open/close: `openModal(d)` (`static/index.html:3917-3971`; announcement branch `3940-3970`, admin-only fields gated by `role==='admin'` at `3953-3961`), `closeModal()` (`3972-3975`), wired for close button/backdrop/Escape (`3976-3983`), also called from `doLogout` (`1279`).
- Row → modal wiring today: `tr.clickable` rows carry `data-*` payload, `tbody.querySelectorAll('tr.clickable').forEach(tr => tr.addEventListener('click', () => openModal(tr.dataset)))` (`3897-3900`).
- For the drill-down, either reuse `#modal-overlay` with a **list** body (multiple announcements) or clone the shell into a list variant; sentiment-bucket spans need click handlers (currently the bar items in `2629-2633` are static).

## Code References

- `static/index.html:2020-2036` — `_ADMIN_COLS` / `_USER_COLS` (admin has Score column).
- `static/index.html:2280-2281` — my-wallet table head hardcoded to `_USER_COLS` (Part 1).
- `static/index.html:2592` — `renderTable(data, 'user', …)` hardcoded literal (Part 1).
- `static/index.html:2604-2636` — `fetchWlSentimentSummary()` client-side aggregation + hardcoded "Ostatnie 7 dni" (Part 2).
- `static/index.html:3831, 3864-3870, 3897-3900` — role-aware `renderTable` + row-click wiring.
- `static/index.html:1155-1165, 3917-3983` — shared modal shell + `openModal`/`closeModal` (Part 3 UI).
- `src/api.py:458-489` — `announcements_my_wallet` handler (mirror for drill-down endpoint).
- `src/api.py:103-135` — `_get_role` / `_require_admin` / `_get_user_id` dependencies.
- `src/api.py:147-166, 214-221` — `AnnouncementAdmin` / `AnnouncementUser` models (field stripping).
- `src/api.py:513-517` — admin-gated + cached endpoint template.
- `src/api.py:76-96` — `_PERF_CACHE` + helpers (per-user key convention).
- `db/bigquery.py:1659-1719` — `list_announcements_for_watchlist` (mirror for drill-down BQ fn).
- `db/bigquery.py:1408-1439` — `_build_filter_clauses` (time-window binding).
- `db/bigquery.py:48-67, 462-469` — announcements + watchlist schemas.
- `src/analyzer.py:45-58` — sentiment values + `structured_analysis` shape.

## Architecture Insights

- **Role exposure is model-enforced, not branch-enforced.** The user contract holds because `AnnouncementUser` omits `analysis_score` and `extra="ignore"` drops unknowns, plus an explicit `pop("sentiment")`. Adding DB columns can't leak to users — but never add `sentiment`/`analysis_score` as fields on the user model. (PUL-82.)
- **Sentiment has never been a query dimension.** It's a JSON string field read only in Python. SQL-side sentiment filtering is the one genuinely new pattern PUL-87 introduces; validate `JSON_VALUE` behavior on absent keys against real prod data before committing.
- **Parameterized queries everywhere.** Match `ScalarQueryParameter` style; no f-string interpolation of user data.
- **Caches are per-instance in-memory dicts** (Cloud Run), short TTL, no cross-instance sharing, best-effort invalidation.
- **Admin+per-user is a new endpoint shape** — no existing endpoint combines `_require_admin` with `_get_user_id`; both are individually well-established.

## Historical Context (from prior changes)

- `context/archive/2026-07-17-sentiment-bar-admin-fix/` (PUL-82) — established the admin-only invariant and built the current sentiment bar. Key recorded decisions:
  - **F1 (SKIPPED)**: `doLogout` clears `_watchlistFetched` + the sentiment bar but NOT `#my-wallet-table-body`, `#wl-tickers-list`, or `_wlData` — a previous user's rows can flash on same-document relogin. Relevant if PUL-87 touches watchlist/logout state (`impl-review.md:49-62`).
  - **F2 (accepted)**: 7-day aggregate reads ≤100 rows → undercounts busy watchlists (`impl-review.md:64-74`). Part 2/3 can fix this by aggregating server-side.
  - **F3 (accepted)**: table + bar each fetch `/my-wallet` = 2 BQ queries per view open (`impl-review.md:76-85`).
  - **F4**: vestigial `X-API-Key`/`X-Client-Id` headers still sent (cookie-JWT since PUL-74); slated for the "DROP watchlist.client_id" chore (`impl-review.md:87-98`).
  - No cache was introduced for `/my-wallet`; `analysis_score` is not a reserved keyword.
- `context/archive/2026-07-17-pul-71-auth-foundation/` and PUL-74 (per-user isolation) — established JWT-only `_get_user_id` and `watchlist.user_id` as canonical identity.

## Related Research

- `context/archive/2026-07-17-sentiment-bar-admin-fix/plan.md` — the sentiment bar's original plan (admin/user split, no cache).
- `context/foundation/lessons.md` — GCP client init (`load_dotenv` + quota project) and Gemini JSON trailing-comma parsing (`json5`) rules; relevant if the drill-down endpoint touches BQ client init or parses `structured_analysis`.

## Open Questions

1. **Part 2 scope**: keep the period-info client-side (cheapest, inherits the 100-row cap) or move aggregation to a new server endpoint (fixes F2/F3, more work)? This is the main planning fork.
2. **SQL sentiment filter**: is `JSON_VALUE(structured_analysis,'$.sentiment')` reliable across all stored rows (any rows with malformed/absent JSON, non-json5-clean strings)? The blob is written such that Python needs `json5` to parse (trailing commas) — does BQ's `JSON_VALUE` tolerate the same strings, or do some rows need cleanup? **Verify against prod before planning the SQL path.**
3. **Window definition**: fixed 7 days (matching the current bar) or a parameter? The bar and the drill-down must use the **same** window so counts match the popup contents (success criterion: "exactly the announcements counted in that bucket").
4. **Drill-down list size**: what LIMIT is safe/complete for a 7-day watchlist bucket? Must not silently truncate below the bar's displayed count.
5. **Score column sort**: should the admin my-wallet Score column be sortable like the announcements table, or static?
6. **doLogout cleanup (F1)**: fix the incomplete reset now that PUL-87 adds more admin-only my-wallet state, or leave it?
