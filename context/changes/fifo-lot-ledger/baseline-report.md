# Baseline comparison — proof the ledger changed no production number

Part 1 rebuilt two lot consumptions on one dated ledger and promised that **not a single
production number would move**. This records the evidence.

## Why the raw artefacts are not here

They were, until PR #249. `baseline/realized.json` and `baseline/positions.json` held
the owner's real holdings, share counts and average purchase prices, and this repository
is public. The `ai-security-review` gate caught it, and part 1's history was rewritten
to remove them.

The gate's summary overstated one detail — it reported Firebase user IDs, and there were
none; the files were keyed by opaque portfolio UUIDs and the string `user_id` did not
appear in either. The substance was right anyway: real personal financial data does not
belong in a public repository, whatever it is keyed by.

The rule from here, applied to part 2 from the start: **the evidence enters the repo,
the data does not.**

## What was compared

Both baselines were captured from the **pre-refactor** code over the real 508-row
production operation history, because once the old implementations were deleted there
would be nothing left to compare against.

### `compute_realized_pnl`

3 scopes (unfiltered, year-filtered, month-filtered), 41 tickers.

**Every pre-existing key identical.** The only difference was the two keys this change
adds: `days_held_weighted` and `days_held_max`.

### `reconstruct_positions`

4 portfolios, 40 positions, 40 closed tickers.

**Ticker, shares, `avg_buy_price`, `company_name` and the closed list all identical.**

### Independent production FIFO audit

Replaying FIFO over all 508 operation rows and comparing against every stored position:

```
glowny   positions=26 (no-ops=2)  stored=73,873.65  fifo=73,873.65  delta=+0.00
ikze     positions=17 (no-ops=1)  stored=41,544.40  fifo=41,544.40  delta=+0.00
```

This is also what disproved the ticket's premise: `avg_buy_price` was never the
time-blind weighted average PUL-114 described. It was already a FIFO remaining basis —
correct for today, frozen for every day before it, which is what part 2 fixed.

## Test baseline

51 tests across `tests/test_portfolio_realized.py`, `tests/test_brokers_xtb.py` and
`tests/e2e/test_portfolio_realized.py` were the contract for "no behaviour change".
All stayed green **with no existing assertion edited** — the standard part 2 could not
hold to, since it changes behaviour on purpose.

Three characterization tests went in *before* the refactor and passed against the old
code. Nothing in the suite covered a partially covered oversell, so without them the
wrong aggregation source would have shipped silently.
