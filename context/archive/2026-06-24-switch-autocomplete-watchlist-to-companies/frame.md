# Frame Brief: Switch autocomplete + watchlist validation from announcements to companies

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

`GET /autocomplete/tickers`, `GET /autocomplete/companies` (`db/bigquery.py:1189-1219`)
and the `POST /watchlist/{ticker}` validation guard (`src/api.py:256`) read
distinct tickers/companies from `announcements`. Per PUL-55, this means the
86 companies seeded into `companies` (PUL-53) with zero `announcements`
history are invisible in autocomplete and rejected by the watchlist guard.

## Initial Framing (preserved)

- **User's stated cause/approach**: switch `list_distinct_tickers()` /
  `list_distinct_companies()` (or new equivalents) to query `companies`
  instead of `announcements`; also revisit the `LIMIT 500` cap and confirm
  the 5-min in-memory cache TTL (`src/api.py:38-51`) still makes sense.
- **User's proposed direction**: implement exactly as scoped in PUL-55.
- **Pre-dispatch narrowing**: user confirmed (1) the 86-zero-history-company
  visibility gap is the real core problem, LIMIT/cache are secondary
  "still makes sense?" checks; (2) the watchlist guard's cache asymmetry
  (it calls `list_distinct_tickers()` uncached on every POST, unlike the
  cached `GET /autocomplete/*`) is explicitly out of scope — data-source
  switch only; (3) the ticket's 263/86 figures (dated 2026-06-23) should be
  re-verified live against BigQuery before deciding on the LIMIT revisit.

## Dimension Map

1. **Data-source completeness** — is `companies` actually a safe drop-in
   replacement for `announcements` as the sole read source? ← turned out to
   be the real issue
