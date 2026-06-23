# Companies Dictionary Table (ticker, name, hop_url, isin) Implementation Plan

## Overview

Add a new BigQuery dimension table `companies` (ticker, name, hop_url, isin) with
two independent write paths feeding it: (1) the existing per-announcement parser
hop, enhanced to capture `hop_url`/`isin` it already fetches but discards today,
and (2) a new one-off seed script that scrapes `bankier.pl`'s full stock listing
page to cover companies with zero ESPI/EBI announcement history. This is the
prerequisite for a follow-up daily company-stats ingestion job (separate ticket)
that will read `hop_url`/`isin` from this table.

## Current State Analysis

No canonical ticker→name→hop_url→isin mapping exists. `list_distinct_tickers()`
/`list_distinct_companies()` (`db/bigquery.py:1114-1144`) derive ticker/company
from the `announcements` table, with no URL or ISIN attached. The only place a
new ticker is ever introduced today is `main.py:55-67` → `parse_announcement()`
→ `update_parsed_content()`. `_extract_ticker_company` (`src/parser.py:175-201`)
already does an HTTP hop to `bankier.pl/inwestowanie/profile/quote.html?symbol=X`
and parses ticker+company from the page heading — but discards `profile_url`
and the rest of the page (including `data-isin`) after extracting those two
fields.

