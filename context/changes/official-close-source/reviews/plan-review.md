<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Official GPW close as the source for `company_daily_stats`

- **Plan**: `context/changes/official-close-source/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-27
- **Verdict**: REVISE → **SOUND** after fixes
- **Findings**: 3 critical, 4 warnings, 2 observations (all fixed)

## Verdicts

| Dimension | Verdict | After fixes |
|-----------|---------|-------------|
| End-State Alignment | FAIL | PASS |
| Lean Execution | PASS | PASS |
| Architectural Fitness | PASS | PASS |
| Blind Spots | FAIL | PASS |
| Plan Completeness | WARNING | PASS |

## Grounding

12/12 paths ✓, 5/5 symbols ✓, brief↔plan ✓, Progress↔Phase 8/8 ✓, 0 checkboxes outside `## Progress`

## Findings

### F1 — Self-heal from `Kurs odn.` corrupts closes on ex-dividend and split dates

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: End-State Alignment
- **Location**: Phase 5 (original numbering) — Self-heal
- **Detail**: The plan treated `Kurs odn.` as the previous session's close. It is the *reference
  price*, which GPW adjusts for corporate actions. On an ex-dividend date it equals the previous
  close minus the dividend, so the self-heal would have overwritten a correct official close with an
  adjusted one — reintroducing precisely the dividend-adjustment defect (GH #191) that the corrective
  pass removes. Silent by design, since the plan deliberately does not alert on divergences.
  Measured evidence: over 14 sessions, 41 of 5 230 comparisons diverged by more than 0.3 pp, up to
  11 pp. `ECHO` on 2026-06-24 had a reference price of 4.94 against a previous close of 5.34 — a
  −7.4% corruption of a correct value.
- **Fix A ⭐ Recommended**: `Kurs odn.` becomes a detector only; the archive supplies the value
  - Strength: Keeps the free detector (no extra request on ~99% of days) while the written value
    comes from a corporate-action-proof source that is published the same evening.
  - Tradeoff: The archive reader must move ahead of the self-heal — phase reordering.
  - Confidence: HIGH — both properties measured 2026-07-27.
  - Blind spot: Number of tickers tripping the detector on a heavy ex-dividend day not measured;
    cost is bounded at one fetch per session date regardless.
- **Fix B**: Drop `Kurs odn.`, verify unconditionally against the archive
  - Strength: Simpler — one source, no detector logic.
  - Tradeoff: One archive fetch on each of 18 daily runs, needlessly in ~99% of cases.
  - Confidence: HIGH.
  - Blind spot: Archive behaviour under 18 requests/day unmeasured; gpw.pl already reset a
    connection under load (F7).
- **Decision**: FIXED via Fix A — archive reader promoted to Phase 5, self-heal to Phase 6; plan now
  states "`kurs_odn` is a detector only, never a value source" and adds an ex-dividend test case.

### F2 — No rollback for the destructive correction of ~270 000 production rows

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 7 (original numbering) — Production run
- **Detail**: The corrective MERGE overwrites in place with no backup. PUL-92 chose insert-only
  specifically so a backfill could never destroy data; this plan gives that protection up and put
  nothing in its place. A wrong name mapping would be unrecoverable.
- **Fix**: CTAS snapshot of the correction window (`company_daily_stats_pre_pul98`) before the run,
  with the restore statement recorded alongside the run log.
  - Strength: Seconds and pennies in BigQuery; unconditional escape hatch for the whole operation.
  - Tradeoff: A table to clean up later (dropping it is human-only).
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED — added as step 1 of Phase 8, with a matching manual criterion.

### F3 — Derived change columns recomputed from consecutive closes instead of the archive's own percentage

- **Severity**: ❌ CRITICAL (raised from WARNING after measurement)
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 6 (original numbering) — corrective script
- **Detail**: The plan specified recomputing `zmiana_procentowa` from the ratio of consecutive
  closes. GPW's own percentage is measured against the reference price, which is adjusted for
  corporate actions. Naive differencing is therefore wrong on every ex-dividend and split.
  Measured over 14 sessions: 41 of 5 230 pairs (0.78%) diverged by more than 0.3 pp, reaching 11 pp,
  clustered on 2026-06-23/24 — the dividend season. `ECHO` on 2026-06-24 rose **+0.10% officially
  against −7.40% computed naively**; `LOKUM` **+1.24% against −2.79%**. The calendar renders this
  quantity directly, so the error would have been drawn on screen as a deep-red cell on a day the
  price rose.
- **Fix**: Take `zmiana_procentowa` from the archive's `Zmiana kursu %`; derive
  `zmiana_kwotowa = close − close/(1 + pct/100)`.
  - Strength: Corporate-action-proof, and it removes the "first session has no predecessor" edge
    case entirely.
  - Tradeoff: The percentage is rounded to 2 decimals, giving a small error in the derived amount.
  - Confidence: HIGH — follows from the definition of the reference price and is measured.
  - Blind spot: None significant.
- **Decision**: FIXED — script contract rewritten; a regression test pins the corporate-action case.

### F4 — Phantom non-session rows unaddressed; one already exists in production

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 7 (original numbering) — verification
- **Detail**: No holiday calendar, trading-day check or market-open logic exists anywhere in the
  project; `snapshot_date` comes straight from the wall clock. Production query confirms
  **2026-06-27, a Saturday, carries 739 rows**. Because the trading-day spine is
  `SELECT DISTINCT snapshot_date`, that date is a phantom trading day on the value chart, and the
  calendar counts its `zmiana_kwotowa` a second time. The corrective script would skip it (the
  archive has no Saturday session), leaving it wrong permanently. More will appear — 11 Nov 2026
  falls on a Wednesday.
- **Fix**: The script reports dates where BigQuery holds rows but the archive reports no session;
  deletion stays a separate decision (`delete_company_daily_stats_for_date` already exists).
  - Strength: Detection is free — the script probes every date anyway — without widening scope to
    destructive deletes.
  - Tradeoff: The 2026-06-27 phantom itself remains for a follow-up.
  - Confidence: HIGH — confirmed by production query.
  - Blind spot: Not checked whether similar rows exist before 2026-06-20.
- **Decision**: FIXED — reporting added to the script contract and to Phase 7 manual criteria;
  deletion and a trading-day guard listed under "What We're NOT Doing".

### F5 — `scripts/seed_companies.py` will NULL-clobber the new provenance columns

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1
- **Detail**: The plan said the script "must be updated or explicitly accepted" — an open question
  left in a finished plan. It shares the full-upsert primitive and builds rows by splatting
  `trading_data`, which never contains the new keys, so a run after the daily job would overwrite
  `source` and `kurs_odn` with NULL for every listed ticker — and it swallows the failure in
  `except BigQueryError → logger.warning`, so it would not even exit non-zero.
- **Fix**: Remove the `--with-stats` write path; the script's job is company reconciliation, not
  price authority.
- **Decision**: FIXED — added as change 3 of Phase 1 with a manual criterion.

### F6 — The four column-list edit sites are not enumerated; the `UPDATE SET` omission is silent

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1
- **Detail**: The plan said "add them to the column lists". There are four sites — schema literal,
  MERGE `UPDATE SET`, the parallel INSERT/VALUES lists, and `_merge_insert_only`'s `columns`
  argument — and three fail silently. `UPDATE SET` is the dangerous one: with 18 ticks per day only
  the first takes the INSERT branch, so omitting it would freeze both columns at their 9:01 values.
  No existing test catches this; SQL assertions in the suite are substring-only.
- **Fix**: Enumerate the four sites in the Phase 1 contract and add query-string regression
  assertions.
- **Decision**: FIXED — table of four sites added, plus a dedicated test sub-section and an
  automated criterion.

### F7 — gpw.pl reset the connection at a 0.4 s cadence; the plan specifies no rate

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 6 (original numbering) — corrective script
- **Detail**: The plan said only "politeness delay between fetches". Fetching ~38 sessions at 0.4 s
  produced `ConnectionReset`; the corrective run is ~390 sessions. `src/http_client.py` provides no
  inter-request throttle despite its docstring claiming one.
- **Fix**: Specify ≥1.5 s cadence, one reused HTTP session, retry with increasing backoff, and make
  the disk cache the resume mechanism.
- **Decision**: FIXED — written into both the archive reader (Phase 5) and script (Phase 7)
  contracts, with a manual criterion for a 20-session sequential fetch.

### F8 — The schema set-equality test will break and was not listed

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1
- **Detail**: `tests/test_bigquery.py:1088-1103` compares the column-name set with `==` and is the
  only test that breaks mechanically when a column is added.
- **Fix**: Name it in the Phase 1 contract and extend it to assert `mode == "NULLABLE"`.
- **Decision**: FIXED.

### F9 — A network + BigQuery dry-run was listed under Automated Verification

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 6 (original numbering), criterion 6.4
- **Detail**: "Dry-run over the full range writes zero rows" needs network and credentials, so it
  cannot run in CI, yet it sat beside `uv run pytest`.
- **Fix**: Move it to Manual Verification.
- **Decision**: FIXED — now criterion 7.4 under Manual.

## Notes

F1 and F3 are the same mistake in two places: treating GPW's reference price as an ordinary previous
close. Both are resolved by one rule, now recorded in the plan's Critical Implementation Details —
**the archive is the oracle; arithmetic on closes is not.**

F4 is the only finding confirmed by a direct production query rather than by reading code.