2. **LIMIT 500 cap** — does the cap still make sense now that `companies` is
   a curated, bounded dimension table? (user's stated framing)
3. **Cache TTL/keying** (`_AC_CACHE`/`_AC_TTL`, `src/api.py:38-51`) — does
   5-min in-memory TTL still fit the new source?
4. **Watchlist-guard cache asymmetry** — ruled out of scope by the user
   pre-dispatch; not investigated further.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. `companies` is a safe, complete replacement for `announcements` | Live BigQuery query (2026-06-24): `companies` has 263 rows; `announcements` has **449** distinct tickers. **272 tickers** with `announcements` history (some with `published_at` as recent as **2026-06-23, same day**) have **no row in `companies` at all** — e.g. `PKP` (PKP Cargo SA w restrukturyzacji), `ROB` (Robyg SA), `TOW` (Tower Investments SA), `SNG`, `UNI`, `UCG`, etc. Direct profile fetch confirms these are real, parseable companies with valid ISINs — they are simply **absent from today's `bankier.pl/gielda/notowania/akcje` listing** (delisted, suspended, in restructuring, or otherwise off the live main-board listing) even though they keep filing ESPI/EBI announcements. The one-off seed script (`scripts/seed_companies.py`) only ever scrapes that single listing snapshot, so it structurally cannot and will never capture these. | **STRONG** |
| 2. `LIMIT 500` cap needs revisiting | `companies` currently has 263 rows (under the cap, no-op today); but `list_distinct_companies()` is keyed on `announcements.company` today (448 distinct), also under 500. Once switched to `companies`, the cap is irrelevant at current scale either way — this is a real but low-stakes cleanup, not blocking. | WEAK (real, but secondary) |
| 3. Cache TTL/keying still fits | No evidence either source size or volatility differs enough to invalidate the existing 5-min TTL design — `companies` changes less often than `announcements` if anything (no per-announcement churn target for autocomplete display). | WEAK (likely fine as-is) |
| 4. Watchlist-guard cache asymmetry | Out of scope per user — not investigated. | N/A (excluded) |

## Narrowing Signals

- User confirmed the 86-company visibility gap is the real core concern —
  but the live count check (which the user asked for) revealed the *actual*
  gap runs in the opposite direction too and is **far larger**: 272 tickers
  that already work today (via `announcements`) would silently **stop**
  working if `companies` becomes the sole source, with zero overlap with the
  86-company framing.
- Sample of "missing from `companies`" tickers all resolve to real, valid
  bankier.pl profiles with ISINs when fetched directly — this rules out
  "bad ticker data" or "test/junk rows in announcements" as the explanation.

## Cross-System Convention

This exact risk was **already flagged and accepted, but under-measured**, in
PUL-53's own plan review and Migration Notes:

- `context/archive/2026-06-23-companies-dictionary-table/reviews/plan-review.md:36-49`
  (finding F2): *"Plan promised every ticker in `list_distinct_tickers()`
  gets a `companies` row... False for delisted/merged tickers with no future
  announcements... Confidence: HIGH — GPW delistings/mergers are routine."*
  Recommended fix: narrow the success criteria and name the gap as accepted,
  out-of-scope risk.
- `context/archive/2026-06-23-companies-dictionary-table/plan.md:503-518`
  (Migration Notes): *"a ticker that delisted, merged, or was suspended
  before this shipped and never files another announcement... remains
  permanently absent from `companies`. This is an accepted gap for this
  ticket; the actual count of affected tickers is unmeasured. **If the
  follow-up daily company-stats job needs full `list_distinct_tickers()`
  coverage, that ticket should account for this gap explicitly rather than
  assume `companies` is a superset.**"*

PUL-55 is exactly that follow-up — and its current scope does not account
for the gap PUL-53 explicitly warned about. The gap is also not edge-case
small: at 272/449 (61%) of historically-active tickers, and includes
same-day filers, not just stale delistings.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: `companies` is not yet a safe
> sole source of truth for autocomplete/watchlist validation — switching to
> it as literally scoped would silently make 272 currently-valid,
> still-announcing tickers (including same-day filers) invisible in
> autocomplete and rejected by the watchlist guard. The plan must explicitly
> decide how to close or bound this gap, not just swap the query source.

The original framing (switch the data source) is directionally correct and
still the right end state — but it is missing the half of the picture PUL-53
already warned about. Planning straight from the literal ticket text would
ship a regression for any of the 272 affected tickers' existing/future
watchlist users, while only fixing visibility for the 86 zero-history
companies. The LIMIT-500 and cache-TTL questions remain valid secondary
checks but are low-stakes compared to this.

## Confidence

**HIGH** — the gap is directly measured against live BigQuery and live
bankier.pl data (not inferred), a sample of "missing" tickers was verified
individually to be real/valid, and the exact risk was independently
predicted in PUL-53's own plan-review and Migration Notes before this
session ever ran a query.

## What Changes for /10x-plan

The plan needs an explicit phase/decision for **closing or bounding the
272-ticker gap** before or alongside the read-path switch — candidates to
weigh (not decided here): re-running the idempotent seed script against
today's listing (closes the "seed run was incomplete/stale" portion, ~152
of the gap per today's 415-link listing vs 263 seeded rows, but not the
genuinely-delisted/restructuring portion like `PKP`); backfilling
`companies` with the `announcements`-only tickers directly (closes the full
gap regardless of current listing-page presence); or a read-time
union/fallback. Whichever direction is chosen, the plan's success criteria
must include a **coverage check against `announcements`' distinct tickers**
(not just "row count in expected range" — PUL-53's Phase 4 manual
verification used that weaker check and is exactly how this gap went
undetected for a full day).

## References

- `db/bigquery.py:1189-1219` — `list_distinct_tickers()` / `list_distinct_companies()`
- `src/api.py:211-263` — autocomplete endpoints + `POST /watchlist/{ticker}` guard
- `src/api.py:38-51` — `_AC_CACHE`/`_AC_TTL` (5-min cache)
- `scripts/seed_companies.py` — one-off seed, single listing-page snapshot
- `src/company_profile.py` — `extract_company_profile_links()` / `fetch_company_profile()`
- `context/archive/2026-06-23-companies-dictionary-table/reviews/plan-review.md:36-49` (finding F2)
- `context/archive/2026-06-23-companies-dictionary-table/plan.md:503-518` (Migration Notes)
- Live verification (this session, 2026-06-24): direct BigQuery counts +
  direct bankier.pl fetches for `PKP`, `ROB`, `SNG`, `TOW`, etc.
