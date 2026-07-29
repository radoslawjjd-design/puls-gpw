<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Official GPW close as the source for `company_daily_stats`

- **Plan**: `context/changes/official-close-source/plan.md`
- **Scope**: Phases 1–8 (full plan)
- **Date**: 2026-07-28
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 6 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Success criteria

| check | result |
|---|---|
| `uv run pytest --ignore=tests/e2e` | 684 passed |
| `uv run ruff check .` | clean |
| `uv run tach check` | OK |
| duplicate `(ticker, snapshot_date)` keys in window | 0 |
| rows stamped `source='archive'` | 47 834 of 241 277 |
| Progress | 57 of 58 — only 8.9 pending, which needs the deploy |

Plan Adherence was verified item by item across all eight phases: all four schema
edit sites, structural colspan mapping with no positional fallback, no
`WHEN NOT MATCHED` branch in the correction MERGE, `kurs_odn` never reaching
`kurs_zamkniecia`, the ambiguity gate in the corrective script, weekends judged
without probing. Every "What We're NOT Doing" boundary holds. The unplanned files
(`src/polish_numbers.py`, the `ruff` chore commit, `tests/test_seed_companies.py`)
are justified — the extraction is byte-identical and explicitly permitted by the
plan, and "`ruff check .` passes" was unmeetable against a 33-finding baseline.

## Findings

### F1 — Calendar reader did not get the NULL-close treatment the positions view got

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `db/bigquery.py:415-431`
- **Detail**: Phase 3 added `AND kurs_zamkniecia IS NOT NULL` to `latest_stats` and
  `latest_etf` (`db/bigquery.py:865,876`) because the official feed reports a
  no-trade session honestly where bankier always published a last known number.
  `get_portfolio_calendar_data` was left untouched and scores a NULL close as zero
  value: `SUM(CASE WHEN close_price IS NOT NULL THEN shares * close_price ELSE 0 END)`.
  Measured on the live feed 2026-07-28: **100 of 332 NewConnect rows (30.1%) carry
  no close**, and the job writes them like any other row. GPW main was 0 of 372 and
  none of the 14 held instruments is affected, so this is not live today — but the
  first day a held position does not trade, that calendar day's value drops by the
  whole position and reads as a real loss. `get_portfolio_history` is safe (NULLs
  filtered before LOCF/BOCF, `db/bigquery.py:540,545`).
- **Fix**: Exclude NULL-close rows from `daily_prices`, or carry the last known
  close forward there the way the history query does.
  - Strength: Closes the same class the positions fix closed, on the one remaining
    reader that renders value per day.
  - Tradeoff: Excluding rows also shrinks `total_positions`, so the honest
    "20 of 21 priced" indicator would disappear unless the counter is kept separate.
  - Confidence: HIGH — the premise is measured, and the sibling fix is in this diff.
  - Blind spot: Which of the two shapes the UI should show has not been decided.
- **Decision**: FIXED — LOCF added to `daily_prices` mirroring `get_portfolio_history`;
  10-day lookback scanned so the 1st of the month has a predecessor, clipped back to
  `>= @month_start`. Verified on real BigQuery: 20 days in 2026-07, 22 in 2026-06,
  21/21 coverage, no lookback rows leaked, values unchanged.

### F2 — Self-heal's name→ticker map lacks the ambiguity gate the corrective script enforces

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `company_stats_main.py:193-197`
- **Detail**: `ticker_of = {stats["company_name"]: ticker for ticker, (stats, _) in official.items() ...}`
  — a plain dict comprehension where the last writer wins. `official` merges GPW
  then NewConnect, so a name claimed by two tickers silently resolves to the NC one,
  and since the archive is GPW-main-only the GPW close would be written against the
  NC ticker. `build_name_map` (`scripts/correct_official_closes.py:99-113`) rejects
  contested names outright and is tested
  (`test_name_map_drops_a_name_two_tickers_claim`), on the stated grounds that a
  mis-mapped name is the failure class this change exists to remove. Measured
  2026-07-28: **0 collisions across 704 names**, so this is defence against a future
  listing, not a live defect.
- **Fix**: Reuse the gated map construction in the self-heal instead of the bare
  comprehension.
- **Decision**: FIXED — contested names are now dropped and counted in a warning
  (`company_stats_main.py`), with `test_self_heal_refuses_a_name_two_tickers_claim`.
  Break-verified: against the pre-fix comprehension the test fails, because the
  self-heal writes the GPW-main close against the NewConnect ticker.

### F3 — The corrective script writes 19 months of production data from a bare invocation

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `scripts/correct_official_closes.py:262-271`
- **Detail**: `--dry-run` is opt-in, so `uv run python scripts/correct_official_closes.py`
  with no arguments applies the full correction with no confirmation. The sibling
  `scripts/backfill_historical_closes.py` refuses to start without an explicit input
  flag. The script stays in the repo after this change ships.
- **Fix**: Require an explicit `--apply` for any write (or make `--dry-run` the default).
- **Decision**: FIXED — reporting is now the default and `--apply` gates every write;
  `--dry-run` kept as a no-op so old commands still work. Covered by
  `test_a_bare_run_reports_without_writing` / `test_apply_performs_the_write`.

### F4 — A missing archive percentage would blank a stored one

