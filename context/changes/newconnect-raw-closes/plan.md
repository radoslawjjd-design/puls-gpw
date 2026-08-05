# Repair NewConnect historical closes from stooq's unadjusted series

## Current state analysis

`company_daily_stats` holds dividend-adjusted closes for every NewConnect name, written
by PUL-92's `--from-db-dir` backfill against `d_pl_txt`. PUL-98 repaired the main market
from the GPW archive and closed GH #191, but the archive covers only the main market —
46 of the 47 affected tickers are absent from it, so they were never reachable.

Measured on BAC: 208 of 250 rows in the visible year are wrong by exactly 3.42%.

Constraints this plan is built around, all verified in `research.md`:

- Raw closes are obtainable from stooq via the `o=1111111` bitmask, but **only through a
  real browser** — scripted fetching is blocked by TLS fingerprinting.
- stooq rate-limits per-symbol downloads to a handful per day. The plan must not depend
  on bulk downloading 46 symbols.
- `merge_company_daily_stats_close_correction` is already source-agnostic and update-only.
  No DB-layer change is needed.
- `zmiana_procentowa` is invariant under the adjustment and is **already correct**
  (248/249 on BAC). `zmiana_kwotowa` is not.
- Zero affected tickers are currently held or watched.

## Desired end state

Every NewConnect close that any user's portfolio history *currently* reaches is the price
actually quoted that day. The tickers that remain adjusted are known by name, reported by
a mechanism that does not depend on a heuristic, and repairable one at a time within
stooq's download limit.

The residual gap is stated rather than hidden: a user who imports broker history
containing a past NewConnect purchase would still reach adjusted rows for that ticker.
Closing that gap is the job of the follow-up in "What we're NOT doing".

## What we're NOT doing

- Bulk-repairing the 45 unheld tickers. The repair is per-ticker and durable; deferring
  it costs nothing and spending ~10 days of manual downloads on data nobody reads buys
  nothing.
- Reversing the adjustment mathematically. Measured dead: no factor makes `d_pl_txt`
  volumes whole.
- Touching `etf_quotes`. Verified unaffected — its off-tick rows are crypto ETNs.
- Changing the DB write path.
- **Surfacing unreliable history in the portfolio-history endpoint.** This was drafted as
  a phase and cut: it edits `get_portfolio_history` in `db/bigquery.py`, the exact
  function PUL-114 part 2 rewrote in the still-open PR #249. This branch is cut from
  master and cannot see that work, so the phase would guarantee a merge conflict in the
  riskiest query in the project. Split out as a follow-up to open once #249 has landed.

## Phase 1: Raw stooq CSV reader

A pure parser for the `o=1111111` download, with no network and no BigQuery.

### Changes required

- `src/stooq_raw.py` (new) — `parse_raw_csv(text) -> list[RawQuote]`.
  - Header is `Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen,LOP`; the file is
    UTF-8 with BOM.
  - Closes carry float round-trip noise (`3.139996958116`). Round to 2 decimals — the
    research verified all 574 BAC rows resolve cleanly.
  - **Reject an adjusted file.** A fractional volume means the `o=` parameter did not
    take effect; raise rather than write adjusted data a second time.
  - Reject stooq's error/limit page, mirroring the guard already in
    `backfill_historical_closes.py:240`.
- `tests/test_stooq_raw.py` (new) — parser tests including both rejection paths.

### Success criteria

#### Automated verification:
- `uv run pytest tests/test_stooq_raw.py`
- `uv run ruff check src/stooq_raw.py tests/test_stooq_raw.py`

#### Manual verification:
- Parsing the downloaded `bac_d.csv` yields 574 rows, all closes tick-aligned.

## Phase 2: Correction pass for NewConnect

### Changes required

