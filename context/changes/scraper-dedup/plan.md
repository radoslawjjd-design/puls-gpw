# Scraper Bankier.pl + Dedup BigQuery — Implementation Plan

## Overview

Implementacja S-01: scraper listingu Bankier.pl (okno 15 min, max 5 stron), batch dedup via BigQuery i insert nowych ogłoszeń ESPI/EBI. Zastępuje stub `main.py`. Ticker i company są poza zakresem — lądują jako NULL w BQ i zostaną uzupełnione przez S-02 (który i tak fetchuje stronę ogłoszenia).

## Current State Analysis

- `main.py` — stub: tylko `create_table_if_not_exists()` + `logger.info("Pipeline started")`
- `db/bigquery.py` — F-02: `is_processed(url)`, `insert_announcement(url, published_at, title, company, ticker)` gotowe; brak batch dedup
- `src/exceptions.py` — F-03: `ScraperError(PipelineStageError)` już zdefiniowany
- `src/notifier.py` — F-03: `send_alert(exc)` gotowe
- `pyproject.toml` — `httpx>=0.27`, `beautifulsoup4>=4.12`, `html5lib>=1.1` w deps; brak `pytest` i `respx`
- HTML selektory znane z F-01: `.m-quotes-announcements-item`, `.m-quotes-announcements-item__anchor`, `.m-quotes-announcements-item__date`, `.a-quotes-badge .value`
- `oldProjectData/bankier.py` — referencyjna implementacja scrapera (inna logika: filtr po dacie, requests, ticker lookup)

## Desired End State

Po wdrożeniu:
- `python main.py` scrapuje listing Bankier.pl, filtruje do okna 15 min, deduplikuje via BQ batch query, insertuje nowe ogłoszenia i kończy z `exit 0` logując ile nowych znaleziono
- Zero nowych ogłoszeń = INFO log + exit 0 (normalna sytuacja poza sesją giełdową)
- Błąd HTTP Bankier po 3 retry = `ScraperError` → `send_alert()` → `exit 1`
- Błąd BQ insert = fail fast → `send_alert()` → `exit 1`

### Key Discoveries

- `ScraperError` już istnieje w `src/exceptions.py` — nie tworzyć nowego wyjątku
- `_announcement_id(url)` w `db/bigquery.py` (private) oblicza SHA256 URL — scraper potrzebuje tej samej formuły do sprawdzenia dedup in-memory; najlepiej wyeksponować jako publiczną funkcję
- Items na stronie Bankier mogą być interleaved (nie czysto chronologicznie) — stary bankier.py comment: "FIX 2026-04-22: continue zamiast break — nie zakładamy chronologii"; stop-condition opiera się na **min datetime na stronie**, nie na pierwszym elemencie starszym niż cutoff
- `httpx` jest już w deps (`httpx>=0.27`) — nie dodawać `requests`
- `zoneinfo` jest stdlib od Python 3.9+ — nie potrzeba `pytz`
- BQ Python client poprawnie konwertuje timezone-aware datetime do UTC przy zapisie TIMESTAMP

## What We're NOT Doing

- Ticker i company lookup (2-hop HTTP) — scope S-02
- Przetwarzanie treści ogłoszenia (PDF/HTML) — scope S-02
- Testy integracyjne z realnym Bankier.pl / realnym BQ — scope: skrypt manualny jeśli potrzeba
- FastAPI endpoint — pipeline to Cloud Run Job, nie serwer HTTP
- Scheduler (re)aktywacja — wstrzymany do S-04

## Implementation Approach

Trzy warstwy:
1. **BQ** — dodanie `announcement_id_for_url()` (public) + `get_processed_ids_since()` do `db/bigquery.py`
2. **Scraper** — `src/http_client.py` (httpx sync + retry) + `src/scraper.py` (listing parse + dedup filter)
3. **Integracja** — `main.py` zastąpienie stub + testy jednostkowe

## Critical Implementation Details

