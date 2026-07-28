# Follow-ups from the PUL-98 implementation review (2026-07-29)

Both were triaged as DEFERRED, not skipped — they are real, just not this change's work.

## 1. Self-heal re-fetches the archive on every scheduler tick

**From**: F8 · `company_stats_main.py` (the `_self_heal_previous_session` call site)

On an ex-dividend or split date the `kurs_odn` divergence is legitimate and never
resolves, so the ~280 KB archive page is fetched and re-confirmed on all ~18 daily
ticks — against a site this codebase documents as resetting connections under load.
The plan measured ~50 permanently unresolved tickers (47 of them NewConnect, which
the GPW-main archive cannot serve by construction), so this fires every day, not
only on corporate-action dates.

**Shape of the fix**: short-circuit after the first confirmation of the day, or run
the self-heal only on the first tick.

**Why deferred**: it changes the job's control flow, and the deploy has not happened
yet. Real logs will size the actual fetch volume and show whether the confirmed-count
path behaves as measured on the throwaway copy.

## 2. `merge_company_daily_stats_close_correction` duplicates `_merge_insert_only`

**From**: F9 · `db/bigquery.py`

Temp table, explicit schema, `WRITE_TRUNCATE` + `CREATE_IF_NEEDED`, 24 h expiry,
`QUALIFY` dedup, `errors` → `BigQueryError`, `num_dml_affected_rows` return and
cleanup in `finally` are all reproduced; only the MERGE tail differs.
`_merge_insert_only` is already parameterised by `(fn_name, table_name, schema,
columns)`.

**Shape of the fix**: one helper parameterised by matched / insert column sets.

**Why deferred**: `_merge_insert_only` is also the ETF quotes write path, so the
refactor has a blast radius beyond this change and does not belong in its final commit.