Live verification (captured in `frame.md`'s addendum) confirmed the same
`profile/quote.html?symbol=X` page also carries `data-isin`/`data-symbol` on
`<section id="quotes-profile-header-box">`, and that this exact page format is
also what `bankier.pl/gielda/notowania/akcje` (the full stock listing page)
links to per company — confirmed live during this planning session via
`WebFetch`: the listing page is a single, static, server-rendered page with one
link per company in the form `/inwestowanie/profile/quote.html?symbol=[TICKER-PARAM]`.
This means both write paths converge on the same page format and the same
parsing logic, just with different entry points.

## Desired End State

A `companies` table exists in BigQuery with one row per known GPW ticker
(`ticker`, `name`, `hop_url`, `isin`, `created_at`, `updated_at`). It is kept
current automatically by the existing announcement pipeline (every parsed
announcement upserts its company), and was bulk-seeded once via a one-off
script covering the full GPW listing (including companies with no ESPI/EBI
history). Verification: querying `companies` in BigQuery returns a row for
every currently GPW-listed company (per the Phase 4 seed) plus every ticker
that receives a new announcement going forward, each with a non-null
`hop_url`. A ticker in `list_distinct_tickers()` that delisted before this
shipped and never announces again is a known, accepted gap — see Migration
Notes.

### Key Discoveries:

- `_extract_ticker_company` (`src/parser.py:175-201`) already fetches the
  exact page Phase A needs — `profile_url` (line 183) and `profile_soup`
  (line 191) are both already in scope, just unused beyond the heading parse.
- Three existing dimension tables (`_WATCHLIST_SCHEMA`, `_X_POSTS_SCHEMA`,
  `_PORTFOLIO_SNAPSHOTS_SCHEMA` in `db/bigquery.py`) all follow one identical
  schema-list → `create_*_if_not_exists()` → `ensure_*_schema_current()`
  recipe, bound through the shared `ensure_schema_current()` (`db/bigquery.py:144-176`).
  No `MERGE`/upsert helper exists anywhere yet — `upsert_company()` is new SQL
  shape for this codebase.
- Test mocking is two independent surfaces that must both be extended:
  `tests/test_bigquery.py` mocks `db.bigquery._get_client` directly; `tests/e2e/conftest.py`'s
  `live_server_url` fixture (lines 203-228) mocks each `src.api.<name>` import
  individually. Only functions actually imported into `src/api.py` need the
  E2E mock — `upsert_company()` is called from `main.py`, never `src/api.py`,
  so it does not need an E2E mock entry.
- The bankier `symbol` URL query param and the real GPW `ticker` are **not
  always the same string** (verified live: `ECHO`→`ECH`, `MOL`→`MOL`) — the
  real ticker must always come from parsing the profile page heading, never
  from the listing page's URL param. See Critical Implementation Details.
- `bankier.pl/gielda/notowania/akcje` is a single, static, server-rendered page
  (no pagination/JS) with one `/inwestowanie/profile/quote.html?symbol=X` link
  per company (verified live via WebFetch during planning).

## What We're NOT Doing

- Not changing `update_parsed_content()`'s signature or the `announcements`
  table schema — `hop_url`/`isin` live only on the new `companies` table,
  written via a separate `upsert_company()` call.
- Not wiring watchlist, portfolio import, or any other subsystem into
  `companies` writes — confirmed in `frame.md` that the parser call site is
  the only ongoing write path; the seed script is the only other one.
- Not switching `/autocomplete/tickers`, `/autocomplete/companies`, or the
  `POST /watchlist/{ticker}` validation guard to read from `companies` instead
  of `list_distinct_tickers()`/`list_distinct_companies()` — out of scope for
  PUL-53, a candidate for a future ticket.
- Not provisioning a recurring Cloud Run job for the seed script — it is a
  manual, human-triggered one-off (`uv run python scripts/seed_companies.py`),
  consistent with `scripts/*.py` convention and the project's human-only
  destructive/bulk-write policy.
- Not building retry/backoff beyond what `src/http_client.get()` already
  provides for the few hundred extra HTTP hops the seed script makes.

## Implementation Approach

Four phases, each independently testable and gated on the previous:

1. **Schema + CRUD** (`db/bigquery.py`) — pure additive scaffolding following
   the existing dimension-table recipe, plus the genuinely new `upsert_company()`
   MERGE.
2. **Shared profile-parsing module** (`src/company_profile.py`) — extract the
   hop+parse logic both write paths need (ticker/name/isin from a
   `profile/quote.html` page), enhanced to also surface `isin` and echo back
   `hop_url`. `src/parser.py` is refactored to call into it.
3. **Wire Phase A** — `main.py` calls `upsert_company()` best-effort after
   `update_parsed_content()`; startup hooks in `src/api.py` and `main.py`
   ensure the table exists; both test-mocking surfaces updated.
4. **Phase B seed script** — `scripts/seed_companies.py`, a one-off,
   `--dry-run`-gated script that scrapes the full listing page and upserts
   every company via the same shared module from Phase 2.

## Critical Implementation Details

### Ticker identity — never trust the URL's `symbol` param

The bankier listing page's link query param (e.g. `?symbol=ECHO`) and the real
GPW ticker parsed from the profile page heading (e.g. `ECH`) are not always
the same string — confirmed live for `ECHO`→`ECH` vs `MOL`→`MOL`. Phase 4's
link-extraction must only use the URL to know *where to hop*, never as the
upsert key. The upsert key always comes from `fetch_company_profile()`'s
parsed heading ticker (same function Phase A already relies on for this
reason).

## Phase 1: Companies table schema + CRUD

### Overview

Add `_COMPANIES_SCHEMA`, `create_companies_table_if_not_exists()`,
`ensure_companies_schema_current()`, and `upsert_company()` to `db/bigquery.py`,
following the existing dimension-table recipe.

### Changes Required:

#### 1. Schema + create/ensure

**File**: `db/bigquery.py`

**Intent**: Define the `companies` table shape and its create/ensure pair,
exactly mirroring `_WATCHLIST_SCHEMA`/`create_watchlist_table_if_not_exists`/`ensure_watchlist_schema_current`
(`db/bigquery.py:353-379`).

**Contract**: `_COMPANIES_TABLE_NAME = "companies"`;
`_COMPANIES_SCHEMA = [ticker STRING REQUIRED, name STRING NULLABLE, hop_url STRING NULLABLE, isin STRING NULLABLE, created_at TIMESTAMP REQUIRED, updated_at TIMESTAMP REQUIRED]`.
`ticker` is the natural/merge key. `create_companies_table_if_not_exists()` and
`ensure_companies_schema_current()` are thin bindings, identical in shape to
the watchlist pair.

#### 2. `upsert_company()`

**File**: `db/bigquery.py`

**Intent**: Insert-or-update a single company row keyed on `ticker`, always
overwriting `name`/`hop_url`/`isin`/`updated_at` on conflict (last-write-wins —
both write paths parse the same page format, so neither produces a partial
row worth protecting against overwrite).

**Contract**: `upsert_company(ticker: str, name: str | None, hop_url: str | None, isin: str | None) -> None`,
raises `BigQueryError` on failure (matching every other write function in this
file). No `MERGE` statement exists anywhere yet in this codebase — this is the
shape to use:

```sql
MERGE `{table}` T
USING (SELECT @ticker AS ticker, @name AS name, @hop_url AS hop_url, @isin AS isin) S
ON T.ticker = S.ticker
WHEN MATCHED THEN
  UPDATE SET name = S.name, hop_url = S.hop_url, isin = S.isin, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (ticker, name, hop_url, isin, created_at, updated_at)
  VALUES (S.ticker, S.name, S.hop_url, S.isin, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
```

#### 3. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Cover the new table following the exact mocking pattern used for
watchlist/portfolio_snapshots (`patch("db.bigquery._get_client", ...)`, lines
307-393 and 701-783) — never mock the public functions themselves.

**Contract**: Add `test_create_companies_table_creates_on_not_found`,
`test_companies_schema_has_required_columns`, and
`test_upsert_company_sends_merge_with_all_fields` (assert the issued query
string contains `MERGE` and the four scalar parameters are passed correctly).

### Success Criteria:

#### Automated Verification:

- New unit tests pass: `uv run pytest tests/test_bigquery.py -k companies`
- Full unit suite still passes: `uv run pytest tests/test_bigquery.py`
- Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py`

#### Manual Verification:

- Run `create_companies_table_if_not_exists()` once against the real `puls-gpw`
  BigQuery dataset (e.g. via a throwaway `uv run python -c` invocation) and
  confirm the `companies` table appears in the BigQuery console with the
  expected 6 columns.
- Round-trip `upsert_company()` against the same real dataset (same throwaway
  invocation): call it once with a test ticker to exercise the `WHEN NOT
  MATCHED` insert path, then call it again with the same ticker and a changed
  `name` to exercise the `WHEN MATCHED` update path. Confirm both rows look
  correct in the BigQuery console. This is the first `MERGE` statement in the
  codebase — mocked unit tests never send the query string to BigQuery's
  parser, so this is the only step that actually proves the SQL is valid.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Shared profile-parsing module

### Overview

Extract the bankier company-profile hop+parse logic into `src/company_profile.py`
so both the existing parser and the new seed script can share it. Enhance it
to also capture `isin` and echo back the `hop_url` it fetched.

### Changes Required:

#### 1. New module `src/company_profile.py`

**File**: `src/company_profile.py` (new)

**Intent**: Own the "given a bankier `profile/quote.html` URL, fetch it and
parse ticker/company/isin" responsibility that `_extract_ticker_company`
currently inlines, so it can be reused without `scripts/seed_companies.py`
reaching into `src/parser.py`'s announcement-parsing internals.

**Contract**: A `CompanyProfile` dataclass (`ticker: str | None`,
`company: str | None`, `isin: str | None`, `hop_url: str`) and
`fetch_company_profile(profile_url: str) -> CompanyProfile | None` (returns
`None` on `ScraperError`, matching `_extract_ticker_company`'s existing
failure behavior). Parses `isin` from the `data-isin` attribute on
`<section id="quotes-profile-header-box">` (live-verified selector, per
`frame.md`'s addendum) — return `None` for `isin` if the section/attribute is
absent, never raise.

#### 2. Refactor `_extract_ticker_company`

**File**: `src/parser.py`

**Intent**: Keep the "find the right anchor in an announcement page" logic
local (it's specific to the announcement-page DOM, not reusable), but delegate
the profile-page hop+parse to `src/company_profile.fetch_company_profile()`
instead of inlining it.

**Contract**: `_extract_ticker_company` keeps its existing parameters and
anchor-finding logic (`src/parser.py:178-185`) up through resolving
`profile_url`, then calls `fetch_company_profile(profile_url)` and returns
`(ticker, company, hop_url, isin)` — its return type changes from
`tuple[str | None, str | None]` to a 4-tuple. The one call site,
`src/parser.py:50` (`ticker, company = _extract_ticker_company(...)`), must be
updated to unpack all four values: `ticker, company, hop_url, isin = _extract_ticker_company(...)`.

#### 3. Thread the new fields through `parse_announcement` / `ParsedContent`

**File**: `src/parser.py`

**Intent**: Make `hop_url`/`isin` available to `main.py` alongside the
existing `ticker`/`company`.

**Contract**: `ParsedContent` gains two fields: `hop_url: str | None`,
`isin: str | None`. All 6 existing `ParsedContent(...)` constructor call sites
(`src/parser.py:47,71,73,85,90,93`) thread the new values through (the HTTP-failure
path at line 47 passes `None, None` for both, same as it does for ticker/company
today).

#### 4. Test fixture + new tests

**File**: `tests/test_parser.py`, `tests/test_company_profile.py` (new)

**Intent**: Keep `_extract_ticker_company`'s existing tests passing against
the new 4-tuple return, and cover `isin` extraction against the live-verified
markup shape.

**Contract**: Add a `data-isin` attribute to `_HTML_PROFILE_PAGE`'s wrapping
`<section id="quotes-profile-header-box">` so existing and new assertions can
check it. `tests/test_company_profile.py` covers `fetch_company_profile()`
directly: happy path (ticker+company+isin all present), missing-isin path,
and `ScraperError` → `None` path — mocking `src.company_profile.get`.

### Success Criteria:

#### Automated Verification:

- Parser tests pass: `uv run pytest tests/test_parser.py`
- New module tests pass: `uv run pytest tests/test_company_profile.py`
- Lint passes: `uv run ruff check src/parser.py src/company_profile.py tests/test_parser.py tests/test_company_profile.py`

#### Manual Verification:

- Run `parse_announcement` against one real, recent bankier.pl announcement
  URL (ad hoc, e.g. via a throwaway script in `scripts/research/` style) and
  confirm `hop_url` and `isin` populate with real, correct-looking values for
  that company.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Wire Phase A — pipeline write + startup hooks + test mocks

### Overview

Make the running pipeline actually populate `companies` on every parsed
announcement, ensure the table exists at every relevant startup, and close
both test-mocking surfaces.

### Changes Required:

#### 1. Pipeline write

**File**: `main.py`

**Intent**: After successfully parsing and storing an announcement, best-effort
upsert its company into the dictionary table — a BQ failure here must not
abort the announcement's analysis/alert flow (matches `save_analysis_result`'s
existing best-effort `BigQueryError` handling at `main.py:69-85`, deliberately
*not* matching `update_parsed_content`'s propagate-to-`send_alert` behavior,
since dictionary enrichment is lower-stakes than the core pipeline).

**Contract**: Immediately after the existing `update_parsed_content(...)` call
(`main.py:67`), call `upsert_company(parsed.ticker, parsed.company, parsed.hop_url, parsed.isin)`
inside its own `try/except BigQueryError` that logs a `WARNING` and continues
— no `raise`.

#### 2. Startup hooks

**File**: `src/api.py`, `main.py`

**Intent**: Guarantee the `companies` table exists before any code path tries
to write to it — on every API cold start and every scrape-pipeline run.

**Contract**: In `src/api.py`, extend the existing `@app.on_event("startup")`
hook (`src/api.py:146-149`) with `create_companies_table_if_not_exists()` +
`ensure_companies_schema_current()`; rename the hook function from
`_create_watchlist_table` to `_init_dimension_tables` since it now provisions
more than one table. In `main.py`, add the same two calls alongside the
existing `create_table_if_not_exists()`/`ensure_schema_current()`/`create_x_posts_table_if_not_exists()`
block (`main.py:41-43`).

#### 3. Test mocks

**File**: `tests/test_bigquery.py`, `tests/e2e/conftest.py`

**Intent**: Close both independent mocking surfaces so neither the unit suite
nor the E2E suite ever hits a real BigQuery client for the new functions.

**Contract**: `tests/test_bigquery.py` — add a unit test asserting `main.py`'s
new best-effort `try/except` swallows a `BigQueryError` from `upsert_company`
without propagating (mirrors any existing best-effort assertion pattern in
this file, e.g. around `save_analysis_result`). `tests/e2e/conftest.py` —
add `patch("src.api.create_companies_table_if_not_exists")` and
`patch("src.api.ensure_companies_schema_current")` to the `live_server_url`
fixture's patch block (`tests/e2e/conftest.py:216-217` area), matching the
watchlist pair exactly. `upsert_company` itself needs no E2E mock — it is
never imported into `src/api.py`.

### Success Criteria:

#### Automated Verification:

- Full unit suite passes: `uv run pytest tests/test_bigquery.py`
- Full E2E suite still passes (confirms conftest mocks are complete):
  `uv run pytest tests/e2e`
- Lint passes: `uv run ruff check main.py src/api.py tests/test_bigquery.py tests/e2e/conftest.py`

#### Manual Verification:

- Run `main.py` once against a real or recent announcement and confirm a
  corresponding row appears in the `companies` BigQuery table.
- Temporarily force `upsert_company` to raise (e.g. via a quick local patch)
  and confirm the pipeline still completes the announcement (stored, analyzed,
  alerted if applicable) — confirms the best-effort guard actually holds.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Phase B — one-off full-GPW seed script

### Overview

A standalone, manually-run script that scrapes `bankier.pl/gielda/notowania/akcje`
for every listed company and upserts each into `companies`, covering tickers
with zero ESPI/EBI announcement history. Gated by `--dry-run`.

### Changes Required:

#### 1. Listing-page link extraction (testable, lives in `src/`)

**File**: `src/company_profile.py`

**Intent**: Keep the one piece of genuinely new, riskier-to-break parsing
logic (extracting every company's profile link from the listing page) inside
`src/` where the project's test-coverage convention applies, rather than
inside the thin `scripts/` orchestrator (matching `scripts/test_bq.py`'s
existing thinness — `scripts/*.py` files are not unit-tested in this codebase).

**Contract**: `extract_company_profile_links(listing_html: str) -> list[str]`
— parses with `BeautifulSoup(listing_html, "html5lib")`, finds every anchor
whose `href` contains `profile/quote.html`, resolves relative URLs via
`urljoin`, and de-duplicates while preserving order. Add a corresponding test
in `tests/test_company_profile.py` using a small realistic fixture (multiple
rows, including one duplicate link, matching the live-verified
`/inwestowanie/profile/quote.html?symbol=X` href shape).

#### 2. Seed script

**File**: `scripts/seed_companies.py` (new)

**Intent**: One-off, human-triggered bulk seed, following the existing
`scripts/test_bq.py`/`scripts/test_alert.py` convention (`load_dotenv()` early,
`src.logging_setup.configure_logging()`, docstring stating the
`uv run python scripts/seed_companies.py` invocation).

**Contract**: `argparse` with one flag, `--dry-run` (default `False`, mirroring
`scripts/test_alert.py`'s existing `--dry-run` convention). Flow: ensure the
table exists (`create_companies_table_if_not_exists()` + `ensure_companies_schema_current()`)
→ `get("https://www.bankier.pl/gielda/notowania/akcje")` → `extract_company_profile_links(resp.text)`
→ for each link, `fetch_company_profile(link)` (rate-limited for free by
`src.http_client.get()`'s existing 0.5s delay) → if not `--dry-run`,
`upsert_company(ticker, company, hop_url, isin)`; if `--dry-run`, log what
would have been written instead → final summary log line: total links found,
upserted/would-upsert count, failed-to-parse count.

### Success Criteria:

#### Automated Verification:

- New link-extraction test passes: `uv run pytest tests/test_company_profile.py -k extract_company_profile_links`
- Full test suite still passes: `uv run pytest`
- Lint passes: `uv run ruff check scripts/seed_companies.py src/company_profile.py`

#### Manual Verification:

- Run `uv run python scripts/seed_companies.py --dry-run` against the real
  bankier.pl listing page; confirm the logged company count is in the
  expected few-hundred range and a handful of sampled tickers/names look
  correct.
- Run `uv run python scripts/seed_companies.py` for real once; confirm rows
  appear in the `companies` BigQuery table for companies that have zero rows
  in `announcements` (proof the zero-history gap is closed).
- Spot-check 2-3 companies' `isin` values against their bankier.pl profile
  page directly.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful before proceeding to the next phase.

---

## Testing Strategy

### Unit Tests:

- `db/bigquery.py`: companies create/ensure/upsert, mocking `_get_client` only
  (never the public functions) — `tests/test_bigquery.py`.
- `src/company_profile.py`: `fetch_company_profile()` happy/missing-isin/error
  paths, and `extract_company_profile_links()` happy/duplicate-link paths —
  `tests/test_company_profile.py`.
- `src/parser.py`: existing `_extract_ticker_company`/`parse_announcement`
  tests updated for the new 4-tuple / `ParsedContent` fields —
  `tests/test_parser.py`.

### Integration Tests:

- E2E suite (`tests/e2e`) exercises the full `live_server_url` fixture startup
  path with the new mocks in place — confirms no real BigQuery call leaks
  through `src/api.py`'s startup hook.

### Manual Testing Steps:

1. Phase 1: create the table against real BQ, confirm via console.
2. Phase 2: parse one real announcement, confirm `hop_url`/`isin` populate.
3. Phase 3: run `main.py` once, confirm a `companies` row appears; force an
   `upsert_company` failure and confirm the pipeline still completes.
4. Phase 4: dry-run the seed script, sanity-check the count and a sample, then
   run it for real and confirm pre-ESPI companies now have rows.

## Performance Considerations

The seed script (Phase 4) makes one HTTP request per company on top of the
listing-page request — at `src.http_client`'s existing 0.5s rate limit and an
expected few-hundred GPW-listed companies, total runtime is on the order of a
few minutes. This is acceptable for a one-off, human-triggered script; no
concurrency or batching is needed.

## Migration Notes

Purely additive — no existing table or column changes. `companies` starts
empty and is populated by (a) every new announcement going forward (Phase 3)
and (b) the one-off Phase 4 seed run. No backfill of historical `announcements`
rows into `companies` is in scope; most of their tickers will be backfilled
incidentally by the Phase 4 full-listing seed, since most GPW-listed companies
with historical announcements are still on the current listing. The exception:
a ticker that **delisted, merged, or was suspended before this shipped** and
never files another announcement is not covered by either write path (Phase
3 only writes forward, Phase 4's seed only sees the current live listing) —
it remains permanently absent from `companies`. This is an accepted gap for
this ticket; the actual count of affected tickers is unmeasured. If the
follow-up daily company-stats job needs full `list_distinct_tickers()`
coverage, that ticket should account for this gap explicitly rather than
assume `companies` is a superset.

## References

- Related research: `context/changes/companies-dictionary-table/research.md`
- Related framing: `context/changes/companies-dictionary-table/frame.md`
- Dimension-table convention: `db/bigquery.py:353-410` (watchlist)
- Existing hop to refactor: `src/parser.py:175-201`
- Pipeline write site: `main.py:55-67`
- E2E mock surface: `tests/e2e/conftest.py:203-228`
- One-off script convention: `scripts/test_bq.py:1-60`, `scripts/test_alert.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Companies table schema + CRUD

#### Automated

- [x] 1.1 New unit tests pass: `uv run pytest tests/test_bigquery.py -k companies` — 9bdb683
- [x] 1.2 Full unit suite still passes: `uv run pytest tests/test_bigquery.py` — 9bdb683
- [x] 1.3 Lint passes: `uv run ruff check db/bigquery.py tests/test_bigquery.py` — 9bdb683

#### Manual

- [x] 1.4 `companies` table created in real BigQuery dataset with expected 6 columns — 9bdb683
- [x] 1.5 `upsert_company()` round-tripped against real BigQuery (insert + update paths both verified) — 9bdb683

### Phase 2: Shared profile-parsing module

#### Automated

- [x] 2.1 Parser tests pass: `uv run pytest tests/test_parser.py` — f93dd31
- [x] 2.2 New module tests pass: `uv run pytest tests/test_company_profile.py` — f93dd31
- [x] 2.3 Lint passes: `uv run ruff check src/parser.py src/company_profile.py tests/test_parser.py tests/test_company_profile.py` — f93dd31

#### Manual

- [x] 2.4 Real announcement parse confirms correct `hop_url`/`isin` values — f93dd31

### Phase 3: Wire Phase A — pipeline write + startup hooks + test mocks

#### Automated

- [ ] 3.1 Full unit suite passes: `uv run pytest tests/test_bigquery.py`
- [ ] 3.2 Full E2E suite passes: `uv run pytest tests/e2e`
- [ ] 3.3 Lint passes: `uv run ruff check main.py src/api.py tests/test_bigquery.py tests/e2e/conftest.py`

#### Manual

- [ ] 3.4 Real pipeline run produces a `companies` row
- [ ] 3.5 Forced `upsert_company` failure does not abort the pipeline

### Phase 4: Phase B — one-off full-GPW seed script

#### Automated

- [ ] 4.1 Link-extraction test passes: `uv run pytest tests/test_company_profile.py -k extract_company_profile_links`
- [ ] 4.2 Full test suite passes: `uv run pytest`
- [ ] 4.3 Lint passes: `uv run ruff check scripts/seed_companies.py src/company_profile.py`

#### Manual

- [ ] 4.4 `--dry-run` count/sample looks correct against real bankier.pl
- [ ] 4.5 Real run populates pre-ESPI companies with zero `announcements` rows
- [ ] 4.6 Spot-checked `isin` values match bankier.pl profile pages
