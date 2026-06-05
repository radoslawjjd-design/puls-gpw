<!-- PLAN-REVIEW-REPORT -->
# Plan Review: S-02 Content Parser (PDF + HTML)

- **Plan**: `context/changes/content-parser/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-06
- **Verdict**: SOUND (po triage wszystkich 5 findings)
- **Findings**: 1 critical · 4 warnings · 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL → PASS po F1+F3 |
| Plan Completeness | WARNING → PASS po F2+F4+F5 |

## Grounding

6/6 paths ✓, 5/5 symbols ✓, brief↔plan ✓
pypdf>=4.0 już w pyproject.toml:13 — zmiana niepotrzebna.

## Findings

### F1 — get() raises ScraperError, pseudokod zakładał None-return

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2 — pseudokod parse_announcement(), krok 1
- **Detail**: get() w http_client.py:56 rzuca ScraperError — nigdy nie zwraca None. Pseudokod sugerował None-check zamiast try/except. Bez poprawki ScraperError propagowałby do main.py → alert → pipeline stops.
- **Fix**: Pseudokod krok 1 zmieniony na try/except ScraperError z early return ParsedContent(None).
- **Decision**: FIXED

### F2 — _extract_html_fallback: logika br-bloków nieimplementowalna dosłownie

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Completeness
- **Location**: Phase 2 — opis helpera _extract_html_fallback
- **Detail**: `<br>` to element void — get_text() na nim zwraca pusty string. Brak precedensu w codebase. Implementer ryzykowałby fallback zwracający zawsze None.
- **Fix**: Opis helpera zastąpiony konkretnym podejściem: replace_with("§BR§") + split + segments[1].
- **Decision**: FIXED

### F3 — pypdf exceptions nie złapane w _extract_pdf_text

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 — opis helpera _extract_pdf_text
- **Detail**: PdfReadError, ValueError, itp. dla uszkodzonych PDF-ów łamały kontrakt "Never raises" parse_announcement().
- **Fix**: Opis _extract_pdf_text zaktualizowany — kontrakt try/except Exception → return "" dodany explicite.
- **Decision**: FIXED

### F4 — Progress Phase 3: brakował checkbox dla pytest --tb=short

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: ## Progress → Phase 3: Automated
- **Detail**: 3 automated criteria w fazie, tylko 2 checkboxy w Progress. Brakował 3.X dla pytest --tb=short -q.
- **Fix**: Dodany checkbox 3.2, stare 3.2-3.6 przenumerowane na 3.3-3.7.
- **Decision**: FIXED

### F5 — Diagnostic log obiecany w Success Criteria, nieobecny w pseudokodzie

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 — Manual Verification 2.6 vs pseudokod
- **Detail**: Success criteria 2.6 obiecywał log "Parser: seauid2|pdf|html|none for <url>" ale pseudokod nie zawierał logger.info().
- **Fix**: logger.info("Parser: %s for %s", source, url) dodany przy każdym return w pseudokodzie.
- **Decision**: FIXED
