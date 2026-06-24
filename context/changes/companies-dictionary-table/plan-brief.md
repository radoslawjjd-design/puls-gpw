# Companies Dictionary Table — Plan Brief

> Full plan: `context/changes/companies-dictionary-table/plan.md`
> Frame brief: `context/changes/companies-dictionary-table/frame.md`
> Research: `context/changes/companies-dictionary-table/research.md`

## What & Why

Two structurally different mechanisms were bundled under one "seed vs.
write-ownership" framing in the original ticket, when they are actually
independent: ongoing maintenance (one existing parser call site, already
fetching the data it needs) and a one-time full-coverage seed (a genuinely new
scraper, since the existing per-announcement hop can't reach companies with
zero ESPI/EBI history). This plan builds both against one shared `companies`
table, so the follow-up daily company-stats job has a canonical
ticker→name→hop_url→isin mapping to read from.

## Starting Point

`_extract_ticker_company` (`src/parser.py:175-201`) already hops to
`bankier.pl/inwestowanie/profile/quote.html?symbol=X` for every parsed
announcement and parses ticker+company — but discards the URL and the rest of
the page (including ISIN) immediately after. No dimension table for companies
exists; `list_distinct_tickers()`/`list_distinct_companies()` only derive from
the `announcements` table.

## Desired End State

A `companies` BigQuery table with one row per known GPW ticker, kept current
automatically by every new announcement going forward, and bulk-seeded once to
also cover companies that have never filed an ESPI/EBI announcement.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Hop URL identity | Existing bankier `profile/quote.html` URL | Already fetched (and discarded) at the existing parser hop | Frame |
| Write ownership scope | Parser call site only; watchlist/portfolio out of scope | Only real ticker-introduction point in the codebase today | Frame |
| Seed mechanism | Separate full-GPW scraper, not a backfill replay | Existing hop can't reach companies with zero announcements | Frame |
| Schema | Add `isin` alongside `hop_url` | Free at the same hop; the actual key the follow-up daily-stats job needs | Frame |
| Phase B source | `bankier.pl/gielda/notowania/akcje` listing page | Static, single-page, links to the exact same profile format Phase A already parses | Plan |
| Shared parsing logic location | New module `src/company_profile.py` | Keeps `parser.py` focused on announcements; avoids `scripts/` depending on `parser.py` internals | Plan |
| MERGE conflict semantics | Always overwrite (last-write-wins) | Both writers parse the identical full page — no partial rows to protect | Plan |
| Pipeline error handling | Best-effort log + continue | Dictionary enrichment is lower-stakes than the core announcement→analysis→alert flow | Plan |
| Seed script safety | `--dry-run` flag, default off | Bulk write to production data across hundreds of companies; matches existing `scripts/test_alert.py` convention | Plan |

## Scope

**In scope:**
- New `companies` table + `create`/`ensure`/`upsert_company()` in `db/bigquery.py`
- Shared `src/company_profile.py` module (profile hop+parse, listing-page link extraction)
- `_extract_ticker_company` refactor to capture `hop_url`/`isin`
- `main.py` wiring (best-effort upsert per parsed announcement)
- Startup hooks in `src/api.py` and `main.py`
- One-off `scripts/seed_companies.py` for full-GPW coverage

**Out of scope:**
- Changing `announcements` table schema or `update_parsed_content()`'s signature
- Wiring watchlist/portfolio import into `companies` writes
- Switching `/autocomplete/*` or watchlist validation to read from `companies`
- A recurring Cloud Run job for the seed script (manual one-off only)

## Architecture / Approach

Two write paths converge on one table: `main.py` → `parse_announcement()` →
`_extract_ticker_company()` → `company_profile.fetch_company_profile()` →
`upsert_company()` (per-announcement, automatic); and
`scripts/seed_companies.py` → `company_profile.extract_company_profile_links()`
→ `fetch_company_profile()` (same function) → `upsert_company()` (one-off,
manual, full coverage). The shared module is the only thing both paths
import from each other's domain.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Schema + CRUD | `companies` table + `upsert_company()` MERGE | First `MERGE` statement in the codebase — new SQL shape |
| 2. Shared parsing module | `src/company_profile.py`, `_extract_ticker_company` refactor | Test fixture needs a `data-isin` attribute added |
| 3. Wire Phase A | Pipeline writes `companies` on every announcement | Two independent test-mock surfaces (unit + E2E) must both be updated |
| 4. Phase B seed script | Full-GPW one-off coverage | `symbol` URL param ≠ real ticker — must always parse from the page, not the URL |

**Prerequisites:** None — purely additive, no infra changes beyond a new BQ table.
**Estimated effort:** ~4 implementation sessions, one per phase.

## Open Risks & Assumptions

- The bankier listing page's HTML structure (no CSS class on rows, per the
  WebFetch check) could shift without notice — `extract_company_profile_links()`
  relies on the `profile/quote.html` href substring, the most stable anchor
  found.
- A handful of companies may lack a `data-isin` attribute on the profile page;
  the plan treats this as an acceptable `NULL`, not a failure.

## Success Criteria (Summary)

- Every newly parsed announcement results in a `companies` row with a non-null `hop_url`.
- After the one-off seed run, `companies` contains several hundred more
  tickers than `list_distinct_tickers()` ever returns — companies with zero
  ESPI/EBI history are now present.
- No BigQuery write failure in either path ever blocks the core
  announcement→analysis→alert pipeline.
