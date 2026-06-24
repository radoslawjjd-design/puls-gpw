# Switch Autocomplete + Watchlist Validation to Companies — Implementation Plan

## Overview

Switch `GET /autocomplete/tickers`, `GET /autocomplete/companies`, and the
`POST /watchlist/{ticker}` validation guard from reading distinct
tickers/companies out of `announcements` to reading them out of the
`companies` dimension table (PUL-53). Before that switch can ship safely,
backfill `companies` with the 272 tickers that have `announcements` history
but no `companies` row — without the backfill, the switch would silently
break autocomplete/watchlist for those 272 currently-active tickers.

## Current State Analysis

`list_distinct_tickers()` / `list_distinct_companies()` (`db/bigquery.py:1189-1219`)
query `announcements` directly: `SELECT DISTINCT ticker ... ORDER BY ticker`
and `SELECT DISTINCT company ... ORDER BY company LIMIT 500`. Both are called
from `src/api.py`: `GET /autocomplete/tickers` and `GET /autocomplete/companies`
(`src/api.py:211-235`, each wrapped in a 5-minute in-memory cache via
`_AC_CACHE`/`_AC_TTL`, `src/api.py:38-51`), and `POST /watchlist/{ticker}`
(`src/api.py:249-263`) calls `list_distinct_tickers()` directly, uncached, as
its validation guard.

`companies` (PUL-53) has its full CRUD already: `create_companies_table_if_not_exists()`,
`ensure_companies_schema_current()`, `upsert_company(ticker, name, hop_url, isin)`
(`db/bigquery.py:463-519`) — MERGE-based, idempotent, keyed on `ticker`. It is
kept current going forward by `main.py`'s best-effort upsert on every parsed
announcement, plus a one-off historical seed (`scripts/seed_companies.py`)
that scraped `bankier.pl/gielda/notowania/akcje`'s current listing once.

Live verification during framing (`context/changes/switch-autocomplete-watchlist-to-companies/frame.md`)
found `companies` currently has 263 rows while `announcements` has 449
distinct tickers — **272 tickers are in `announcements` but have no row in
`companies` at all**, including tickers with `published_at` as recent as
the day before this plan was written. These are real, currently-filing
companies (verified individually: `PKP`, `ROB`, `TOW`, `SNG`, etc. all
resolve to valid bankier.pl profiles with ISINs) that are simply absent from
today's listing snapshot (delisted, suspended, in restructuring, or
otherwise off the live main-board page) — `scripts/seed_companies.py` only
ever sees that one listing page, so it structurally can't reach them.

### Key Discoveries:

- `fetch_company_profile()` (`src/company_profile.py:22-37`) resolves
  correctly when given a profile URL built directly from the **real**
  ticker (`?symbol=<real_ticker>`), not just from listing-page-derived
  symbol params — verified live for `PKP`, `ROB`, `SNG`, `TOW`. The backfill
  needs no new scraping/parsing logic, only a new URL-building entry point.
- `companies.ticker` is the MERGE key and `REQUIRED` — every row is
  inherently unique per ticker, so `list_distinct_tickers()` doesn't need a
  `DISTINCT`/null-filter once it reads from `companies`. `companies.name` is
  `NULLABLE`, so `list_distinct_companies()` still needs `WHERE name IS NOT NULL`.
  (`_COMPANIES_SCHEMA`, `db/bigquery.py:465-472`)
  Confirmed via `mcp` after PUL-53/seed_companies.py: 263 rows have a 100%
  `hop_url`/`isin` fill rate, but **the 272 backfilled-via-fallback rows in
  Phase 2 will have `name`-only entries when the per-ticker hop fails** — the
  `WHERE name IS NOT NULL` filter is what keeps those still-nameless edge
  cases (if any) out of the companies-name autocomplete without erroring.
- Existing unit tests (`tests/test_bigquery.py:659-699`) assert on the exact
  query string (`"SELECT DISTINCT ticker"`, `"LIMIT 500"`, etc.) — these
  assertions must be rewritten for the new query shape, not just re-pointed.
- `src/api.py` and its tests (`tests/test_api.py:219-270,446-477`,
  `tests/e2e/conftest.py:210-218`) patch `list_distinct_tickers`/
  `list_distinct_companies` **by name only** — since the public function
  names and signatures don't change, **none of those three files need any
  edits**. This confirms the blast radius is contained to `db/bigquery.py`
  plus the new backfill script.