- **Severity**: 👁 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `scripts/correct_official_closes.py:160-173`, `company_stats_main.py:213-229`
- **Detail**: `pct_agrees = pct is None or (...)` treats an absent percentage as
  agreement, so when the close disagrees and the archive leaves `Zmiana kursu %`
  blank the row is still emitted with `zmiana_procentowa=None` and
  `zmiana_kwotowa=None` — and both are in `_CLOSE_CORRECTION_COLUMNS`, so the MERGE
  writes NULL over good values. The mirror case (`close is None → continue`) is
  guarded and tested; this one is neither. Measured: **0 of 157 899 archive rows
  across 409 cached sessions** carry a close without a percentage, and the
  production run nulled **0 of 241 277** rows. Latent, with no demonstrated trigger.
- **Fix**: Emit the percentage keys only when `pct is not None`.
- **Decision**: FIXED — omitting the keys would not have helped (the MERGE assigns all
  four columns unconditionally, so an absent key still writes NULL), so both call
  sites now decline to correct a row the archive left without a percentage.
  Covered by `test_a_close_without_a_percentage_is_left_alone`.

### F5 — `--cache-dir` defaults to no cache at all

- **Severity**: 👁 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `scripts/correct_official_closes.py:266,282`
- **Detail**: The plan describes the disk cache as behaviour ("never re-fetch a
  cached date", criterion 7.9 "a second dry-run performs no archive fetches"), but
  the flag defaults to `None`. An operator who omits it re-fetches ~390 pages and an
  interrupted run has no resume. The production run passed an explicit directory.
- **Fix**: Default `--cache-dir` to a repo-local path such as `.cache/gpw-archive`.
- **Decision**: FIXED — defaults to `.cache/gpw-archive`, added to `.gitignore`.

### F6 — Cache files are trusted unconditionally and written non-atomically

- **Severity**: 👁 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `scripts/correct_official_closes.py:217-227`
- **Detail**: `write_text` is not atomic, and a cached file is reused without a
  sanity check. An interrupted run, or an HTTP 200 carrying an error page, leaves a
  file that parses to "no sheet" — which both suppresses that date's corrections and
  can report it as a phantom trading day. `src/gpw_archive.py`'s guarantee that a
  failed fetch is never reported as `{}` covers only non-2xx and network errors, not
  a truncated cache file. `scripts/backfill_historical_closes.py:237-250` guards
  exactly this with `classify_response`.
- **Fix**: Write to `.tmp` then `os.replace`, and sanity-check the body before caching.
- **Decision**: FIXED — `page_looks_complete()` requires the page marker and a closing
  tag (invariant measured over all 409 cached pages, sessions and non-sessions alike);
  an incomplete fetch raises into the consecutive-failure counter instead of being
  cached, a truncated cache file is refetched, and the write goes through `.tmp` +
  `replace`. Three tests added.

### F7 — Tests seed a provenance value production never writes

- **Severity**: 👁 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `tests/test_bigquery_close_correction.py:43`, `scripts/test_bq_close_correction.py:88,116,130`
- **Detail**: Fixtures use `source="gpw-archive"` while production writes `"archive"`
  (`company_stats_main.py:47`, `scripts/correct_official_closes.py:60`, also
  duplicated rather than shared). The column is free-text so nothing breaks, but the
  round-trip never exercises the sentinel the audit query `source = 'archive'`
  depends on.
- **Fix**: Use `"archive"` in the fixtures and hoist the literal to one shared constant.
- **Decision**: FIXED — fixtures now seed `"archive"`, and
  `test_the_provenance_sentinel_matches_the_daily_job` pins the two definitions to
  each other so a drift fails a test rather than silently under-reporting the audit
  query. Real-BQ round-trip re-run: all five checks pass, throwaway table dropped.

### F8 — The self-heal re-fetches the archive on all 18 daily ticks

- **Severity**: 👁 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `company_stats_main.py:287-290`
- **Detail**: On an ex-dividend or split date the `kurs_odn` divergence is legitimate
  and never resolves, so the ~280 KB archive page is fetched and re-confirmed on
  every scheduler tick — against a site this codebase documents as resetting
  connections under load. The plan measured ~50 permanently unresolved tickers
  (47 NewConnect), which guarantees this fires daily.
- **Fix**: Short-circuit after the first confirmation of the day, or run the
  self-heal only on the first tick.
- **Decision**: DEFERRED — follow-up ticket. Changing the job's control flow is
  better done after the deploy, with real logs to size the actual fetch volume.

### F9 — The correction MERGE duplicates `_merge_insert_only` almost line for line

- **Severity**: 👁 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: `db/bigquery.py:2877-2947`
- **Detail**: Temp table, explicit schema, `WRITE_TRUNCATE` + `CREATE_IF_NEEDED`,
  24 h expiry, `QUALIFY` dedup, `errors` → `BigQueryError`, `num_dml_affected_rows`
  return and cleanup in `finally` are reproduced verbatim; only the MERGE tail
  differs. `_merge_insert_only` is already parameterised by
  `(fn_name, table_name, schema, columns)`.
- **Fix**: Fold both into one helper parameterised by matched/insert column sets.
- **Decision**: DEFERRED — follow-up ticket. The shared primitive is also the ETF
  write path, so the refactor does not belong at the end of this change.
