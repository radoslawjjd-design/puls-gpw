# F-03: Structured Logging i Email Alert — Implementation Plan

## Overview

Dodajemy dwie warstwy obserwowalności przed pierwszym produkcyjnym kodem pipeline'u: (1) JSON structured logging kompatybilne z Cloud Logging, (2) email alert do właściciela przy każdym nieobsłużonym wyjątku. Bez tej warstwy pipeline może failować cicho w Cloud Run bez wiedzy właściciela — "cicha awaria jest niedopuszczalna" (PRD NFR).

## Current State Analysis

- `main.py` — tylko `print()`, zero logowania, zero error handling
- `.env.example` — SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL zdefiniowane ✅
- Secret Manager — 6 SMTP secretów jako placeholdery, czekają na prawdziwe wartości ✅
- Cloud Run czyta stdout/stderr automatycznie → Cloud Logging, ale wymaga pola `severity` (nie `levelname`) do prawidłowego mapowania poziomów logów
- Brak `src/` — tworzymy teraz jako home dla modułów pipeline'u (zgodnie z AGENTS.md)
- `python-json-logger` brak w `pyproject.toml` — dodajemy w Phase 1

## Desired End State

`main.py` startuje z JSON logging na stderr, gdzie każdy log jest poprawnym JSON-em z polem `severity` widzialnym w Cloud Logging Console. Każdy nieobsłużony wyjątek loguje traceback i wysyła email alert do właściciela (subject: `[puls-gpw] Pipeline ERROR: ExceptionType`). `PipelineStageError` hierarchy dostępna jako kontrakt dla S-01/S-02/S-03. `scripts/test_alert.py` weryfikuje end-to-end lokalnie.

### Key Discoveries

- Cloud Logging wymaga pola `severity` zamiast `levelname` — `python-json-logger` obsługuje to przez `rename_fields={"levelname": "severity"}` w konstruktorze formattera
- SMTP port 587 → STARTTLS (`.env.example` domyślny); `smtplib.SMTP` + `starttls()`
- Lesson F-02: `load_dotenv()` musi być pierwszym wywołaniem w `main.py` — jest już tam, zachowujemy kolejność
- AGENTS.md: `PipelineStageError` base class + log to stderr + alert email — F-03 realizuje kontrakt

## What We're NOT Doing

- Żadnych alertów per-stage (tylko top-level uncaught exception w main.py)
- Żadnych retry dla emaila alertu (SMTP failure → log + exit(1))
- Żadnych external logging services (Datadog, Sentry) — Cloud Logging wystarcza dla MVP
- `pytest` ani test framework — Phase 3 używa skryptu jak `scripts/test_bq.py`
- Alert przy normalnym zakończeniu bez błędu
- Template emaila z HTML — plain text

## Implementation Approach

Trzy fazy: (1) JSON logging setup — nowa zależność, nowy moduł `src/logging_setup.py`, update `main.py`; (2) Exception hierarchy + email alert — `src/exceptions.py`, `src/notifier.py`, try/except wrapper w `main.py`; (3) Skrypt testowy weryfikujący oba mechanizmy end-to-end.

## Critical Implementation Details

**Cloud Logging severity mapping**: `python-json-logger`'s `JsonFormatter` domyślnie emituje pole `levelname`. Cloud Logging ignoruje `levelname` i szuka pola `severity`. Konfiguracja: `JsonFormatter(..., rename_fields={"levelname": "severity", "asctime": "timestamp"})`. Bez tego wszystkie logi lądują w Cloud Logging z poziomem DEFAULT.

**Kolejność w main.py**: `load_dotenv()` → `configure_logging()` → imports z `db.bigquery` i `src.*` → `main()`. Logger musi być skonfigurowany przed pierwszym użyciem, a dotenv przed wczytaniem env vars przez moduły.

---

## Phase 1: JSON Logging Setup

### Overview

Dodaj `python-json-logger` do zależności, stwórz `src/logging_setup.py` z funkcją `configure_logging()`, zaktualizuj `main.py`.

### Changes Required

#### 1. pyproject.toml — dodaj dependency

**File**: `pyproject.toml`

**Intent**: Dodaj `python-json-logger` do sekcji `[project] dependencies`. Uruchom `uv sync` po edycji.

**Contract**: `"python-json-logger>=3.2"` w `dependencies`. Po `uv sync` plik `uv.lock` aktualizuje się.

#### 2. src/__init__.py — pakiet

**File**: `src/__init__.py`

**Intent**: Pusty plik inicjalizujący pakiet `src`.

**Contract**: Plik pusty.

#### 3. src/logging_setup.py — JSON logger

**File**: `src/logging_setup.py`

**Intent**: Centralny punkt konfiguracji logowania. Jeden wywołujący (`main.py`) konfiguruje root logger raz przy starcie.