**Stop-condition paginacji** opiera się na `min(item_dt for item on page)` vs `cutoff`. Jeśli `min_dt < cutoff` — przerywamy paginację po tej stronie (wszystkie kolejne strony będą jeszcze starsze). Przetwarzamy jednak wszystkie itemy na bieżącej stronie, które są `>= cutoff`.

**Intra-run dedup**: po batch prefetch z BQ, dodawaj każde nowe ogłoszenie do `known_ids` (set in-memory) przed przejściem do następnego itemu — chroni przed duplikatami gdy ten sam URL pojawi się na dwóch stronach paginacji.

**Cutoff dla `get_processed_ids_since`**: przekaż `now - timedelta(minutes=30)` (2× okno) zamiast dokładnie 15 min — bufor na edge case przy granicy okna i ewentualne opóźnienie BQ.

---

## Phase 1: BQ — public helpers + batch dedup function

### Overview

Dodaje dwie publiczne funkcje do `db/bigquery.py`: `announcement_id_for_url()` (refactor z private) i `get_processed_ids_since()` (batch dedup). Refactor nie zmienia zachowania istniejącego kodu.

### Changes Required

#### 1. `db/bigquery.py` — publiczny `announcement_id_for_url` + batch dedup

**File**: `db/bigquery.py`

**Intent**: Wyeksponować `_announcement_id` jako publiczną funkcję (zmiana nazwy + wrapper) tak, żeby `src/scraper.py` mógł importować tę samą formułę SHA256 bez duplikowania logiki. Dodać `get_processed_ids_since(cutoff: datetime) -> set[str]` — jeden BQ query zwracający zbiór `announcement_id` dla ogłoszeń z `published_at >= cutoff`.

**Contract**:
```python
def announcement_id_for_url(url: str) -> str:
    """SHA256 hex digest of the announcement URL — stable dedup key."""

def get_processed_ids_since(cutoff: datetime) -> set[str]:
    """Return set of announcement_ids where published_at >= cutoff.

    Caller should pass cutoff = now - 2× scrape_window for safety margin.
    Raises RuntimeError if the BQ query fails.
    """
```

Wewnętrzna `_announcement_id` deleguje do `announcement_id_for_url` (backwards-compat).

#### 2. `pyproject.toml` — dev deps dla testów

**File**: `pyproject.toml`

**Intent**: Dodać `pytest>=8.0` i `respx>=0.21` do `[dependency-groups] dev` — potrzebne do uruchamiania unit testów i mockowania httpx.

### Success Criteria

#### Automated Verification

- `uv sync` kończy bez błędów — nowe dev deps zainstalowane
- `python -c "from db.bigquery import announcement_id_for_url, get_processed_ids_since"` — brak ImportError
- Istniejące wywołania `_announcement_id` w module nadal działają (wrapper)

#### Manual Verification

- `announcement_id_for_url("https://example.com")` zwraca ten sam hash co stare `_announcement_id("https://example.com")`

**Implementation Note**: Po tej fazie zatrzymaj się i potwierdź manualnie przed przejściem do Phase 2.

---

## Phase 2: HTTP client + scraper module

### Overview

Dwa nowe moduły: `src/http_client.py` (httpx sync z retry + rate limiting) i `src/scraper.py` (dataclass `Announcement` + `scrape_new_announcements()`). Scraper nie modyfikuje BQ — tylko odczytuje (batch dedup) i zwraca listę nowych ogłoszeń.

### Changes Required

#### 1. `src/http_client.py` — httpx GET z retry

**File**: `src/http_client.py` (nowy plik)

**Intent**: Enkapsulować logikę HTTP retry (3 próby, exponential backoff, rate limit 0.5s) i User-Agent header. Rzucać `ScraperError` po wyczerpaniu prób. Używany przez scraper — w przyszłości też przez S-02.

**Contract**:
```python
def get(url: str) -> httpx.Response:
    """GET z rate limit (0.5s) i retry (3×, exp backoff).

    Raises ScraperError("All 3 attempts failed for <url>") po wyczerpaniu prób.
    Logi WARNING per nieudaną próbę.
    """
```

