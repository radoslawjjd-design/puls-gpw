<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: F-03 Structured Logging i Email Alert

- **Plan**: context/changes/observability-baseline/plan.md
- **Scope**: All Phases (1–3 of 3)
- **Date**: 2026-06-04
- **Verdict**: APPROVED (all findings fixed during triage)
- **Findings**: 0 critical, 4 warnings, 2 observations — all fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING → FIXED |
| Scope Discipline | PASS |
| Safety & Quality | WARNING → FIXED |
| Architecture | PASS |
| Pattern Consistency | WARNING → FIXED |
| Success Criteria | PASS |

## Findings

### F1 — configure_logging() wywołane po importach src/db

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: main.py:8–12
- **Detail**: Plan wymaga configure_logging() PRZED importami src.* i db.*. Aktualny kod: load_dotenv() → import db.bigquery → import src.* → configure_logging(). Żaden moduł nie loguje przy imporcie teraz, ale kontrakt był złamany.
- **Fix**: Przeniesiono import i wywołanie configure_logging() bezpośrednio po load_dotenv(), przed pozostałymi importami.
- **Decision**: FIXED

### F2 — smtplib.SMTP() bez timeout

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/notifier.py:34
- **Detail**: Brak timeout powoduje nieskończone blokowanie połączenia przy nieosiągalnym SMTP. W Cloud Run zużywa cały timeout joba.
- **Fix**: Dodano `timeout=10` → `smtplib.SMTP(host, port, timeout=10)`.
- **Decision**: FIXED

### F3 — smtp.starttls() bez ssl.SSLContext

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/notifier.py:35
- **Detail**: starttls() bez jawnego kontekstu nie wymusza weryfikacji certyfikatu — możliwy downgrade do plaintext. App Password Gmail mógł wyciec.
- **Fix A ⭐ Applied**: Dodano `import ssl` + `smtp.starttls(context=ssl.create_default_context())`. Port 587 bez zmian.
- **Decision**: FIXED via Fix A

### F4 — send_alert() w test_alert.py bez własnego try/except

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: scripts/test_alert.py:45–46
- **Detail**: Wyjątek z send_alert() propagował się z zewnętrznego except ValueError, mieszając tracebacki. main.py zawiera wzorzec z zagnieżdżonym try/except.
- **Fix**: Opakowano send_alert(exc) we własny try/except z `print([ERROR]...)` i `sys.exit(1)`.
- **Decision**: FIXED

### F5 — configure_logging() nie waliduje stringa level

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/logging_setup.py:26
- **Detail**: Nieznany string (np. 'VERBOSE') był cicho traktowany jako poziom 0 (NOTSET).
- **Fix**: Dodano guard: `getLevelName(level.upper())` + `raise ValueError` dla nieznanych wartości.
- **Decision**: FIXED

### F6 — test_alert.py bez asercji (vs. test_bq.py)

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: scripts/test_alert.py (overall)
- **Detail**: test_bq.py używa assert. test_alert.py polegał tylko na braku wyjątku.
- **Fix**: Dodano StringIO capture handler + asercje na pola `severity`, `timestamp`, `message` w każdej linii JSON.
- **Decision**: FIXED
