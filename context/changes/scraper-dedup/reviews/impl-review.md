<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Scraper Bankier.pl + Dedup BigQuery

- **Plan**: context/changes/scraper-dedup/plan.md
- **Scope**: All phases (1–3 of 3)
- **Date**: 2026-06-05
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical  4 warnings  5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | WARNING |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — _TIMEOUT nie jest konfigurowalny przez env var (plan drift)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/http_client.py:14
- **Detail**: Plan wymaga os.environ.get defaults dla wszystkich czterech stałych. _REQUEST_DELAY i _MAX_RETRIES używają os.environ.get, ale _TIMEOUT = 30 był hardcoded. Przy Cloud Run nie można zmienić timeoutu bez redeploy.
- **Fix**: Zmień na `_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))`.
- **Decision**: FIXED

### F2 — bare RuntimeError zamiast typed BigQueryError w hierarchii wyjątków

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architecture / Pattern Consistency
- **Location**: db/bigquery.py:119, :153, :155
- **Detail**: src/exceptions.py definiuje PipelineStageError → ScraperError. db/bigquery.py rzucał bare RuntimeError — łamało hierarchię; future caller z `except PipelineStageError` cicho pominąłby błędy BQ.
- **Fix A ⭐ Recommended**: Dodaj `BigQueryError(PipelineStageError)` do src/exceptions.py i zastąp RuntimeError w bigquery.py.
  - Strength: Zamknięcie luki architektonicznej; <10 linii zmiany.
  - Tradeoff: Callerzy muszą wiedzieć o nowym typie.
  - Confidence: HIGH — PipelineStageError już istnieje jako baza.
  - Blind spot: None significant.
- **Fix B**: Zostaw RuntimeError, dodaj komentarz.
  - Confidence: LOW — antywzorzec.
- **Decision**: FIXED via Fix A

### F3 — strona z nieprzetrawalnymi datami: page_min_dt=None — cicha terminacja paginacji

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/scraper.py:114
- **Detail**: Jeśli strona MA items, lecz żaden nie ma parsowalnej daty — pętla urywała się cicho na linii 114. Maskuje regresję zmiany formatu daty przez Bankier.
- **Fix**: Rozdziel warunek i dodaj `logger.warning("Bankier page %d: brak parsowalnych dat — stop", page)` dla gałęzi None.
- **Decision**: FIXED

### F4 — db/bigquery.py: brak loggera — operacje BQ niewidoczne w Cloud Logging

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py (brak import logging)
- **Detail**: Każdy inny moduł deklaruje logger. db/bigquery.py nie miał loggera — tworzenie tabeli, inserty i save_analysis były całkowicie ciche w Cloud Logging.
- **Fix**: Dodaj `import logging` + `logger = logging.getLogger(__name__)`, logger.info w create_table_if_not_exists, logger.debug w insert_announcement.
- **Decision**: FIXED

### F5 — sleep przed pierwszą próbą HTTP (nie tylko między retry)

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/http_client.py:40
- **Detail**: `time.sleep(_REQUEST_DELAY)` stał przed każdą próbą, włącznie z attempt=1. Dodawał 0.5s przed każdym requestem nawet przy cold-start.
- **Fix**: Przesuń sleep do retry path (`if attempt > 1`).
- **Decision**: FIXED

### F6 — company/ticker=None bez komentarza TODO

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: main.py:26
- **Detail**: `insert_announcement(..., None, None)` — hardcoded None dla company i ticker bez komentarza wyjaśniającego, że to celowe (scope S-02).
- **Fix**: Dodaj inline komentarz `# company/ticker: scope S-02`.
- **Decision**: FIXED

### F7 — DST fold: replace(tzinfo=) zamiast fold-aware localize

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/scraper.py:74
- **Detail**: `replace(tzinfo=_WARSAW)` zawsze przyjmuje fold=0 podczas godziny przestawiania zegarów. Edge case ~raz na rok w 15-minutowym oknie.
- **Fix**: Dodaj komentarz dokumentujący znane ograniczenie.
- **Decision**: FIXED

### F8 — WIF credentials: pominięcie quota_project bez logu

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:41–43
- **Detail**: Jeśli credentials nie mają `with_quota_project` (np. WIF na Cloud Run), blok był cicho pomijany. Może dać 403 w produkcji bez żadnego sygnału w logach.
- **Fix**: Dodaj `logger.warning(...)` w gałęzi else.
- **Decision**: FIXED

### F9 — brak testu dla ogłoszenia z datą w przyszłości

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/test_scraper.py
- **Detail**: Scraper nie ma górnego limitu daty — item z `published_at > now` trafia do wyniku bez ostrzeżenia. Brak guard'a i brak testu.
- **Fix**: Dodaj guard + test_future_item.
- **Decision**: SKIPPED