- Once backfilled, `companies` will have ~535 rows (263 existing + 272
  backfilled) — already past the existing `LIMIT 500` on
  `list_distinct_companies()`. The cap must be dropped, not just reconsidered.

## Desired End State

`companies` contains a row for every ticker that has ever appeared in
`announcements` (full historical coverage, not just the current listing
snapshot), plus every company `main.py`'s pipeline upserts going forward.
`list_distinct_tickers()` / `list_distinct_companies()` read exclusively
from `companies`, unbounded. `GET /autocomplete/tickers`, `GET /autocomplete/companies`,
and `POST /watchlist/{ticker}` all continue to work, unchanged in behavior,
for every ticker that worked before this change — plus the 86 zero-history
companies from PUL-53 that didn't.

**Verification**: `SELECT COUNT(DISTINCT ticker) FROM announcements WHERE ticker IS NOT NULL` minus
`SELECT COUNT(*) FROM companies` (by ticker, via `NOT EXISTS`) returns `0`. A
manual `GET /autocomplete/tickers` and `POST /watchlist/PKP` both succeed
against the real API once the backfill has run.

## What We're NOT Doing

- Not changing `src/api.py`, `tests/test_api.py`, or `tests/e2e/conftest.py`
  — the public function names/signatures/cache behavior are unchanged; only
  `db/bigquery.py`'s internal query implementation changes.
- Not addressing the `POST /watchlist/{ticker}` guard's cache asymmetry (it
  calls `list_distinct_tickers()` uncached on every request, unlike the
  cached `GET /autocomplete/*`) — explicitly ruled out of scope during
  framing.
- Not wiring backfill into an automatic startup/pipeline self-heal — this is
  a one-off, human-triggered script (`scripts/backfill_companies.py`),
  matching the existing `scripts/seed_companies.py` convention and the
  project's human-only bulk-write posture.
- Not building retry/backoff beyond what `src/http_client.get()` already
  provides for the backfill's per-ticker hops.
- Not changing `_AC_TTL`/`_AC_CACHE` (`src/api.py:38-51`) — confirmed during
  framing that the 5-minute TTL still fits a `companies`-sourced read.

## Implementation Approach

Three phases, strictly ordered — the backfill must close the gap before the
read-path switch ships, otherwise the switch ships a regression for the 272
affected tickers:

1. **Backfill foundation** (`db/bigquery.py`) — a read-only query to find
   which `announcements` tickers are missing from `companies`, plus each
   one's best-known company name as a fallback.
2. **Backfill execution** (`src/company_profile.py` + `scripts/backfill_companies.py`)
   — hop each missing ticker's real bankier.pl profile directly and upsert;
   fall back to a minimal ticker+name row when the hop fails.
3. **Switch the read path** (`db/bigquery.py`) — point `list_distinct_tickers()`/
   `list_distinct_companies()` at `companies`, drop the now-undersized
   `LIMIT 500`.

---

## Phase 1: Backfill foundation — find tickers missing from companies

### Overview

Add a read-only query function that returns every `announcements` ticker
absent from `companies`, paired with the best fallback company name
available from `announcements` history, so Phase 2 has what it needs to
backfill.

### Changes Required:

#### 1. `list_tickers_missing_from_companies()`

**File**: `db/bigquery.py`

**Intent**: Give the backfill script (Phase 2) the exact set of
`(ticker, fallback_name)` pairs it needs to process — `announcements`
tickers with no corresponding `companies` row.

**Contract**: `list_tickers_missing_from_companies() -> list[tuple[str, str | None]]`,
raises `BigQueryError` on query failure (matching every other read function
in this file). `fallback_name` is the most recent non-null `company` value
for that ticker in `announcements` (companies can change their displayed
name over time; most recent is the best guess). Query shape:

```sql
SELECT a.ticker AS ticker,
       ARRAY_AGG(a.company IGNORE NULLS ORDER BY a.published_at DESC LIMIT 1)[SAFE_OFFSET(0)] AS fallback_name
FROM `{announcements}` a
WHERE a.ticker IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM `{companies}` c WHERE c.ticker = a.ticker)
GROUP BY a.ticker
ORDER BY a.ticker
```