**Contract**:
- `configure_logging(level: str = "INFO") -> None` — konfiguruje root logger, nie zwraca wartości
- Handler: `logging.StreamHandler(sys.stderr)` — Cloud Run czyta stderr
- Formatter: `pythonjsonlogger.json.JsonFormatter` z `rename_fields={"levelname": "severity", "asctime": "timestamp"}` i formatem `"%(asctime)s %(levelname)s %(name)s %(message)s"`
- Root logger ustawiony na `level`
- Każdy moduł deklaruje swój logger osobno: `logger = logging.getLogger(__name__)` — zapewnia hierarchiczną nazwę w Cloud Logging (np. `"src.scraper"`, `"__main__"`)

#### 4. main.py — dodaj configure_logging

**File**: `main.py`

**Intent**: Skonfiguruj JSON logging przy starcie pipeline'u, zaraz po `load_dotenv()`. Zamień `print("Hello from test-projekt!")` na `logger.info(...)`.

**Contract**: Import `configure_logging` z `src.logging_setup` — wywołany przed innymi importami z `src.*` i `db.*`. Następnie `logger = logging.getLogger(__name__)`. `print()` zastąpiony przez `logger.info("Pipeline started")`.

### Success Criteria

#### Automated Verification

- `uv sync` kończy się kodem 0
- `uv run python -c "from src.logging_setup import configure_logging; print('OK')"` kończy się kodem 0
- `uv run python main.py 2>&1 | python -c "import json,sys; [json.loads(l) for l in sys.stdin if l.strip()]; print('JSON OK')"` kończy się kodem 0

#### Manual Verification

- Output `uv run python main.py` na stderr to valid JSON z polami `severity`, `timestamp`, `message`
- Pole `severity` ma wartość `INFO` (nie `levelname: INFO`)

---

## Phase 2: Exception Hierarchy + Email Alert

### Overview

Stwórz `PipelineStageError` hierarchy w `src/exceptions.py`, funkcję `send_alert()` w `src/notifier.py`, owniaj logikę `main()` w try/except.

### Changes Required

#### 1. src/exceptions.py — PipelineStageError hierarchy

**File**: `src/exceptions.py`

**Intent**: Definiuje hierarchię wyjątków pipeline'u jako kontrakt dla przyszłych stage'ów (S-01 → S-04). Każdy stage rzuca odpowiedni podtyp; top-level handler w `main.py` łapie bazową klasę.

**Contract**:
```
PipelineStageError(Exception)       — base, catch-all dla pipeline errors
  ScraperError(PipelineStageError)  — S-01: błąd pobierania/parsowania HTML
  ParserError(PipelineStageError)   — S-02: błąd ekstrakcji PDF/HTML
  AnalysisError(PipelineStageError) — S-03: błąd Gemini lub supervisor gate
  NotificationError(PipelineStageError) — S-04: błąd wysyłki emaila
```
Każda klasa ma docstring. Brak dodatkowej logiki.

#### 2. src/notifier.py — send_alert

**File**: `src/notifier.py`

**Intent**: Wysyła plain-text email alert do właściciela przez SMTP. Jedyna funkcja publiczna: `send_alert(exc)`. Używana przez handler w `main.py`.

**Contract**:
- `send_alert(exc: BaseException) -> None`
- Czyta z `os.environ`: `SMTP_HOST`, `SMTP_PORT` (int), `SMTP_USER`, `SMTP_PASSWORD`, `OWNER_EMAIL`
- Subject: `[puls-gpw] Pipeline ERROR: {type(exc).__name__}`
- Body (plain text): timestamp UTC, exception type + message, pełny traceback (`traceback.format_exc()`)
- Transport: `smtplib.SMTP(host, port)` → `smtp.starttls()` → `smtp.login()` → `smtp.send_message()`
- `MIMEText(body, "plain", "utf-8")`; `From` = SMTP_USER, `To` = OWNER_EMAIL
- Nie łapie wyjątków — propaguje do wywołującego (który loguje i robi `sys.exit(1)`)

#### 3. main.py — try/except wrapper

**File**: `main.py`

**Intent**: Owiniaj cały body `main()` w try/except — każdy nieobsłużony wyjątek loguje error + traceback i triggeruje email alert. Jeśli sam email failuje — loguje i wychodzi z kodem 1.

**Contract**:
```python
try:
    # pipeline logic
except Exception as exc:
    logger.exception("Pipeline failed")
    try:
        send_alert(exc)
        logger.info("Alert email sent")
    except Exception as alert_exc:
        logger.error("Failed to send alert: %s", alert_exc)
    sys.exit(1)
```
Import `send_alert` z `src.notifier`; import `sys`.

### Success Criteria

#### Automated Verification

- `uv run python -c "from src.exceptions import PipelineStageError, ScraperError, ParserError, AnalysisError, NotificationError; print('OK')"` kończy się kodem 0
- `uv run python -c "from src.notifier import send_alert; print('OK')"` kończy się kodem 0
- `uv run python main.py` kończy się kodem 0 (pipeline normalny przebieg)

