---
topic: Month selector for Dywidendy and Zrealizowane (PUL-120)
researcher: Claude Opus 5
date: 2026-08-04
---

# Research: month selector alongside the year filter

Line numbers below are current as of `91e17ac`. The ticket's numbers for
`static/index.html` predate PUL-123 and sit ~9 lines lower.

## The two filter paths really are different

| layer | dividends | realized |
| -- | -- | -- |
| client state | `_ppDivYear` (`static/index.html:3713`) | `_ppRealYear` (`:3710`) |
| selector container | `#pp-div-years` (`:4627`) | `#pp-real-years` (`:4635`) |
| change handler | `:4758` | `:4749` |
| request | `&year=` (`:5469`) | `&year=` (`:5441`) |
| renderer | `_renderPortfolioDividends` (`:5485`) | `_renderPortfolioRealized` (`:5379`) |
| endpoint | `src/api.py:1413` | `src/api.py:1454` |
| cache key | `dividends:{user}:{portfolio}:{year or 'all'}` (`:1427`) | `realized:…` (`:1475`) |
| filtering | SQL predicate, `db/bigquery.py:3917` | Python post-filter, `src/portfolio_realized.py:93` |

Both selectors are rendered by one shared helper, `_renderYearSelect`
(`static/index.html:5361`), and both handlers are delegated on the container
because the `<select>` inside is rebuilt from every response (`:4745-4748`).

`_PP_MONTHS_PL` already exists at `static/index.html:5213` with full Polish month
names — no new list needed.

## The realized filter is genuinely load-bearing

`compute_realized_pnl` (`src/portfolio_realized.py:32`) consumes lots for **every**
sale and only then drops out-of-period ones (`:78-94`). The comment at `:78-80`
states why: a sale the filter drops must still consume its lots, or the next
in-scope sale re-matches against shares that already left the account. The month
filter has to land at `:93`, beside the year check — anywhere earlier and every
later sale reports proceeds against zero cost.

## The timezone claim — verified against production data, and it holds

The ticket asserts `EXTRACT(YEAR FROM occurred_at)` runs in UTC over a Warsaw-time
event. Reading the write path alone suggests the opposite: `occurred_at` comes off
openpyxl as a **naive** datetime (`src/brokers/xtb.py:157`, read via
`load_workbook(..., data_only=True)`) and is serialized with a bare `.isoformat()`
(`src/api.py:517`), so a naive Warsaw wall-clock would land in the TIMESTAMP column
reinterpreted as UTC — and UTC extraction would then return the right Warsaw year
by accident.

That reasoning is wrong, and the data says so. Hour distribution of stored trades:

```
utc_hour:  7   8   9  10  11  12  13  14  15
rows:    142 126  68  26  66  34  14  24   8
```

GPW trades 09:00–17:00 Warsaw = 07:00–15:00 UTC in CEST. The stored instants are
**true UTC**, so the ticket is right: UTC extraction misattributes an event that
happened late on a Warsaw evening.

**Current blast radius is zero.** No stored row has a Warsaw month or year
differing from its UTC one. The only late-evening rows are two `cash` operations
at 2025-05-04 22:15 UTC (= 00:15 Warsaw on the 5th) — a day shift, not a month
one, and `cash` reaches neither view. So this is a latent defect being closed
before month granularity multiplies its exposure by twelve, not a live wrong
number on screen today.

**The realized side has the same defect, which the ticket does not mention.**
`sold_year = op["occurred_at"].year` (`src/portfolio_realized.py:91`) reads the
UTC year of a tz-aware UTC datetime. Fixing only the SQL side would leave the two
views disagreeing about which month a late-evening sale belongs to.

`compute_realized_pnl` has exactly one production caller (`src/api.py:1493`), fed
by `list_broker_trades` — always tz-aware UTC out of BigQuery. Its docstring
claims it also serves freshly parsed exports; no such caller exists. Tests pass
naive datetimes, so the conversion must treat naive input as UTC rather than as
system-local, or the test corpus silently shifts by the runner's timezone.

## Constraints that must survive

- `data` CTE stays grouped by ticker alone (`db/bigquery.py:3908-3910`) — adding a
  period column splits one holding into several rows and understates each.
- `meta` CTE stays meta-first, `FROM meta LEFT JOIN data` (`:3884-3887`, `:3920-3922`).
  Written the other way the selector empties whenever the chosen period has no
  payouts, stranding the user with no way back. A month filter makes empty periods
  routine, so this constraint gets sharper.
- Cache keys must carry the month (`src/api.py:1427`, `:1475`). The invalidation
  prefix scan (`:131`) matches `dividends:{user}:` / `realized:{user}:`, so a
  longer key keeps working untouched.
- Validation happens **before** the key is built (`:1420-1426`, `:1468-1474`), with
  the comment explaining why: an unchecked value carves out its own cache entry.

## Test surface

- `tests/test_portfolio_realized.py` — pure unit tests over `compute_realized_pnl`;
  the natural home for month filtering and the FIFO-integrity assertion.
- `tests/test_api.py:2309` — the existing "validates year before building a cache
  key" test is the pattern to mirror for the month.
- `tests/e2e/conftest.py:518` — `_fake_get_dividend_summary` **ignores** its `year`
  argument, so dividend filtering cannot be asserted end-to-end without teaching
  the fake to filter. Realized needs no such change: the real
  `compute_realized_pnl` runs over `_fake_list_broker_trades` (`:531`).
- `tests/e2e/test_portfolio_dividends.py`, `tests/e2e/test_portfolio_realized.py` —
  existing selector tests to extend rather than duplicate.