#### 2. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Cover the new function with the project's standard mocked-client
pattern, placed alongside the existing companies-table tests
(`tests/test_bigquery.py:800-839`).

**Contract**: Add `test_list_tickers_missing_from_companies_returns_pairs`
(asserts the returned list of tuples and that the query string contains
`NOT EXISTS` and references both table names) and
`test_list_tickers_missing_from_companies_empty_result`.

### Success Criteria:

#### Automated Verification:

- New unit tests pass: `uv run pytest tests/test_bigquery.py -k missing_from_companies`
- Full unit suite still passes: `uv run pytest tests/test_bigquery.py`
- Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py`

#### Manual Verification:

- Run `list_tickers_missing_from_companies()` once against the real
  BigQuery dataset (throwaway `uv run python -c` invocation) and confirm the
  returned count is in the expected range (272 as of this plan's writing;
  will differ slightly by the time this runs given ongoing announcement
  ingestion).

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Backfill execution — close the gap

### Overview

A standalone, human-triggered script that resolves every ticker from Phase
1 against its real bankier.pl profile and upserts it into `companies`,
falling back to a minimal row when the hop fails. Idempotent (relies on the
existing `upsert_company()` MERGE) and safe to re-run.

### Changes Required:

#### 1. `profile_url_for_ticker()`

**File**: `src/company_profile.py`

**Intent**: Give the backfill script a way to build a bankier.pl profile URL
directly from a known ticker, without going through the listing-page link
extraction Phase B (PUL-53) already owns — verified live that bankier.pl's
`?symbol=` lookup accepts the real ticker directly for tickers tested
(`PKP`, `ROB`, `SNG`, `TOW`).

**Contract**: `profile_url_for_ticker(ticker: str) -> str`, returning
`urljoin(_BANKIER_BASE_URL, f"/inwestowanie/profile/quote.html?symbol={ticker}")`
(reuses the module's existing `_BANKIER_BASE_URL`).

#### 2. Backfill script

**File**: `scripts/backfill_companies.py` (new)

**Intent**: One-off, human-triggered backfill, following the
`scripts/seed_companies.py` convention exactly (`load_dotenv()` early,
`src.logging_setup.configure_logging()`, `--dry-run` flag, docstring stating
the `uv run python scripts/backfill_companies.py` invocation).

**Contract**: Flow: ensure `companies` exists
(`create_companies_table_if_not_exists()` + `ensure_companies_schema_current()`)
→ `list_tickers_missing_from_companies()` → for each `(ticker, fallback_name)`:
`fetch_company_profile(profile_url_for_ticker(ticker))` → if the profile
resolves **and** its parsed `ticker` matches the input ticker, upsert with
the full profile (`name`, `hop_url`, `isin`); otherwise upsert a minimal row
(`ticker`, `fallback_name`, `hop_url=None`, `isin=None`) — per the framing
decision, a ticker must stay valid for autocomplete/watchlist even when the
bankier hop fails. `--dry-run` logs what would happen instead of writing.
Final summary log line: total missing, resolved-via-hop count,
minimal-fallback count.

#### 3. Test fixture

**File**: `tests/test_company_profile.py`

**Intent**: Cover the one genuinely new, testable unit (`scripts/*.py` is
not unit-tested per project convention, matching `scripts/seed_companies.py`'s
existing thinness).

**Contract**: Add `test_profile_url_for_ticker_builds_symbol_query_url`.

### Success Criteria:

#### Automated Verification:

- New test passes: `uv run pytest tests/test_company_profile.py -k profile_url_for_ticker`
- Full test suite still passes: `uv run pytest`
- Lint passes: `uv run ruff check src/company_profile.py scripts/backfill_companies.py tests/test_company_profile.py`

#### Manual Verification:

- Run `uv run python scripts/backfill_companies.py --dry-run` against the
  real BigQuery dataset; confirm the logged missing-ticker count is in the
  expected range and a handful of sampled tickers (e.g. `PKP`, `ROB`)
  resolve via hop (not fallback).
- Run `uv run python scripts/backfill_companies.py` for real once; confirm
  rows appear in `companies` for previously-missing tickers.
- Re-run `list_tickers_missing_from_companies()` (or an equivalent ad hoc
  query) and confirm it now returns an empty list — the coverage gap is
  closed.
- Spot-check `PKP`'s row directly in BigQuery: `name`, `hop_url`, `isin` all
  populated and matching its real bankier.pl profile.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Switch the read path + drop the LIMIT cap

### Overview

Point `list_distinct_tickers()` / `list_distinct_companies()` at `companies`
instead of `announcements`, and remove the now-undersized `LIMIT 500`. No
other file needs to change — `src/api.py`'s endpoints and the watchlist
guard call these same two functions by name today.

### Changes Required:

#### 1. `list_distinct_tickers()` / `list_distinct_companies()`

**File**: `db/bigquery.py`

**Intent**: Read from the now-backfilled `companies` dimension table instead
of deriving distinct values from `announcements` on every call.

**Contract**: `list_distinct_tickers()` becomes
`SELECT ticker FROM \`{companies}\` ORDER BY ticker` (no `DISTINCT`/null
filter needed — `ticker` is the `REQUIRED` MERGE key, inherently unique and
non-null). `list_distinct_companies()` becomes
`SELECT name FROM \`{companies}\` WHERE name IS NOT NULL ORDER BY name`
(`LIMIT 500` removed — `name` stays `NULLABLE` so the filter stays).
Both functions' signatures, return types, and `BigQueryError` semantics are
unchanged.

#### 2. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Rewrite the existing query-shape assertions
(`tests/test_bigquery.py:659-699`) for the new `companies`-sourced query —
these are string-content assertions tied to the old `announcements` query
shape, not just call-site re-points.

**Contract**: Update `test_list_distinct_tickers_returns_sorted_list` to
assert `"FROM"` + the companies table reference and drop the
`"ticker IS NOT NULL"` assertion (no longer applicable). Update
`test_list_distinct_companies_returns_sorted_list_with_limit` → rename to
`test_list_distinct_companies_returns_sorted_list` and drop the
`"LIMIT 500"` assertion (replace with an assertion that no `LIMIT` clause is
present). Both empty-result tests (`tests/test_bigquery.py:672-699`) need no
change — they only check `result == []`.

### Success Criteria:

#### Automated Verification:

- Updated/full unit suite passes: `uv run pytest tests/test_bigquery.py`
- Full E2E suite still passes (confirms `tests/e2e/conftest.py`'s mocks
  remain valid with no changes): `uv run pytest tests/e2e`
- Full suite passes: `uv run pytest`
- Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py`

#### Manual Verification:

- Round-trip `list_distinct_tickers()` and `list_distinct_companies()`
  against the real BigQuery dataset (throwaway `uv run python -c`
  invocation) — confirms the hand-written SQL is valid (mocked unit tests
  never send the query string to BigQuery's parser).
- Start the API locally and call `GET /autocomplete/tickers` — confirm
  `PKP` (a ticker that was broken before Phase 2's backfill, and would have
  stayed broken without it) is present in the response.
- Call `POST /watchlist/PKP` against the running API — confirm `200` (not
  `422 Unknown ticker`).
- Call `GET /autocomplete/companies` — confirm the response size now
  exceeds 500 entries (proof the `LIMIT` removal took effect) and a
  zero-history PUL-53 company (e.g. one of the original 86) is present.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Testing Strategy

### Unit Tests:

- `db/bigquery.py`: `list_tickers_missing_from_companies()` and the rewritten
  `list_distinct_tickers()`/`list_distinct_companies()` query-shape
  assertions, mocking `_get_client` only (never the public functions) —
  `tests/test_bigquery.py`.
- `src/company_profile.py`: `profile_url_for_ticker()` happy path —
  `tests/test_company_profile.py`.

### Integration Tests:

- E2E suite (`tests/e2e`) exercises `GET /autocomplete/tickers`, `GET /autocomplete/companies`,
  and `POST /watchlist/{ticker}` through the full `live_server_url` fixture
  startup path — since the fixture's mocks aren't touched, a passing E2E run
  confirms the read-path switch is invisible to the API surface, as
  intended.

### Manual Testing Steps:

1. Phase 1: query the new function against real BQ, sanity-check the count.
2. Phase 2: dry-run the backfill, sanity-check the sample, run it for real,
   confirm the coverage gap closes to 0, spot-check `PKP`.
3. Phase 3: round-trip the rewritten queries against real BQ, then exercise
   `GET /autocomplete/tickers`, `POST /watchlist/PKP`, and
   `GET /autocomplete/companies` against the running API.

## Performance Considerations

Phase 2's backfill makes one HTTP request per missing ticker (272 as of
this plan) on top of `src.http_client`'s existing 0.5s rate limit — on the
order of 2-3 minutes total, acceptable for a one-off, human-triggered
script. Phase 3's queries against `companies` (≈535 rows post-backfill) are
materially cheaper than the old `announcements`-wide `SELECT DISTINCT`
queries they replace.

## Migration Notes

Purely additive at the data layer — `companies` gains rows, no existing
table or column changes. The 272 backfilled rows are a one-time historical
catch-up; `companies` stays current going forward via `main.py`'s existing
per-announcement upsert (PUL-53) for any genuinely new ticker. A ticker that
delists with zero `announcements` history **and** is never picked up by a
future re-run of `scripts/seed_companies.py` remains a (now fully-named, in
the original PUL-53 sense) theoretical gap — out of scope here, since this
plan's backfill is keyed off `announcements`, the same source PUL-55 set out
to stop depending on for the read path.

## References

- Related frame: `context/changes/switch-autocomplete-watchlist-to-companies/frame.md`
- `db/bigquery.py:1189-1219` — `list_distinct_tickers()` / `list_distinct_companies()` (current)
- `db/bigquery.py:463-519` — `companies` schema, CRUD, `upsert_company()`
- `src/api.py:211-263` — autocomplete endpoints + `POST /watchlist/{ticker}` guard (unchanged by this plan)
- `src/company_profile.py` — `fetch_company_profile()`, `_BANKIER_BASE_URL`
- `scripts/seed_companies.py` — one-off seed convention to mirror
- `tests/test_bigquery.py:659-839` — existing query-shape + companies tests
- `tests/test_api.py:219-270,446-477`, `tests/e2e/conftest.py:210-218` — confirmed unaffected

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backfill foundation — find tickers missing from companies

#### Automated

- [x] 1.1 New unit tests pass: `uv run pytest tests/test_bigquery.py -k missing_from_companies` — 6cdc51d
- [x] 1.2 Full unit suite still passes: `uv run pytest tests/test_bigquery.py` — 6cdc51d
- [x] 1.3 Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py` — 6cdc51d

#### Manual

- [x] 1.4 `list_tickers_missing_from_companies()` round-tripped against real BigQuery, count in expected range — 6cdc51d

### Phase 2: Backfill execution — close the gap

#### Automated

- [x] 2.1 New test passes: `uv run pytest tests/test_company_profile.py -k profile_url_for_ticker` — 205d1fd
- [x] 2.2 Full test suite still passes: `uv run pytest` — 205d1fd
- [x] 2.3 Lint passes: `uv run ruff check src/company_profile.py scripts/backfill_companies.py tests/test_company_profile.py` — 205d1fd

#### Manual

- [x] 2.4 `--dry-run` count/sample looks correct against real bankier.pl — 205d1fd
- [x] 2.5 Real run populates previously-missing tickers in `companies` — 205d1fd
- [x] 2.6 Coverage gap closes to 0 (`list_tickers_missing_from_companies()` returns empty) — 205d1fd
- [x] 2.7 `PKP` row spot-checked directly in BigQuery — matches live bankier.pl profile — 205d1fd

### Phase 3: Switch the read path + drop the LIMIT cap

#### Automated

- [x] 3.1 Updated/full unit suite passes: `uv run pytest tests/test_bigquery.py`
- [x] 3.2 Full E2E suite passes: `uv run pytest tests/e2e`
- [x] 3.3 Full suite passes: `uv run pytest`
- [x] 3.4 Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py`

#### Manual

- [x] 3.5 `list_distinct_tickers()`/`list_distinct_companies()` round-tripped against real BigQuery
- [x] 3.6 `GET /autocomplete/tickers` includes `PKP`
- [x] 3.7 `POST /watchlist/PKP` returns 200 (not 422)
- [x] 3.8 `GET /autocomplete/companies` exceeds 500 entries and includes a zero-history PUL-53 company
