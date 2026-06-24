---
date: 2026-06-23T00:00:00+02:00
researcher: Radek (via Claude Code)
git_commit: 96c9b01495d68e1e21130d13b4eac0d6cd41fb81
branch: radoslawjjd/pul-53-companies-dictionary-table-ticker-name-hop-url
repository: radoslawjjd-design/puls-gpw
topic: "Companies dictionary table (ticker, name, hop_url, isin) — implementation research for /10x-plan"
tags: [research, codebase, bigquery, parser, scraper, companies-dictionary-table, PUL-53]
status: complete
last_updated: 2026-06-23
last_updated_by: Radek (via Claude Code)
---

# Research: Companies dictionary table (ticker, name, hop_url, isin)

**Date**: 2026-06-23
**Researcher**: Radek (via Claude Code)
**Git Commit**: 96c9b01495d68e1e21130d13b4eac0d6cd41fb81
**Branch**: radoslawjjd/pul-53-companies-dictionary-table-ticker-name-hop-url
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

`frame.md` already resolved PUL-53's two open design questions (seed source, write
ownership) into a confirmed two-phase plan: **Phase A** (schema + create/ensure +
`upsert_company()` + capturing `profile_url`/`isin` at the existing parser hop) and
**Phase B** (a separate one-off full-GPW-company-list seed script). This research
answers the implementation-level question `/10x-plan` needs next: *exactly which
files, conventions, and test fixtures does each phase touch, and what is the blast
radius of the Phase A signature change?*

## Summary

- The codebase has a single, consistent BigQuery dimension-table convention
  (schema list → `create_*_if_not_exists` → `ensure_*_schema_current`) used by
  `_WATCHLIST_SCHEMA`, `_X_POSTS_SCHEMA`, `_PORTFOLIO_SNAPSHOTS_SCHEMA`. `companies`
  should follow it exactly. No MERGE/upsert helper exists yet anywhere — `upsert_company()`
  is new, but the surrounding create/ensure scaffolding is pure copy-paste.
- Phase A's signature change is narrow but has four concrete edit points:
  `_extract_ticker_company` (parser.py:175-201), `ParsedContent` (parser.py:30-35),
  `main.py:67`, and `update_parsed_content` (db/bigquery.py:514-519) — plus their
  tests. Only one production call chain exists for each, so the ripple is fully
  enumerable, not open-ended.
- Test mocking has a known trap (confirmed by prior project history): BOTH
  `tests/test_bigquery.py` (unit, mocks `db.bigquery._get_client`) AND
  `tests/e2e/conftest.py` (E2E, mocks `src.api.<function>` names) need the new
  table's functions added independently. **Portfolio snapshots' create/ensure
  functions are NOT in either startup-hook list nor fully E2E-mocked** — an
  existing gap in the codebase, not a pattern to copy.
