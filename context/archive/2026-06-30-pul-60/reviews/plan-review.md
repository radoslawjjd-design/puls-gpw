<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Performance — sub-second load for announcements, watchlist, portfolio, treemap and calendar

- **Plan**: context/changes/pul-60/plan.md
- **Mode**: Deep
- **Date**: 2026-06-30
- **Verdict**: REVISE → SOUND (po triage)
- **Findings**: 0 critical, 2 warnings, 1 observation (+ 2 critical plan-completeness fixed inline)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | WARNING |

## Grounding

5/5 paths ✓ (`src/api.py`, `db/bigquery.py`, `static/index.html`, `src/portfolio_calendar.py`, `scripts/`), 4/4 symbols ✓ (`_AC_CACHE`, `list_user_portfolio_positions`, `_portfoliosFetched`, `_build_filter_clauses`), brief↔plan ✓

## Findings

### F1 — Progress Phase 2: brak metryki TTFB + brak czeku kalendarza

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Progress → Phase 2 Automated/Manual
- **Detail**: Progress 2.2 był "SQL string assert" (automated) — brak metryki wydajnościowej wymaganej przez Success Criteria. Manual nie miał checkboxa dla "Dane kalendarza identyczne przed i po" mimo że jest w Phase 2 body.
- **Fix**: Zmienić 2.2 na "X-Process-Time dla GET /api/portfolio/positions < 1500 ms"; dodać 2.5 (Manual) dla kalendarza; dodać 2.6 (Automated) dla SQL string (przeniesione).
- **Decision**: FIXED — zastosowano w plan.md Progress Phase 2

### F2 — _watchlistFetched guard: brak kroku 'set to true'

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 6 → Changes Required #1 Contract
- **Detail**: Plan opisuje reset do `false` przy mutacji, ale nie precyzuje że `fetchWatchlistTickers()` musi ustawić `_watchlistFetched = true` WEWNĄTRZ funkcji po udanym fetchu (jak `_portfoliosFetched:2110`). Bez tego guard nigdy nie blokuje re-fetchu.
- **Fix**: Dodać do Kontraktu Phase 6 punkt 1: fetch function ustawia `true` po sukcesie; add/remove resetują do `false` PRZED wywołaniem fetch.
- **Decision**: FIXED — zastosowano w plan.md Phase 6 Changes #1 Contract

### F3 — asyncio nie importowany w src/api.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4 → Changes Required #1 Contract
- **Detail**: grep potwierdził brak `import asyncio` w `src/api.py`. Phase 4 używa `asyncio.to_thread()` + `asyncio.gather()` — bez importu → `NameError` przy runtime.
- **Fix**: Dodać do Phase 4 Contract explicit note że `import asyncio` brakuje i musi być dodane.
- **Decision**: FIXED — zastosowano w plan.md Phase 4 Contract

### F4 — Migration Notes: fazy 'w dowolnej kolejności' — nieprawdziwe

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: plan-brief.md Prerequisites
- **Detail**: plan-brief.md pisał "Fazy 1–4 mogą być wdrożone w dowolnej kolejności" — fazy 2–5 mają Manual Verification z `X-Process-Time`, który nie istnieje bez Fazy 1. Implementer bez Fazy 1 nie może zmierzyć poprawy.
- **Fix**: Zmienić Prerequisites na "Faza 1 musi być wdrożona pierwsza; Fazy 2–6 są niezależne od siebie."
- **Decision**: FIXED — zastosowano w plan-brief.md

### F5 — Migration script: autodetekcja typu published_at nie opisana

- **Severity**: 👁️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 5 → Changes Required #1 Contract
- **Detail**: Plan wspominał "skrypt weryfikuje typ" ale nie podawał metody. Konkretna metoda: `client.get_table(ref).schema` → `field.field_type`.
- **Fix**: Dodać konkretny snippet autodetekcji do Contract Phase 5.
- **Decision**: FIXED — zastosowano w plan.md Phase 5 Contract
