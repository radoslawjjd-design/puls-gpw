<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Compute calendar and value-history from holdings as of each day

- **Plan**: context/changes/calendar-holdings-as-of-date/plan.md
- **Scope**: Phases 3–4 of 5
- **Date**: 2026-07-31
- **Verdict**: APPROVED (after F1 fixed in-review)
- **Findings**: 0 critical, 3 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS (after F1 fix) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Grounding

Diff scope `3409b9c..HEAD`: `db/bigquery.py` (+246), `scripts/test_bq_portfolio_time_dimension.py` (new, 517), `src/api.py` (+6), `static/index.html` (+6), `tests/` (+167). Every file in the diff is named in the plan; no file the plan names is missing.

Automated criteria, all re-run on this branch:

| Command | Result |
|---|---|
| `uv run pytest` | 943 passed |
| `uv run tach check` | `[OK] All modules validated!` |
| `uv run python scripts/test_bq_portfolio_time_dimension.py` | 15/15 checks OK, throwaway tables dropped |
| round-trip residual report | 4 seeded residuals, nothing else |
| round-trip wall-clock | calendar 0.4 s, history 1.3 s (budget 20 s) |

Manual criteria were verified against production BigQuery during phases 3–4: both Główny wallets start 2025-01-29 (377 pts), both IKZE 2025-07-10 (265 pts), right edge equals "Mój portfel" to the grosz on all five wallets with data (`delta = +0.00`), "Wszystkie" sums exactly (44 379,43 + 26 972,77 = 71 352,20), prod timings calendar 1,16 s / chart 1,50–1,90 s.

## Findings

### F1 — The phase-4 diagnostic is inert in production

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:662, db/bigquery.py:978
- **Detail**: Phase 4 §2's stated intent is *"żeby nieoczekiwana reszta nie została zauważona dopiero przez usera"* — the point is that **we** notice. The count is emitted with `logger.debug`, and `api_main.py:7` calls `configure_logging()` with its default `"INFO"`, so DEBUG records never leave the process in Cloud Run. The implementation matches the plan's letter (*"loguje liczbę na poziomie DEBUG"*) and defeats its intent. Not a regression introduced here — the pre-existing timing logs on the same lines have always been inert — but this phase exists specifically to make divergence visible.
- **Fix**: Add `_log_unexplained_holdings`, called from both functions: silent on zero (the normal case, so no log-volume cost), `logger.info` with user and portfolio when non-zero. The DEBUG timing line is untouched.
- **Decision**: FIXED — `_log_unexplained_holdings` added in `db/bigquery.py`, covered by `test_an_unexplained_holding_is_reported_at_a_level_production_actually_emits` (asserts both the non-zero emit and the zero-stays-silent branch).

### F2 — `data_from` ships as a top-level key, not a `notes` entry

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/api.py:303-308, plan.md Phase 3 §2
- **Detail**: The plan's contract says *"Nowy wpis w istniejącej tablicy `notes`"* and *"koperta `{series, notes, excluded}` **bez zmian**"*. The implementation adds a top-level `data_from: str | None = None` instead. The reason is sound: `PortfolioHistoryNote` requires `ticker: str`, and this fact is per-portfolio, not per-ticker — forcing it into `notes` would need either a dummy ticker or a loosened model for every note. The deviation is additive and backward-compatible (`extra="ignore"`, a `None` default, and `data.get("data_from")` at the endpoint, so a pre-change db result still serialises), and the frontend renders it in the same `(i)` popover, so nothing shifts for the user. It is still a documented contract clause that was not followed.
- **Fix**: Record it as an addendum in the plan rather than reshaping working code around a contract written before the model constraint was known.
- **Decision**: FIXED — addendum added under plan.md Phase 3 §2.

### F3 — `ops_basis` is an addition inside a documented "not doing"

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: db/bigquery.py (`ops_basis` CTE in `get_portfolio_history`)
- **Detail**: Both the plan (*"`avg_buy_price` zostaje bez zmian (poza zakresem)"*) and `research.md:31-32` put cost basis out of scope. Phase 3 nonetheless adds `ops_basis`, a weighted average of buy prices from `user_broker_operations`, used when a ticker has no position row left. The justification holds: phase 3's wider universe admits tickers sold to zero whose row the import deletes, and without a basis their entire value reads as profit that unwinds on the sale date — a phantom hump on the P/L curve. It is the same *class* of number as `avg_buy_price` (a time-blind weighted average), so the accepted approximation is unchanged; only the set of tickers that have one changes. The round-trip harness guards it ("every valued ticker carries a cost basis, including the backward-filled one"). But it is a behaviour change in an area this change declared closed, and it leaves **two** sources of cost basis where there was one.
- **Fix**: Keep the code — removing it would ship a known phantom profit — and document it, including as an explicit input to the cost-basis follow-up ticket, which must now replace both sources rather than one.
- **Decision**: FIXED — documented in `change.md` under "Dopisane w Fazie 3 poza planem".

## Notes for Phase 5

Phase 5's criteria are unchanged and still pending a production deploy. The residual report's four seeded entries in the harness are not the same set as production's three known cases (2× `_CASH`, 1× hand-entered) — the harness seeds a fourth on purpose (`rt-pf-oversell / DDD`, a sell whose matching buy predates the export window) to exercise the no-negative-shares guard.
