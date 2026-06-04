# F-03: Structured Logging i Email Alert — Plan Brief

> Full plan: `context/changes/observability-baseline/plan.md`

## What & Why

Dodajemy dwie warstwy obserwowalności wymagane przez PRD NFR: JSON structured logging kompatybilne z Cloud Logging oraz email alert do właściciela przy każdym nieobsłużonym wyjątku pipeline'u. "Cicha awaria jest niedopuszczalna" — F-03 jest prerequisitem dla S-04 i dla wszelkiego produkcyjnego kodu pipeline'u.

## Starting Point

`main.py` ma tylko `print()` i zero error handling. SMTP credentials już zdefiniowane w `.env.example` i Secret Manager (jako placeholdery czekające na prawdziwe wartości). Brak `src/` — tworzymy teraz.

## Desired End State

`uv run python main.py` emituje JSON logi na stderr z polem `severity` widocznym w Cloud Logging Console. Każdy nieobsłużony wyjątek: (1) loguje error + traceback, (2) wysyła email `[puls-gpw] Pipeline ERROR: ExceptionType` do właściciela, (3) kończy z `exit(1)`. `PipelineStageError` hierarchy gotowa jako kontrakt dla S-01–S-04.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Logging library | stdlib `logging` + `python-json-logger` | Zero nowych zewnętrznych zależności poza tiny, stabilną paczką; Cloud Run czyta stderr bez konfiguracji | Plan |
| Alert trigger | Tylko top-level uncaught exception | Jeden punkt wejścia, zero boilerplate'u w stage-functions; realizuje NFR "cicha awaria" | Plan |
| PipelineStageError now | Tak — `src/exceptions.py` z 5 klasami | AGENTS.md już wskazuje ten kontrakt; F-03 ustawia wzorzec dla S-01–S-04 | Plan |
| Email format | Plain text: exception type + message + traceback | Zawsze działa niezależnie od stage; traceback wystarczy do diagnozy | Plan |
| SMTP failure handling | Log to stderr + exit(1), bez retry | Cloud Run zobaczy non-zero exit code; proste i niezawodne dla MVP | Plan |
| Cloud Logging severity | `rename_fields={"levelname": "severity"}` w JsonFormatter | Cloud Logging ignoruje `levelname`, szuka `severity`; 1 parametr w konstruktorze | Plan |

## Scope

**In scope:**
- `src/logging_setup.py` — `configure_logging()`
- `src/exceptions.py` — `PipelineStageError` + 4 podklasy
- `src/notifier.py` — `send_alert(exc)` przez SMTP/STARTTLS
- `main.py` — JSON logging + try/except wrapper
- `scripts/test_alert.py` — end-to-end weryfikacja

**Out of scope:**
- Alerty per-pipeline-stage (boilerplate; stage'y nie istnieją)
- Retry dla emaila alertu
- External services (Sentry, Datadog)
- pytest / test framework (Moduł 3)
- HTML email template

## Architecture / Approach

```
main.py
  load_dotenv()
  configure_logging()   ← src/logging_setup.py
  try:
    pipeline logic
  except Exception as exc:
    logger.exception(...)
    send_alert(exc)     ← src/notifier.py (smtplib + STARTTLS)
    sys.exit(1)

src/exceptions.py:
  PipelineStageError → ScraperError, ParserError, AnalysisError, NotificationError
```

Cloud Run reads stderr → Cloud Logging. JSON format with `severity` field maps correctly to Cloud Logging log levels.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. JSON Logging Setup | `python-json-logger`, `src/logging_setup.py`, `main.py` emituje JSON | Cloud Logging severity mapping — wymaga `rename_fields` |
| 2. Exception Hierarchy + Alert | `src/exceptions.py`, `src/notifier.py`, `main.py` try/except | SMTP config (credentials muszą być w `.env`) |
| 3. Integration Test Script | `scripts/test_alert.py` z `--dry-run` | Prawdziwy email test wymaga wypełnionych SMTP secrets |

**Prerequisites:** SMTP credentials w `.env` (lokalnie) i Secret Manager (Cloud Run) — placeholdery już istnieją, wymagają prawdziwych wartości przed testem Phase 3 bez `--dry-run`.

**Estimated effort:** ~1 sesja, 3 fazy.

## Open Risks & Assumptions

- SMTP credentials w Secret Manager są nadal placeholderami — Phase 3 manual test wymaga ich wypełnienia
- Jeśli provider SMTP wymaga SSL (port 465) zamiast STARTTLS (port 587) — `src/notifier.py` wymaga drobnej zmiany transportu

## Success Criteria (Summary)

- `uv run python main.py 2>&1` → valid JSON na stderr z `severity: INFO`
- `uv run python scripts/test_alert.py --dry-run` → exit 0
- Właściciel otrzymuje email alert przy force-thrown exception (manual test z prawdziwym SMTP)
