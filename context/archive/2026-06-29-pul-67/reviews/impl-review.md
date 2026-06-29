<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: ETF/ETC/ETN Quotes Ingestion (PUL-67)

- **Plan**: context/changes/pul-67/plan.md
- **Scope**: All phases (1–7)
- **Date**: 2026-06-29
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings (both fixed), 4 observations (3 fixed, 1 skipped)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS (fixed) |
| Architecture | PASS |
| Pattern Consistency | PASS (fixed) |
| Success Criteria | PASS |

## Automated Success Criteria

- `uv run python -c "from db.bigquery import merge_etf_instruments, merge_etf_quotes, list_distinct_tickers; print('OK')"` → OK ✅
- `uv run ruff check src/gpw_etf_metrics.py etf_quotes_main.py` → All checks passed ✅
- `uv run pytest --tb=short -q` → 468 passed ✅

## Findings

### F1 — fetch_etf_page() nie łapie ScraperError

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality / Pattern Consistency
- **Location**: src/gpw_etf_metrics.py:65
- **Detail**: get() wywoływany bez try/except; bankier_metrics.py:71 łapie ScraperError i zwraca {} — tu propaguje raw do main().
- **Fix**: Owrap get() w try/except ScraperError, return ({}, []).
- **Decision**: FIXED — src/gpw_etf_metrics.py

### F2 — Brak testu HTTP-failure dla fetch_etf_page()

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: tests/test_gpw_etf_metrics.py
- **Detail**: Brak testu ScraperError path; test_bankier_metrics.py:103 ma odpowiednik.
- **Fix**: Dodaj test_fetch_etf_page_http_failure_returns_empty z side_effect=ScraperError.
- **Decision**: FIXED — tests/test_gpw_etf_metrics.py

### F3 — Typ _AC_CACHE niezgodny z etf-instruments

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: src/api.py:53
- **Detail**: _AC_CACHE: dict[str, tuple[list[str], float]] — etf-instruments przechowuje list[dict].
- **Fix**: Zmień na dict[str, tuple[list, float]].
- **Decision**: FIXED — src/api.py

### F4 — etfRes.status 401 nie triggeruje logout

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: static/index.html (autocomplete init)
- **Detail**: Warunek logout sprawdza tRes/cRes ale nie etfRes.
- **Fix**: Dodaj || etfRes.status === 401.
- **Decision**: FIXED — static/index.html

### F5 — Phase 2 scraper: drift strategii parsowania

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: src/gpw_etf_metrics.py, context/changes/pul-67/plan.md
- **Detail**: Plan: header-text dynamic + _parse_float(). Kod: CSS classes + data-o-value (robustniejsze). Deliberate pivot w TDD.
- **Fix**: Zaktualizuj plan.md notatką o rzeczywistej strategii.
- **Decision**: FIXED — context/changes/pul-67/plan.md (Implementation note dodana)
