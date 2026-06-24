# Switch Autocomplete + Watchlist Validation to Companies — Plan Brief

> Full plan: `context/changes/switch-autocomplete-watchlist-to-companies/plan.md`
> Frame brief: `context/changes/switch-autocomplete-watchlist-to-companies/frame.md`

## What & Why

PUL-55 asks to switch `/autocomplete/tickers`, `/autocomplete/companies`,
and the `POST /watchlist/{ticker}` guard from reading `announcements` to
reading the `companies` dimension table (PUL-53), so the 86 companies with
zero announcement history stop being invisible. **The actual problem to plan
around** (per frame): `companies` is not yet a safe sole source of truth —
switching as literally scoped would silently break autocomplete/watchlist
for 272 currently-active tickers that exist in `announcements` but were
never captured by `companies`' one-off, listing-page-only seed.

## Starting Point

`companies` has 263 rows (PUL-53, seeded once from `bankier.pl`'s current
GPW listing page). `announcements` has 449 distinct tickers, including many
delisted/suspended/restructuring companies (e.g. `PKP`, `ROB`, `TOW`) that
still file ESPI/EBI announcements but are off today's listing page and so
were never seeded. `list_distinct_tickers()`/`list_distinct_companies()`
(`db/bigquery.py:1189-1219`) still read `announcements` directly today.

## Desired End State

Every ticker that has ever appeared in `announcements`, plus every PUL-53
zero-history company, has a row in `companies`. Autocomplete and watchlist
validation read exclusively from `companies`, unbounded — no currently
working ticker breaks, and the 86 originally-invisible companies become
visible.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| How to close the 272-ticker gap | Backfill via direct per-ticker hop (`fetch_company_profile` by real ticker) | Reuses 100% existing scraping code, closes the gap including delisted/restructuring tickers a re-seed can't reach | Plan (user-confirmed) |
| `LIMIT 500` on `list_distinct_companies()` | Remove entirely | Post-backfill `companies` already has ~535 rows — the cap would silently truncate today, not just eventually | Plan (user-confirmed) |
| Backfill execution model | One-off human-triggered script (`scripts/backfill_companies.py`) | Matches existing `scripts/seed_companies.py` convention and the project's human-only bulk-write posture | Plan (user-confirmed) |
| Fallback when a ticker's hop fails | Insert minimal row (ticker + `announcements`-derived name, null `hop_url`/`isin`) | Keeps the ticker valid for autocomplete/watchlist even when bankier can't be reached — the actual goal of this ticket | Plan (user-confirmed) |
| `src/api.py` changes | None | Function names/signatures/cache are unchanged — only `db/bigquery.py`'s internal query changes | Plan |
| Watchlist guard's cache asymmetry | Out of scope | Pre-existing behavior unrelated to the data-source switch | Frame |

## Scope

**In scope:**
- New `list_tickers_missing_from_companies()` query (Phase 1)
- New `scripts/backfill_companies.py` + `profile_url_for_ticker()` helper (Phase 2)
- `list_distinct_tickers()`/`list_distinct_companies()` switched to `companies`, `LIMIT 500` dropped (Phase 3)

**Out of scope:**
- `src/api.py`, `tests/test_api.py`, `tests/e2e/conftest.py` (no changes needed)
- Watchlist guard cache asymmetry
- Automatic/startup-wired self-healing backfill

## Architecture / Approach

Sequential, gap-before-switch: Phase 1 finds what's missing, Phase 2 closes
it via a one-off script reusing the existing bankier.pl profile-hop module,
Phase 3 flips the read path only once the gap is verifiably closed.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backfill foundation | Query to find tickers missing from `companies` | Query correctness (ARRAY_AGG fallback-name logic) |
| 2. Backfill execution | `companies` backfilled to ~535 rows, gap closed to 0 | Per-ticker hop failures handled via minimal-row fallback |
| 3. Switch read path | Autocomplete/watchlist read from `companies`, no `LIMIT` | Regression if Phase 2 didn't fully land first |

**Prerequisites:** PUL-53 merged (done); Phase 2 must complete and be
manually verified before Phase 3 starts.
**Estimated effort:** ~1 session across 3 phases.

## Open Risks & Assumptions

- A handful of very old delistings may not resolve via direct ticker hop
  even with the fallback (mitigated: minimal row still keeps them valid).
- Backfill's ~272 HTTP hops take ~2-3 minutes at the existing 0.5s rate
  limit — acceptable for a one-off script, not a concern.

## Success Criteria (Summary)

- `list_tickers_missing_from_companies()` returns empty after Phase 2.
- `GET /autocomplete/tickers` includes previously-broken tickers (e.g. `PKP`).
- `POST /watchlist/PKP` returns 200, not 422.
- `GET /autocomplete/companies` returns more than 500 entries.