Stałe konfiguracyjne jako module-level constants z `os.environ.get` defaults:
- `_REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.5"))`
- `_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))`
- `_TIMEOUT = 30`
- `_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; puls-gpw/1.0)"}`

HTTP client: module-level singleton `httpx.Client` (analogiczny do wzorca `_get_client()` z `db/bigquery.py` i `_get_session()` z `oldProjectData/base.py`) — reuse TCP/TLS połączeń między requestami. Użyj `_get_http_client()` z double-checked locking, zwracającego `httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)`.

Backoff: `time.sleep(_REQUEST_DELAY * attempt)` między próbami (attempt 1→0.5s, 2→1.0s).

#### 2. `src/scraper.py` — `Announcement` dataclass + `scrape_new_announcements()`

**File**: `src/scraper.py` (nowy plik)

**Intent**: Zaimplementować główną logikę scrapera: paginacja listingu Bankier.pl, parse HTML (selektory z F-01), filtr okna 15 min (Europe/Warsaw), batch dedup i zwrócenie listy nowych ogłoszeń.

**Contract**:
```python
@dataclass
class Announcement:
    title: str           # pełny tytuł z .m-quotes-announcements-item__anchor
    espi_code: str       # prefix tytułu przed ":" (np. "SFKPOLKAP")
    bankier_url: str     # absolutny URL strony ogłoszenia
    published_at: datetime   # timezone-aware (Europe/Warsaw)
    source: str          # "espi" | "ebi"

def scrape_new_announcements() -> list[Announcement]:
    """Pobierz nowe (nie-duplikat) ogłoszenia z okna 15 min.

    Raises ScraperError jeśli HTTP fail po retry.
    Zwraca [] gdy brak nowych (normalne — INFO log, caller nie rzuca alertu).
    """
```

Env vars używane przez scraper:
- `SCRAPE_WINDOW_MINUTES` (default: `"15"`)
- `MAX_PAGES_BANKIER` (default: `"5"`)
- `BANKIER_LISTING_URL` (default: `"https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/{page}"`)

**Logika paginacji** (stop-condition na min datetime):
```
for page in 1..MAX_PAGES:
    items = fetch_and_parse(page)
    if not items: break
    page_min_dt = min(item.dt for item in items if item.dt parseable) or None
    for item in items:
        if item.dt >= cutoff and item.ann_id not in known_ids:
            add to result; known_ids.add(ann_id)
    if page_min_dt is None or page_min_dt < cutoff: break  # None = brak dat = stop
```

Diagnostics log na końcu: `logger.info("Scraper: %d new / %d seen / %d pages", new, seen, pages)`.

### Success Criteria

#### Automated Verification

- `python -c "from src.scraper import scrape_new_announcements, Announcement"` — brak ImportError
- `python -c "from src.http_client import get"` — brak ImportError

#### Manual Verification

- `python -c "from src.scraper import scrape_new_announcements; r = scrape_new_announcements(); print(len(r), r[:2])"` z prawdziwym Bankier.pl zwraca listę (może być pusta poza sesją giełdową) bez wyjątku
- Sprawdź Cloud Logging po uruchomieniu — log `"Scraper: N new / M seen / P pages"` widoczny

**Implementation Note**: Uruchom ręczny smoke test przeciwko prawdziwemu Bankier.pl przed Phase 3.

---

## Phase 3: main.py integration + unit tests

### Overview

Zastąpienie stub w `main.py` pełnym pipeline S-01 (scrape → insert). Dodanie katalogu `tests/` z unit testami pokrywającymi parse HTML, stop-condition, dedup filter i edge case'y.

### Changes Required

#### 1. `main.py` — integracja scrapera

**File**: `main.py`