- `scripts/correct_newconnect_closes.py` (new) — sibling to `correct_official_closes.py`,
  reusing `merge_company_daily_stats_close_correction`.
  - Input: a directory of stooq raw CSVs, one per symbol; filename maps to ticker.
  - Writes `kurs_zamkniecia` (raw), `zmiana_kwotowa` (differenced from raw closes),
    `source='stooq_raw'`.
  - **Keeps `zmiana_procentowa` as stored** — measured already correct, and PUL-98's rule
    forbids deriving it by differencing. Documented deviation: on the ex-dividend day GPW
    measures against an adjusted reference we do not have for NewConnect, so that single
    day stays approximate.
  - Reporting by default; `--apply` required to write, matching PUL-98.
  - Refuses any ticker whose file is absent — no partial-series writes.
- `tests/test_correct_newconnect_closes.py` (new).

### Success criteria

#### Automated verification:
- `uv run pytest tests/test_correct_newconnect_closes.py`
- `uv run pytest` — full suite green
- `uv run ruff check .`

#### Manual verification:
- Dry run against `bac_d.csv` reports 208 rows to change and writes nothing.

## Phase 3: Execute the repair

### Changes required

- **Capture a before-snapshot first.** The MERGE overwrites in place with no undo. Dump
  the affected `(ticker, snapshot_date, kurs_zamkniecia, zmiana_kwotowa)` rows so the
  write is reversible. These are public market prices, not user data, so the snapshot
  may live in the repo — unlike the PUL-114 baselines, which held portfolio values and
  had to be rewritten out of history.
- Run the pass for BAC with `--apply`.
- Diagnose and fix MCR's name mapping so PUL-98's existing archive path reaches it.
- `context/changes/newconnect-raw-closes/repair-report.md` — rows changed per ticker,
  before/after spot checks, the executed commands.

Expected user-visible effect, stated so it is not mistaken for a regression: historical
values on BAC-held days rise by 3.42%, so *reported portfolio growth over that window
falls*. This is the same shape as PUL-98's correction and ships silently, consistent with
the decision taken for PUL-114's curve correction in this batch.

### Success criteria

#### Automated verification:
- Post-repair query: zero BAC rows differ from the raw series.
- Before-snapshot exists and round-trips: replaying it would restore the old values.
- `uv run pytest`

#### Manual verification:
- Portfolio history right-edge unchanged (BAC's position is closed; today's value must
  not move).

## Phase 4: Contamination report and the on-demand path

### Changes required

- Extend the new script with `--report-contaminated`: cross-references
  `company_daily_stats` against `d_pl_txt` fractional volumes to list, exactly, which
  tickers still carry adjusted history. Replaces the tick heuristic, which undercounts.
- `docs/` note (or the script docstring) recording the one-ticker repair recipe: the
  stooq URL with `o=1111111`, the browser requirement, the `--apply` invocation.

### Success criteria

#### Automated verification:
- `uv run pytest`
- `uv run ruff check .`

#### Manual verification:
- The report lists 45 tickers and does not list BAC or MCR.

## Progress

### Phase 1: Raw stooq CSV reader
#### Automated
- [ ] 1.1 `uv run pytest tests/test_stooq_raw.py`
- [ ] 1.2 `uv run ruff check src/stooq_raw.py tests/test_stooq_raw.py`
#### Manual
- [ ] 1.3 Parsing `bac_d.csv` yields 574 rows, all closes tick-aligned

### Phase 2: Correction pass for NewConnect
#### Automated
- [ ] 2.1 `uv run pytest tests/test_correct_newconnect_closes.py`
- [ ] 2.2 `uv run pytest` — full suite green
- [ ] 2.3 `uv run ruff check .`
#### Manual
- [ ] 2.4 Dry run reports 208 rows to change and writes nothing

### Phase 3: Execute the repair
#### Automated
- [ ] 3.1 Post-repair query: zero BAC rows differ from the raw series
- [ ] 3.2 Before-snapshot exists and round-trips
- [ ] 3.3 `uv run pytest`
#### Manual
- [ ] 3.4 Portfolio history right-edge unchanged

### Phase 4: Contamination report and the on-demand path
#### Automated
- [ ] 4.1 `uv run pytest`
- [ ] 4.2 `uv run ruff check .`
#### Manual
- [ ] 4.3 Report lists 45 tickers, excludes BAC and MCR
