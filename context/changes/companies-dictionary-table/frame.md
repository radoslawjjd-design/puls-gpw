# Frame Brief: Companies dictionary table (PUL-53)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

No canonical ticker→name→hop_url mapping exists. `list_distinct_tickers()`
(`db/bigquery.py:1114`) returns tickers seen in parsed announcements, but with
no name or URL attached. PUL-53 is the prerequisite for a follow-up daily
company-stats ingestion job that will read `hop_url` from this table.

The ticket explicitly left two design questions open, to "resolve before
/10x-plan":
1. Seed source — scrape a GPW company-list page once, or manual/CSV seed?
2. Write ownership going forward — auto-upsert from multiple call sites
   (parser, watchlist add, portfolio import), or a periodic full refresh?

## Initial Framing (preserved)

- **User's stated cause/approach**: build a new dimension table `companies`
  (ticker/name/hop_url/created_at/updated_at), `create_*_if_not_exists` +
  `ensure_*_schema_current` following the existing pattern, an `upsert_company()`
  MERGE helper, and a yet-undecided seed mechanism.
- **User's proposed direction**: treat seed source and write ownership as two
  open design choices to be resolved during `/10x-plan`'s interview.
- **Pre-dispatch narrowing**: not separated yet by the user at invocation time —
  the ticket's own "Open questions" section was taken as the starting point.

## Dimension Map

1. **Hop URL identity** — does `hop_url` already exist anywhere in the system,
   or does it need a wholly new source? ← directly underlies both open questions
2. **Write ownership** — how many distinct code paths can introduce a new
   ticker today, and which of those actually persist vs. only validate?
   ← ticket's open question #2
3. **Seed mechanism** — is a one-off backfill of already-known tickers
   sufficient, or is broader GPW coverage (companies with zero announcements)
   required? ← ticket's open question #1

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| Hop URL is already computed somewhere and only needs to be persisted | `db/bigquery.py:486-487` docstring: ticker/company are populated "after a second HTTP hop to the company profile page". `src/parser.py:175-201` (`_extract_ticker_company`) fetches `profile_url` (bankier `profile/quote.html`), parses ticker+company from it, then **discards `profile_url`** — it is never returned or persisted. | STRONG |
| Write ownership spans multiple subsystems (parser + watchlist + portfolio) | Independent Explore-agent audit of the whole repo: `src/api.py:245-259` (`POST /watchlist/{ticker}`) only validates via `list_distinct_tickers()`, never writes a new ticker. `db/bigquery.py:226-270` (`save_portfolio_snapshot`) writes no ticker value at all. The **only** code path that ever introduces a new ticker is `main.py:55,67` → `parse_announcement()` → `update_parsed_content()` → `db/bigquery.py:514`. | STRONG (against multi-call-site framing) |
| One-off backfill can fully replace a new GPW-company-list scrape | The bankier hop_url is discovered via a link (`a.m-quote-list__anchor.-stock`) embedded **inside an announcement's own page** (`src/parser.py:178-180`) — there is no announcement to hop from for a company that has never published ESPI/EBI. So replaying the existing per-ticker hop only covers tickers already in `list_distinct_tickers()`; it structurally cannot reach companies with zero announcement history. | STRONG, but user overrode on product grounds (see below) |

## Narrowing Signals

User answers (AskUserQuestion round):

- **Hop URL identity — confirmed**: the intended `hop_url` IS the existing
  bankier `profile/quote.html` URL already fetched (and discarded) in
  `_extract_ticker_company`. No new "what page is this" ambiguity.
- **Write ownership — confirmed, scope narrowed**: only the parser call site
  (`main.py` → `update_parsed_content`) is a real ticker-introduction point
  today. Watchlist/portfolio integration is explicitly out of scope for PUL-53.
- **Seed mechanism — initial framing held, not the investigation's lean**: user
  wants full GPW company-list coverage (including companies that have never
  filed an ESPI/EBI announcement), not just a backfill replay of the existing
  per-ticker hop. Reasoning: pre-ESPI companies wouldn't appear in
  `list_distinct_tickers()` at all, and the user wants them in the dictionary
  regardless.

## Cross-System Convention

Reference-data tables in this codebase (e.g. `_PORTFOLIO_SNAPSHOTS_SCHEMA`,
watchlist) all follow `create_*_if_not_exists` + `ensure_*_schema_current`
(`db/bigquery.py`), but **no MERGE/upsert helper exists anywhere in the
project yet** — `upsert_company()` is a genuinely new pattern, not a copy of
an existing one. The append-only fact-table convention does not apply here by
the ticket's own design (data can be corrected/renamed).

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: two structurally different
> mechanisms were bundled under one "seed vs. write-ownership" framing, when
> they are actually independent by both evidence and the user's stated goal.

1. **Ongoing maintenance (write ownership) is resolved, not open**: the single
   existing parser call site (`main.py` → `update_parsed_content`) is the only
   place a ticker is ever introduced. Capturing the `profile_url` it already
   fetches and upserting into `companies` there is a small, local change — no
   multi-subsystem integration needed.