- Phase B has no existing one-off-script precedent for *scraping a new external
  list source* — `scripts/*.py` precedent exists for one-off BQ round-trips
  (`scripts/test_bq.py`) but not for net-new scraping. The full-GPW-company-list
  source itself is **not present anywhere in the repo** (confirmed by both the
  frame.md investigation and this research's grep) — it must be chosen fresh
  during `/10x-plan`.

## Detailed Findings

### BigQuery dimension-table convention (Phase A schema/CRUD)

- Schema-list constant + `create_*_if_not_exists()` + `ensure_*_schema_current()`
  triplet, repeated identically for three tables: `_WATCHLIST_SCHEMA`
  (`db/bigquery.py:353-357`), `_X_POSTS_SCHEMA` (`db/bigquery.py:68-76`),
  `_PORTFOLIO_SNAPSHOTS_SCHEMA` (`db/bigquery.py:191-201`).
- `ensure_schema_current(table_name, schema)` (`db/bigquery.py:144-176`) is a
  shared, generic additive-migration function — every table's `ensure_*` is a
  thin one-line binding over it (e.g. `ensure_watchlist_schema_current`,
  `db/bigquery.py:373-379`). `companies` should add `ensure_companies_schema_current()`
  the same way, not a bespoke migration.
- **No MERGE/upsert helper exists anywhere in the project** — confirmed independently
  by frame.md and by this research. The closest analog, `add_watchlist_ticker`
  (`db/bigquery.py:382-410`), uses an `INSERT ... WHERE NOT EXISTS` guard, not a
  true upsert (it never updates existing rows). `upsert_company()` needs an actual
  `MERGE` statement since company name/hop_url/isin can be corrected over time —
  this is genuinely new SQL shape for this codebase, worth flagging explicitly in
  the plan rather than assuming a copy-paste source exists.
- `list_distinct_tickers()` (`db/bigquery.py:1114-1127`) and `list_distinct_companies()`
  (`db/bigquery.py:1130-1144`) already exist but derive from the `announcements`
  table (`DISTINCT ticker`/`DISTINCT company` WHERE NOT NULL), not from any
  `companies` dictionary table. They back `/autocomplete/tickers`,
  `/autocomplete/companies`, and the `POST /watchlist/{ticker}` validation guard
  (`src/api.py:207-218, 220-231, 251-254`). PUL-53 does not require touching these —
  noted only because a future ticket could swap their source to the new table.

### Startup wiring — where create/ensure calls must be added

All `create_*_table_if_not_exists()` / `ensure_*_schema_current()` call sites in
the repo:

| Call site | Tables wired |
| --- | --- |
| `main.py:41-43` | announcements, x_posts (no watchlist — main.py doesn't need it) |
| `src/api.py:146-149` (`@app.on_event("startup")` → `_create_watchlist_table()`) | watchlist only |
| `post_main.py:239-242` | announcements, x_posts |
| `scripts/test_bq.py:50-55` | announcements, x_posts, watchlist (manual integration test, not a startup hook) |

**Gap already in the codebase**: `create_portfolio_snapshots_table_if_not_exists()`
/ `ensure_portfolio_snapshots_schema_current()` are called from **none** of these
— they only run inside `tests/test_bigquery.py`. This means portfolio_snapshots
relies on having been created manually/once in BQ. **Do not copy this gap** —
`/10x-plan` should pick an explicit startup hook for `companies` (most likely
`src/api.py`'s existing `_create_watchlist_table` hook, renamed/extended, since
that's the only call site that fires on every Cloud Run cold start; `main.py`
fires once per scrape cycle and is also a reasonable second call site since
Phase A's write path lives there).

### Test mocking — two independent places, both must be updated

1. **Unit tests** (`tests/test_bigquery.py`) mock `db.bigquery._get_client` directly
   (not the public functions). Pattern confirmed for both watchlist
   (lines 701-783, e.g. `test_create_watchlist_table_creates_on_not_found`:701-714,
   `test_add_watchlist_ticker_inserts_with_not_exists_guard`:724-737) and
   portfolio_snapshots (lines 307-393). A new `tests/test_bigquery.py` block for
   `companies` should mirror this exactly — mock `_get_client`, not `upsert_company`.

2. **E2E tests** (`tests/e2e/conftest.py:203-228`, `live_server_url` fixture) mock
   at the `src.api.<name>` import boundary, one `patch(...)` per function:
   `list_announcements_admin`, `list_announcements_user`, `list_distinct_tickers`,
   `list_distinct_companies`, `list_x_posts_admin`, `get_latest_snapshot_for_wallet`,
   `get_latest_snapshot_before`, `create_watchlist_table_if_not_exists`,
   `ensure_watchlist_schema_current`, `add_watchlist_ticker`, `remove_watchlist_ticker`,
   `list_watchlist_tickers`, `list_announcements_for_watchlist` (lines 209-221).
   **Whichever create/ensure/upsert functions for `companies` end up imported into
   `src/api.py` (even just for the startup hook) must be added to this list**, or
   E2E tests will hit a real (unmocked) BigQuery client at startup — this is the
   exact failure mode flagged in project memory (`feedback-e2e-conftest-bq-mocking`).

### Phase A ripple — `_extract_ticker_company` / `parse_announcement` signature change

Confirmed fully enumerable (single call chain, no fan-out):

- `_extract_ticker_company` (`src/parser.py:175-201`) has exactly one caller:
  `parse_announcement` at `src/parser.py:50`. It already fetches the profile page
  (`profile_resp = get(profile_url)`, line 187) and builds `profile_soup`
  (line 191) but **discards both** after extracting ticker/company — capturing
  `profile_url` (already a local variable, line 183) and `isin` (new: parse
  `data-isin` off `profile_soup.select_one("#quotes-profile-header-box")`) is a
  same-function, no-new-HTTP-call change.
- `parse_announcement` (`src/parser.py:38`) has 2 production-relevant callers:
  `main.py:55` and the test suite `tests/test_parser.py` (10 call sites,
  lines 95-213).
- `ParsedContent` (`src/parser.py:30-35`, 4 fields today) is constructed in 6
  places, all inside `src/parser.py` (lines 47, 71, 73, 85, 90, 93) — adding
  `profile_url`/`isin` fields means touching all 6 constructor calls (most will
  just thread through `None` on failure paths).
- `update_parsed_content` (`db/bigquery.py:514-519`) has exactly one production
  caller: `main.py:67`. Its UPDATE statement (lines 526-532) and one existing
  unit test (`tests/test_bigquery.py:490-505`,
  `test_update_parsed_content_sets_three_fields`) would need the new
  parameter(s) — though per frame.md's resolved design, `companies` is a
  separate table written via `upsert_company()`, so this may turn out to be a
  **new call added at `main.py:67`'s vicinity** (alongside, not inside,
  `update_parsed_content`) rather than a signature change to
  `update_parsed_content` itself. `/10x-plan` should decide explicitly whether
  `isin`/`profile_url` flow through `ParsedContent` → a new `upsert_company()`
  call in `main.py`, vs. being added as columns on `announcements` too — frame.md's
  schema decision (`isin`/`hop_url` belong on `companies`) implies the former.
- Test fixture gap: `tests/test_parser.py`'s mock profile-page HTML
  (`_HTML_PROFILE_PAGE`, lines ~47-49) does **not** currently include a
  `data-isin` attribute on the heading section — it will need a
  `<section id="quotes-profile-header-box" data-isin="...">` wrapper added for
  any new isin-extraction test to exercise the real markup shape (matches the
  live-verified structure in frame.md's addendum).

### Phase B — one-off seed script precedent

- `scripts/*.py` is an established convention for standalone, manually-run
  scripts (`scripts/test_bq.py`, `scripts/test_alert.py`,
  `scripts/research/bankier_html_check.py`, `scripts/research/pdf_sampler.py`),
  invoked via `uv run python scripts/<name>.py` — but every existing script
  exercises *already-built* pipeline functions for a manual round-trip/check.
  **None of them scrape a brand-new external source** — Phase B's seed script
  is structurally new territory for `scripts/`, not a copy-paste.
- Reusable conventions Phase B should still follow: `src/http_client.get()`
  (`src/http_client.py:34-56`, 0.5s rate-limit + 3 retries + 30s timeout,
  raises `ScraperError`) and `BeautifulSoup(resp.text, "html5lib")` parsing
  (used identically in `src/scraper.py:61` and `src/parser.py:49,191`).
  `src/scraper.py`'s pagination loop (lines 56-129, page-by-page with a
  cutoff-based early `break`) is the closest structural analog for a
  paginated company-list source, if the chosen source is paginated.
- **No full-GPW-company-list source is referenced anywhere in the repo** —
  confirmed by an explicit grep for `gpw.pl`, `lista spółek`, `wig`,
  `infostrefa` across source and `context/**` docs, with zero hits beyond
  frame.md's own bankier.pl per-announcement-hop verification. Choosing the
  concrete source is unresolved and explicitly deferred to `/10x-plan`
  (frame.md says the same).
- Deployment: production jobs run via Cloud Run with
  `--command=uv --args="run,python,<file>.py"` (`.github/workflows/deploy.yml:47-69`,
  covering `main.py`, `post_main.py`, `api_main.py` only). A Phase B seed
  script run from `scripts/` has no existing Cloud Run job wired for it —
  for a true one-off, local `uv run python scripts/seed_companies.py` (per the
  CLAUDE.md rule that destructive/manual infra actions are human-only) is
  consistent with project convention; provisioning a recurring Cloud Run job
  for it would be new infra, out of scope unless explicitly requested.

## Code References

- `db/bigquery.py:68-76` — `_X_POSTS_SCHEMA` (convention reference)
- `db/bigquery.py:144-176` — `ensure_schema_current` (shared generic migrator)
- `db/bigquery.py:191-223` — `_PORTFOLIO_SNAPSHOTS_SCHEMA` + create/ensure (closest single-owner-write analog; also shows the "missing startup wiring" anti-pattern to avoid)
- `db/bigquery.py:353-410` — `_WATCHLIST_SCHEMA` + create/ensure/`add_watchlist_ticker` (closest full CRUD analog; `INSERT...WHERE NOT EXISTS`, not a true upsert)
- `db/bigquery.py:514-519` — `update_parsed_content` (signature/ripple point)
- `db/bigquery.py:1114-1144` — `list_distinct_tickers` / `list_distinct_companies` (adjacent, not in scope)
- `src/parser.py:30-35` — `ParsedContent` dataclass (4 constructor call sites to extend)
- `src/parser.py:175-201` — `_extract_ticker_company` (the existing hop; `profile_url`/`profile_soup` discarded today)
- `main.py:41-43,55-67` — pipeline startup wiring + the single ticker-introduction call chain
- `src/api.py:146-149` — `@app.on_event("startup")` hook (likely second/only home for `companies` create/ensure)
- `tests/test_bigquery.py:307-393,701-783` — unit-test mocking pattern (`_get_client`)
- `tests/e2e/conftest.py:203-228` — E2E mocking pattern (`patch("src.api.<name>")`)
- `tests/test_parser.py:43-52,95-213` — parser test fixtures and call sites (HTML fixture needs `data-isin` added)
- `scripts/test_bq.py:1-60` — one-off script convention (BQ round-trip only, not scraping)
- `src/http_client.py:34-83` — `get()`/`download_binary()` reusable HTTP client
- `src/scraper.py:56-129` — pagination convention for a future Phase B source
- `.github/workflows/deploy.yml:47-69` — Cloud Run job invocation convention

## Architecture Insights

- The codebase enforces one consistent dimension-table recipe (schema-list →
  create → ensure) strictly enough that deviating from it (e.g. inventing a
  bespoke migration) would be a real outlier — `/10x-plan` should treat the
  recipe as non-negotiable scaffolding and spend its design effort only on the
  genuinely new parts: the `MERGE`-based `upsert_company()` and the Phase B
  source choice.
- Test mocking is **two separate surfaces by design** (unit mocks the BQ client
  internals; E2E mocks the API import boundary) — both must be extended for any
  new BQ-backed feature, and the codebase already has one example
  (portfolio_snapshots) where the E2E surface only partially covers it. This is
  exactly the failure mode the project has hit before (see Historical Context).
- The single-writer-call-chain shape (`main.py` → `parse_announcement` →
  `update_parsed_content`) that frame.md found for tickers also holds for the
  proposed `companies` write path — Phase A is genuinely a local, single-call-site
  change, not a multi-subsystem integration, which derisks the implementation
  plan considerably.

## Historical Context (from prior changes)

- `context/changes/companies-dictionary-table/frame.md` — full framing investigation:
  confirmed hop_url identity (bankier `profile/quote.html`, already fetched and
  discarded), confirmed single write-ownership call site, confirmed seed mechanism
  needs a separate full-coverage source, and the live-verified addendum adding
  `isin` to the schema via `data-isin` on `#quotes-profile-header-box`.
- Project memory `feedback-e2e-conftest-bq-mocking` (2026-06-23 session): "nowe
  BQ-backed endpointy potrzebują WSZYSTKICH db.bigquery.* funkcji
  domockowanych w live_server_url, nie tylko startup hooków" — directly
  predicts the trap documented above in the Test mocking section; this
  research confirms the exact current mock list so the new entries can be
  added completely rather than partially.
- Project memory `feedback-e2e-conftest-bq-mocking` companion note (2026-06-22b
  session): "nowa tabela BQ pisana tylko przez API musi mieć create/ensure
  dopatchowane w tests/e2e/conftest.py" — same lesson, reinforced.

## Related Research

- `context/changes/companies-dictionary-table/frame.md` (this change's own framing doc — primary input to this research)

## Open Questions

1. **Phase B source selection** — which concrete external source provides full
   GPW company coverage (ticker, name, a hop_url-equivalent, isin) for companies
   with zero ESPI/EBI history? Not resolvable from the codebase; needs external
   research or a user-provided source during `/10x-plan`.
2. **Where does `isin`/`hop_url` get written from Phase A** — does `main.py`
   call a new `upsert_company()` directly (parallel to `update_parsed_content`),
   or does `ParsedContent` grow fields that are unpacked at the `main.py:67`
   call site into a separate `upsert_company()` call? Either is consistent with
   frame.md's resolved design; `/10x-plan` should pick one explicitly.
3. **Startup hook ownership** — should `create_companies_table_if_not_exists()`
   / `ensure_companies_schema_current()` be wired into `src/api.py`'s existing
   hook only, `main.py` only, or both? (Recommendation above: both, since one
   writes and one reads/serves.)