**Intent**: Zastąpić `logger.info("Pipeline started")` wywołaniem `scrape_new_announcements()` i pętlą `insert_announcement()`. Fail-fast przy RuntimeError z BQ — wyjątek propaguje do istniejącego `except Exception` który wywołuje `send_alert()`.

**Contract**: Sekwencja w `main()`:
1. `create_table_if_not_exists()`
2. `new = scrape_new_announcements()` — `ScraperError` propaguje do outer except
3. `if not new: logger.info(...); return`
4. `for ann in new: insert_announcement(ann.bankier_url, ann.published_at, ann.title, None, None)` — `RuntimeError` propaguje
5. `logger.info("Pipeline completed: %d new announcements inserted", len(new))`

#### 2. `tests/__init__.py` — marker pakietu testów

**File**: `tests/__init__.py` (nowy, pusty)

**Intent**: Umożliwić pytest discovery w katalogu `tests/`.

#### 3. `tests/fixtures/sample_listing_page1.html` — fixture HTML

**File**: `tests/fixtures/sample_listing_page1.html` (nowy)

**Intent**: Minimal valid HTML fixture z 3 itemami `.m-quotes-announcements-item`: 2 w oknie 15 min, 1 starszy. Używany przez testy zamiast prawdziwego Bankier.pl.

**Contract**: Fixture musi zawierać poprawne selektory F-01:
- `.m-quotes-announcements-item__anchor` z href i tekstem `TICKER: Tytuł ogłoszenia`
- `.m-quotes-announcements-item__date` z tekstem `DD.MM.YYYY HH:MM`
- `.a-quotes-badge .value` z tekstem `espi` lub `ebi`

#### 4. `tests/test_scraper.py` — unit testy

**File**: `tests/test_scraper.py` (nowy)

**Intent**: Pokryć 6 przypadków testowych używając `respx` do mockowania httpx i `unittest.mock.patch` do mockowania `get_processed_ids_since` i `datetime`.

**Datetime mocking**: `src/scraper.py` musi importować datetime jako moduł (`import datetime`, nie `from datetime import datetime`) — dzięki temu `unittest.mock.patch("src.scraper.datetime")` chwyta wywołania `datetime.now()` wewnątrz modułu. Każdy test operujący na datach ustawia `mock_dt.now.return_value = fixed_now` gdzie `fixed_now` to timezone-aware datetime Europe/Warsaw.

**Test cases**:

| Test | Co weryfikuje | Datetime mock |
|---|---|---|
| `test_parse_item_fields` | Poprawny parse tytułu, espi_code, URL, datetime, source z fixture HTML | tak — fixed_now |
| `test_dedup_filter` | Item z known_ids jest pominięty; nowy item trafia do wyniku | tak — fixed_now |
| `test_stop_condition_on_page` | Gdy min_dt strony < cutoff → brak requestu page 2 | tak — fixed_now |
| `test_pagination_continues` | Gdy wszystkie itemy w oknie → request page 2 wysłany | tak — fixed_now |
| `test_max_pages_safeguard` | Przy MAX_PAGES_BANKIER=2 i 3 dostępnych stronach → max 2 requesty | tak — fixed_now |
| `test_empty_page_stops` | Strona bez `.m-quotes-announcements-item` → pętla kończy, brak wyjątku | tak — fixed_now |

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ -v` — wszystkie 6 testów przechodzi
- `uv run pytest tests/ --tb=short -q` — 0 failures, 0 errors
- `python -m py_compile src/scraper.py src/http_client.py main.py` — brak błędów składni

#### Manual Verification

- `python main.py` — kończy bez wyjątku; log `"Pipeline completed: N new announcements inserted"` lub `"Pipeline completed: 0 new announcements"` widoczny
- Drugie `python main.py` w ciągu minuty — log `"Pipeline completed: 0 new announcements"` (dedup działa)
- Sprawdź BigQuery Console: tabela `espi_ebi.announcements` zawiera wiersze z `ticker=NULL`, `company=NULL`, `published_at` w ostatnich 15 min
- Cloud Logging (po deploy lub lokalnie): structured JSON logi widoczne

**Implementation Note**: Koniecznie uruchom dwa kolejne przebiegi (dedup test) przed oznaczeniem fazy jako done.

---

## Testing Strategy

### Unit Tests

- Parse HTML: poprawne pola z fixture (title, espi_code, bankier_url, published_at, source)
- Stop-condition: `min_dt < cutoff` → brak requestu kolejnej strony
- Dedup: znany `announcement_id` w known_ids → item pominięty
- Pagination: wszystkie itemy w oknie → page 2 fetched
- Max pages: env var `MAX_PAGES_BANKIER=2` → max 2 requesty
- Empty page: brak items → pętla kończy czysto

### Integration / Manual Testing

1. `python main.py` z prawdziwym Bankier.pl + BQ — weryfikacja E2E
2. Drugi run w ciągu minuty — weryfikacja dedup (0 nowych)
3. Sprawdzenie wierszy w BQ Console (`ticker=NULL`, `company=NULL`)
4. Weryfikacja logów JSON w terminalu (structured logging F-03)

### Manual Testing Steps

1. Ustaw `.env`: `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`
2. `python main.py` — obserwuj logi
3. Sprawdź BQ: `SELECT * FROM espi_ebi.announcements ORDER BY processed_at DESC LIMIT 10`
4. `python main.py` ponownie — sprawdź że log mówi `0 new announcements`

## Performance Considerations

- 5 stron × 25 itemów = max 125 HTTP requestów per run (praktycznie 1-2 strony)
- Rate limit 0.5s między requestami → max run time ~3s (strona 1) do ~15s (5 stron)
- BQ batch query (1 query) zamiast N per-item queries — główna optymalizacja

## Migration Notes

Brak migracji schematu BQ — `ticker` i `company` są już NULLABLE w F-02. Wiersze insertowane przez S-01 będą miały `ticker=NULL`, `company=NULL` do czasu S-02.

## References

- Research HTML selektory: `context/archive/2026-05-26-scraper-parser-research/research.md`
- BQ client: `db/bigquery.py`
- Exception hierarchy: `src/exceptions.py`
- Referencyjna implementacja: `oldProjectData/bankier.py` (logika retry, diagnostyki)
- Notifier: `src/notifier.py`

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: BQ helpers + dev deps

#### Automated

- [x] 1.1 `uv sync` kończy bez błędów po dodaniu pytest + respx — c69ed01
- [x] 1.2 `python -c "from db.bigquery import announcement_id_for_url, get_processed_ids_since"` — brak ImportError — c69ed01
- [x] 1.3 Istniejące wywołania `_announcement_id` w module nadal działają — c69ed01

#### Manual

- [x] 1.4 `announcement_id_for_url(url)` zwraca ten sam hash co stare `_announcement_id(url)` — c69ed01

### Phase 2: HTTP client + scraper module

#### Automated

- [x] 2.1 `python -c "from src.http_client import get"` — brak ImportError
- [x] 2.2 `python -c "from src.scraper import scrape_new_announcements, Announcement"` — brak ImportError

#### Manual

- [x] 2.3 Smoke test: `scrape_new_announcements()` działa bez wyjątku z prawdziwym Bankier.pl
- [x] 2.4 Structured log `"Scraper: N new / M seen / P pages"` widoczny w terminalu

### Phase 3: main.py integration + unit tests

#### Automated

- [ ] 3.1 `uv run pytest tests/ -v` — wszystkie 6 testów przechodzi
- [ ] 3.2 `python -m py_compile src/scraper.py src/http_client.py main.py` — brak błędów

#### Manual

- [ ] 3.3 `python main.py` kończy bez wyjątku; log `"Pipeline completed: N new announcements inserted"`
- [ ] 3.4 Drugi run w ciągu minuty → log `"0 new announcements"` (dedup działa)
- [ ] 3.5 BQ Console: wiersze z `ticker=NULL`, `company=NULL`, `published_at` w ostatnich 15 min