2. **One-time seed (full GPW coverage) is a separate, genuinely new scraper**:
   it cannot be "replay the existing per-ticker hop" because that hop only
   exists inside an announcement page — companies with zero ESPI/EBI history
   have no announcement to hop from. A full-company-list source (distinct from
   bankier's per-announcement profile link) is required for that subset, and
   it is the right call given the user's coverage goal.
3. These two mechanisms share only the table schema and `upsert_company()` —
   they are not one feature with one open design question, they are two
   independent write paths into the same table.

## Confidence

**HIGH** — both narrowed dimensions (hop URL identity, write ownership) are
backed by direct file:line evidence plus user confirmation. The seed dimension
has strong evidence for the *structural* claim (existing hop can't reach
pre-ESPI companies) and an explicit, reasoned user decision on the *product*
question (full coverage wanted) — no residual ambiguity blocking /10x-plan.

One detail intentionally left for `/10x-plan` (not resolved here, per
guardrail against solution design): which concrete external source the
full-company-list scraper should target, and whether it can produce a
bankier-style `profile/quote.html` URL or a different page format for
`hop_url`.

## What Changes for /10x-plan

Plan two independent pieces against the shared `companies` table/schema/
`upsert_company()`, not one bundled "table + seed-or-scrape decision":

- **Phase A**: schema + `create/ensure` + `upsert_company()` + wiring the
  capture of `profile_url` (already fetched in `_extract_ticker_company`) into
  the single existing parser call site. **Schema should include `isin`
  alongside `hop_url`** (see addendum below) — it is available at zero extra
  cost from the same static page fetch and is the actual key the follow-up
  daily-stats job needs.
- **Phase B**: a one-off, separate full-GPW-company-list seed script (new
  source TBD in planning) for companies with no announcement history.

## Addendum: hop_url page verified live — add `isin` to schema

User asked to verify, against the real `profile/quote.html` page, where the
follow-up ticket's daily trading-data metrics (kurs odniesienia, wolumen,
kapitalizacja, etc. — out of scope for PUL-53 itself, but the reason `hop_url`
exists) would actually come from. Findings, verified live on two different
instruments (`ECHO`/PL ISIN and `MOL`/HU ISIN):

- The static `profile/quote.html?symbol=X` page (the page `hop_url` already
  points at) renders `div.m-quotes-metric-table__data` **empty** server-side —
  the keyword/amount `ul`/`li`/`span` rows the user pointed at are filled
  client-side via JS, not present in the static HTML the existing parser hop
  fetches.
- The same static page **does** carry `data-isin` and `data-symbol` on
  `<section id="quotes-profile-header-box">` (e.g. `data-isin="PLECHPS00019"
  data-symbol="ECHO"`) — free, no extra request, available at the exact same
  hop the parser already does today.
- The actual metrics live behind a public, unauthenticated JSON endpoint:
  `GET https://api.bankier.pl/quotes/public/company-profile-chart/{ISIN}/?symbols={SYMBOL}&metrics=true&today=true`
  (`today=true` is required — without it the endpoint returns a multi-day
  aggregate window instead of the live session snapshot). Verified field names
  match the UI exactly: `Kurs_odniesienia`, `Kurs_otwarcia`, `Minimum`/
  `Maximum` (+`_data` timestamps), `Wolumen_obrotu_szt`, `Wartosc_obrotu_zl`,
  `Liczba_transakcji`, `Stopa_zwrotu_1R`, `Kapitalizacja`, `Rynek`,
  `System_notowan`.
- `symbol` (bankier's URL param, e.g. `ECHO`) and the real GPW `ticker` (e.g.
  `ECH`, parsed from the page heading `"Echo Investment SA (ECH)"`) **are not
  always the same string** — confirmed via ECHO (differs) vs. MOL (same:
  `symbol=ticker=MOL`). This is per-company, not systematic — reinforcing that
  `hop_url`/`isin`/`symbol` must be captured/stored literally, never derived
  from `ticker`.

**Decision (user-approved)**: add an `isin` column to the `companies` table
alongside `hop_url`, captured from the same `data-isin` attribute at the same
hop — `/10x-plan` should treat this as confirmed schema scope, not an open
question.

## References

- Source files:
  - `db/bigquery.py:475-512` (`insert_announcement`, docstring re: second hop)
  - `db/bigquery.py:514` (`update_parsed_content`)
  - `db/bigquery.py:1114` (`list_distinct_tickers`)
  - `db/bigquery.py:191-223` (`_PORTFOLIO_SNAPSHOTS_SCHEMA` pattern to follow)
  - `src/parser.py:38-93` (`parse_announcement`)
  - `src/parser.py:175-201` (`_extract_ticker_company` — the existing hop)
  - `main.py:55,67` (the single ticker-write call site)
  - `src/api.py:245-259` (`POST /watchlist/{ticker}` — validate-only, not a write path)
- Related ticket: Linear PUL-53, GitHub #84
- Investigation tasks: #1, #2, #3 (TaskCreate, completed)
- Live verification: `https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ECHO`,
  `?symbol=MOL`; metrics endpoint
  `https://api.bankier.pl/quotes/public/company-profile-chart/{ISIN}/?symbols={SYMBOL}&metrics=true&today=true`
  (verified live for both instruments during framing, 2026-06-23)
