# Baseline comparison — what the dated basis actually changed

Phase 2 replaced one constant cost basis per ticker with a step function from the FIFO
lot ledger, and deleted the `ops_basis` CTE. This records what that did to the real
curve, measured against the pre-change series captured in Phase 1.

**The series themselves are not committed.** This repository is public and the artefact
is a year of portfolio value keyed by Firebase user id (plan review F2). What a future
reader needs to check the equivalence claim is the comparison, and that is below.

## The right edge does not move (PUL-100)

The invariant: the chart's right-hand end must equal today's reported P&L, or the
correction is not a correction — it is a second, different number.

Measured with **both** bases computed inside one query, over one price snapshot, so the
market cannot move between the two readings:

| wallet | tickers held | segments | Δ value | Δ P&L | dropped |
| -- | --: | --: | --: | --: | -- |
| `626e9da1…` | 1 | 0 | +0.0000 | +0.0000 | — |
| `57ed5830…` | 8 | 27 | +0.0000 | +0.0000 | — |
| `6c6fdd5b…` | 12 | 78 | +0.0000 | +0.0000 | — |
| `10414536…` | 9 | 27 | +0.0000 | +0.0000 | — |
| `d49d0121…` | 13 | 78 | +0.0000 | +0.0000 | — |

Zero drift, zero holdings dropped by the tightened predicate. The first wallet holds
only a hand-entered position: **0 segments**, and it still prices correctly through the
`avg_buy_price` fallback — the case the plan flagged as load-bearing rather than
defensive.

> **A false alarm worth recording.** Diffing the *stored* Phase 1 baseline against a
> fresh run first showed the right edge moving by −40.55 / −45.21 / −0.38 PLN. The
> signature gave it away: `Δvalue` equalled `Δpnl` exactly, which can only happen when
> the basis is unchanged and the *price* moved — `Δpnl = Σ shares × Δpx = Δvalue`. The
> quotes scheduler runs every 30 minutes and had refreshed today's closes between the
> capture and the diff. Comparing a stored series against a live one measures the
> market as well as the change; the single-query comparison above does not.

## What the correction is worth

Historical closes do not move, so the historical half of the baseline diff is sound.
Over 250 trading days on the combined ("Wszystkie") view:

| | |
| -- | -- |
| days where reported P&L rises | **205** |
| days where it falls | 15 |
| days unchanged | 30 |
| largest single-day correction | **+2 143,10 PLN** (2025-10-15) |
| days lost to the tightened predicate | **0** |

Research predicted +2 143,10 PLN as the worst gap before a line of phase 2 was written.
The implementation reproduced it to the grosz.

The direction is the point: the old basis was **too high** on nine days in ten, so the
curve understated past profit for about a year. It now does not.

## `ops_basis` is gone, and was worse than it looked

The deleted CTE stood in for tickers whose position row the import had removed. Part 1
measured it wrong for 8 of 20 such tickers — BAC +11,33%, TOR +9,47%, LPP +4,96%. The
ledger serves the same tickers with a dated cost instead of one all-buys average.

Its replacement also covers a case `ops_basis` never reached: four tickers (CBF, KRU,
SNT, XTB) were sold to zero and bought again, so they *have* a live position row and the
`COALESCE` fallback never fired for them. Their basis was simply today's, applied to
every day before the zero crossing. How far each one's basis actually travels:

| ticker | lowest basis | highest basis | spread |
| -- | --: | --: | --: |
| LPP | 14 820,59 | 15 335,00 | 514,41 |
| SNT | 229,80 | 316,00 | 86,20 |
| KRU | 378,28 | 430,00 | 51,72 |
| PAS | 56,00 | 103,35 | 47,35 |
| CBF | 162,80 | 188,40 | 25,60 |
| XTB | 69,35 | 81,96 | 12,61 |

SNT is the ticker PUL-114 named in its own headline example — for the wrong reason, as
part 1 showed, but it turns out to be one of the four this change most affects.

## Segment volume

210 segments across all of production, 78 on the largest wallet. A segment is emitted
only on a day a key's own basis moved; without that rule the same information takes
1 422 rows, because every ticker re-emits an unchanged basis on every other ticker's
trading day.

## PUL-29 compliance, stated rather than assumed

`context/foundation/lessons.md` records the rule this change had to meet: **mocked
BigQuery tests do not verify SQL syntax**, so any change to hand-written SQL owes a real
round trip *and* a cheap regression assertion on the query string.

The `ai-code-review` gate failed PR #250 on exactly this, and it was right — the round
trip had been done many times over (everything above is its output) but nothing in the
repo pinned the new constructs, so a later edit could drop the `dated_basis` join and no
test would notice.

- **Round trip on real BigQuery** — the whole verification above, run against production
  rather than against `scripts/test_bq.py`'s scratch table.
- **Query-string regression** — two tests now pin the `UNNEST(@basis_segments)` join, the
  `dated_basis` CTE, the `ORDER BY valid_from DESC` that resolves the step function, the
  `COALESCE` fallback, the `portfolio_id` join condition, and the typed-empty-relation
  branch taken when a wallet has no segments.
- **Reserved keywords** — the new identifiers are `basis_seg`, `dated_basis`,
  `valid_from`, `basis`, `rn`, `usable`, `basis_gaps`. None appears in BigQuery's
  reserved list, so none needs backticks. Checked, not assumed.

## Assertions changed, and why

Phase 2 changes behaviour deliberately, so unlike part 1 it could not leave every
existing assertion untouched. One did change:

`tests/test_bigquery.py::test_history_zero_share_day_neither_pays_nor_counts_towards_coverage`
pinned `COUNTIF(px_ff IS NOT NULL) AS covered`. The predicate widened from "priced" to
"priced **and** costed", and value, P&L and the coverage counter all moved together on
purpose — splitting them would let `covered > 0` admit a day whose value nobody costed.
The test now pins the wider predicate and the fact that all three share it.

Seven history tests also gained a `stub_basis_segments` fixture: `get_portfolio_history`
now reads broker operations and stored positions before it runs, and without the stub
those tests would feed history-shaped mock rows into the ledger.
