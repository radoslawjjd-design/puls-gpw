---
change_id: history-coverage-gate
title: Full-coverage gate collapses the value-history range when a holding is a recent IPO
status: impl_reviewed
created: 2026-07-26
updated: 2026-07-27
archived_at: null
tracking:
  linear: PUL-100
  github: 195
---

## Notes

`get_portfolio_history` (`db/bigquery.py:465`) emits a day only when **every** held
position has a forward-filled price that day. The series therefore starts at the
*latest* first-price date across all holdings — a sub-1% position in a freshly
listed company clamps the whole chart.

Baseline measured on real BigQuery before any change (2026-07-26, `?range=1y`):

| view | points | latency | span |
|---|---|---|---|
| Wszystkie | 71 | 1878 ms | 2026-04-16 → 2026-07-24 |
| Główny (13 positions) | 71 | 1616 ms | 2026-04-16 → 2026-07-24 |
| second portfolio (8 positions) | 249 | 1371 ms | 2025-07-28 → 2026-07-24 |

`S2B` (4 shares, 267 PLN, 0.66% of a 40 261 PLN portfolio, listed 2026-04-16) is
the clamp. Partition scan for the 1y+400d window is 2.5 MB — the performance
concern raised in PUL-100 point 3 is already answered: clustering holds despite
`company_daily_stats` growing from 16k to 1.9M rows in PUL-92. No optimisation needed.

Design decisions and rejected alternatives (backfill at `avg_buy_price`, skip
missing positions) are argued in the Linear ticket; the plan carries them forward
rather than re-deriving them.

## After (2026-07-26, same script, same ranges)

| view | points | latency | span |
|---|---|---|---|
| Wszystkie | **249** (was 71) | 1831 ms | 2025-07-28 → 2026-07-24 |
| Główny (13 positions) | **249** (was 71) | 1501 ms (was 1616) | 2025-07-28 → 2026-07-24 |
| second portfolio (8 positions) | 249 | 1382 ms | 2025-07-28 → 2026-07-24 |

`notes` reports `S2B` from 2026-04-16 at 35.70; `excluded` is empty. No latency
regression — the two extra window functions cost nothing measurable against the same
partition scan.

Criterion 4.5 was verified on a synthetic basket (`AAS`, `KRU`) over real prices,
because no real portfolio holds one of the scraper-only tickers: `AAS` has 3 rows from
2026-06-26 and previously would have clamped the year to ~20 points; it now returns the
full 249 and is disclosed as *"brak notowań przed 26.06.2026"*. That case is also the
proof that plan-review F2 was worth acting on — `AAS` is not a June 2026 listing, so the
ticket's original *"notowany od"* wording would have stated a falsehood.

Two criteria remain open by design:

- **4.2** (`ruff check .`) does not pass and never did — 33 pre-existing errors in
  `api_main.py`, `post_main.py`, `tests/test_scraper.py` and others, none in files this
  change touches (`db/bigquery.py`, `src/api.py`, `tests/test_bigquery.py`,
  `tests/test_api.py` all lint clean). Fixing them belongs in its own change.
- **4.6** — closed 2026-07-27. PR #200 squash-merged as `a8265d7`; CI deployed revision
  `puls-gpw-api-00137-sqp`; `/health` → `{"status":"ok"}` and the endpoint correctly
  rejects an anonymous read with 401. The authenticated response *shape* was not exercised
  against the deployed instance — prod `JWT_SECRET` lives in Secret Manager and cannot be
  forged locally — so it was verified through `TestClient` on identical code against the
  same real BigQuery. Visual confirmation in the browser is the owner's last mile.