#### Manual Verification

- Przejrzyj `src/exceptions.py` — 5 klas, każda z docstringiem
- Przejrzyj `src/notifier.py` — `send_alert()` z type annotation i docstringiem

---

## Phase 3: Integration Test Script

### Overview

Skrypt `scripts/test_alert.py` weryfikuje że JSON logging i email alert działają end-to-end lokalnie. Analogiczny do `scripts/test_bq.py` z F-02.

### Changes Required

#### 1. scripts/test_alert.py — skrypt weryfikacyjny

**File**: `scripts/test_alert.py`

**Intent**: Standalone skrypt testujący obie warstwy obserwowalności. Tryb `--dry-run` pomija faktyczne wysłanie emaila (dla CI i środowisk bez SMTP).

**Contract**: Sekwencja kroków:
1. Skonfiguruj JSON logging przez `configure_logging()`
2. Wygeneruj jeden log na każdym poziomie (DEBUG, INFO, WARNING, ERROR) — zweryfikuj JSON output na stderr
3. Stwórz testowy wyjątek (`raise ValueError("test-alert-F03")`) — złap go i sprawdź traceback
4. Jeśli `--dry-run` NIE jest w `sys.argv`: wywołaj `send_alert(exc)` — wypisz potwierdzenie wysłania
5. Jeśli `--dry-run`: wypisz `[dry-run] would send alert email to {OWNER_EMAIL}`

Skrypt dodaje `sys.path.insert(0, root)` + `load_dotenv()` jak `scripts/test_bq.py`.

### Success Criteria

#### Automated Verification

- `uv run python scripts/test_alert.py --dry-run` kończy się kodem 0 i wypisuje `[dry-run] would send alert email to...`

#### Manual Verification

- `uv run python scripts/test_alert.py` (bez `--dry-run`, z wypełnionym `.env`) — właściciel otrzymuje email alert z subject `[puls-gpw] Pipeline ERROR: ValueError`
- Output na stderr to JSON z `severity: ERROR` i `traceback` w body emaila

---

## Testing Strategy

### Manual Testing Steps

1. Wypełnij `.env` prawdziwymi wartościami SMTP (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL)
2. `uv run python main.py 2>&1` — sprawdź JSON output na stderr
3. `uv run python scripts/test_alert.py --dry-run` — weryfikacja bez emaila
4. `uv run python scripts/test_alert.py` — weryfikacja z prawdziwym emailem (sprawdź skrzynkę)
5. Dodaj tymczasowy `raise ValueError("test")` do `main()`, uruchom `uv run python main.py 2>&1` — sprawdź email alert (usuń raise po teście)

## References

- Roadmap F-03: `context/foundation/roadmap.md` §F-03
- Lessons: `context/foundation/lessons.md` — GCP client init + load_dotenv ordering
- AGENTS.md — PipelineStageError kontrakt i error handling convention
- `python-json-logger` docs: https://github.com/madzak/python-json-logger
- Secret Manager secrets: `deploy-plan.md` — 6 SMTP secretów jako placeholdery

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: JSON Logging Setup

#### Automated

- [x] 1.1 `uv sync` kończy się kodem 0 i aktualizuje `uv.lock` — 07d6c34
- [x] 1.2 `uv run python -c "from src.logging_setup import configure_logging; print('OK')"` kończy się kodem 0 — 07d6c34
- [x] 1.3 `uv run python main.py 2>&1 | python -c "import json,sys; [json.loads(l) for l in sys.stdin if l.strip()]; print('JSON OK')"` kończy się kodem 0 — 07d6c34

#### Manual

- [x] 1.4 Output `main.py` zawiera pola `severity` i `timestamp` (nie `levelname`/`asctime`) — 07d6c34

### Phase 2: Exception Hierarchy + Email Alert

#### Automated

- [x] 2.1 `uv run python -c "from src.exceptions import PipelineStageError, ScraperError, ParserError, AnalysisError, NotificationError; print('OK')"` kończy się kodem 0 — da16368
- [x] 2.2 `uv run python -c "from src.notifier import send_alert; print('OK')"` kończy się kodem 0 — da16368
- [x] 2.3 `uv run python main.py` kończy się kodem 0 — da16368

#### Manual

- [x] 2.4 Przejrzyj `src/exceptions.py` — 5 klas z docstringami — da16368
- [x] 2.5 Przejrzyj `src/notifier.py` — `send_alert()` z type annotation i docstringiem — da16368

### Phase 3: Integration Test Script

#### Automated

- [x] 3.1 `uv run python scripts/test_alert.py --dry-run` kończy się kodem 0 i wypisuje `[dry-run] would send alert email to...`

#### Manual

- [x] 3.2 `uv run python scripts/test_alert.py` (z wypełnionym `.env`) — email received z subject `[puls-gpw] Pipeline ERROR: ValueError`
- [x] 3.3 JSON output na stderr zawiera `severity: ERROR` dla log.error() wywołań
