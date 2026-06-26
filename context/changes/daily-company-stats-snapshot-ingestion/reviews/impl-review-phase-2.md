<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Daily company-stats snapshot ingestion — Phase 2

- **Plan**: context/changes/daily-company-stats-snapshot-ingestion/plan.md
- **Scope**: Phase 2 of 4
- **Date**: 2026-06-26
- **Verdict**: APPROVED (post-triage)
- **Findings**: 1 critical  1 warning  1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS (fixed) |
| Architecture | PASS |
| Pattern Consistency | PASS (fixed) |
| Success Criteria | PASS |

## Findings

### F1 — resp.json() może crashować cały job

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/bankier_metrics.py:55
- **Detail**: `resp.json()` wywoływane bez obsługi wyjątków. JSONDecodeError niezłapany propaguje do outer catastrophic handler → `sys.exit(1)` dla całego joba zamiast skip per-ticker. Narusza kontrakt "jeden ticker nie zatrzymuje reszty".
- **Fix**: Owinąć `resp.json()` w `try/except (ValueError, AttributeError)` → `logger.warning + return None`. Dodano też `isinstance(data, dict)` guard przed `data.get("profile_data")`.
- **Decision**: FIXED

### F2 — Brak testu dla JSONDecodeError

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_bankier_metrics.py
- **Detail**: Po naprawie F1 brak testu weryfikującego że `ValueError` z `resp.json()` → `None`.
- **Fix**: Dodano `test_fetch_daily_stats_json_decode_error_returns_none` z `side_effect=ValueError`.
- **Decision**: FIXED

### F3 — _BANKIER_API_URL przekracza ~100 znaków

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/bankier_metrics.py:9
- **Detail**: Linia ~115 znaków. Ruff przeszedł (konfiguracja to akceptuje) — nie bloker.
- **Decision**: SKIPPED
