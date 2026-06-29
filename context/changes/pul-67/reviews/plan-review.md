<!-- PLAN-REVIEW-REPORT -->
# Plan Review: ETF/ETC/ETN Quotes Ingestion and Portfolio Integration

- **Plan**: `context/changes/pul-67/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-29
- **Verdict**: SOUND (po triage)
- **Findings**: 1 critical | 3 warnings | 2 observations

## Verdicts

| Dimension | Verdict |
|---|---|
| End-State Alignment | WARNING |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

7/7 paths ✓, 4/4 symbols ✓, brief↔plan ✓

## Findings

### F1 — ensure_schema_current() nie tworzy tabel — Phase 3 entrypoint no-op

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 (#4) + Phase 3 (Flow)
- **Detail**: `ensure_schema_current()` (db/bigquery.py:160–163) robi early return gdy tabela nie istnieje. Plan definiował tylko `ensure_*_schema_current()` bez `create_*_table_if_not_exists()`. Wzorzec company_stats_main.py:30–31 wymaga obu.
- **Fix Applied**: Dodano `create_etf_instruments_table_if_not_exists()` i `create_etf_quotes_table_if_not_exists()` do Phase 1. Phase 3 flow: create_*() przed ensure_*() dla obu tabel.
- **Decision**: FIXED (Phase 1 #4 + Phase 3 Flow steps 1–4)

### F2 — Szerszy blast radius list_distinct_tickers() — watchlist + 10+ test mocków

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 (#8) + Phase 6 testing
- **Detail**: `list_distinct_tickers()` wywoływana w 3 miejscach produkcyjnych (autocomplete, watchlist api.py:321, portfolio). 10+ mocków w test_api.py i test_bigquery.py:668 docstring nieaktualne.
- **Fix Applied**: Phase 1 #8 Intent rozszerzony o wzmiankę watchlist side effect. Phase 6 Automated Verification: nowe kryteria dla mocków i docstringu.
- **Decision**: FIXED

### F3 — Sygnatury merge_etf_*() niezgodne z wzorcem list[dict]

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision
- **Dimension**: Architectural Fitness
- **Location**: Phase 1 (#5, #6)
- **Detail**: Plan miał `dict[str, dict]`; wzorzec merge_company_daily_stats używa `list[dict]`.
- **Fix Applied**: Zmieniono sygnatury na `list[dict]` w Phase 1 #6 i #7.
- **Decision**: FIXED

### F4 — Bootstrap: etf_instruments pusta do pierwszego uruchomienia joba

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Migration Notes
- **Detail**: Po deploy etf_instruments jest pusta → HTTP 422 dla ETF tickerów dopóki job nie uruchomi się raz.
- **Fix Applied**: Dodano "Bootstrap po deploy" notę do Migration Notes z instrukcją manualnego uruchomienia joba.
- **Decision**: FIXED (Fix A)

### F5 — dry-run flag nie istnieje w codebase

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 3 Automated Verification
- **Detail**: "(jeśli flaga dry-run istnieje)" — conditional criterion, żaden entrypoint tej flagi nie ma.
- **Fix Applied**: Usunięto wzmiankę o --dry-run z Phase 3 automated verification.
- **Decision**: FIXED

### F6 — Nazwa instrumentu = ticker na GPW

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: End-State Alignment
- **Location**: Phase 6
- **Detail**: GPW "Nazwa pełna" = ticker string. Cross-fill pokaże ticker w obu polach.
- **Fix Applied**: Dodano notę w Phase 6 #1 Contract że name = ticker string i jest to oczekiwane.
- **Decision**: FIXED
