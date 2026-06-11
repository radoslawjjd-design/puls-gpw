# BigQuery Fields Audit — Implementation Plan

## Overview

Audit i porządkowanie 8 nullable pól tabeli `announcements` w BigQuery: dokumentacja semantyki, usunięcie martwego kodu (`analysis_type`, `save_analysis()`), rozdzielenie `processed_at` na dwa precyzyjne timestampy, uproszczenie `insert_announcement`, surfacing odrzuceń analizy w logach oraz testy jednostkowe potwierdzające semantykę każdego kroku pipeline'u.

## Current State Analysis

Tabela `announcements` (17 kolumn, `db/bigquery.py:17-35`) rozrastała się inkrementalnie przez 5 slices. Kilka pól jest w stanie niezgodnym z bieżącą implementacją:

- `save_analysis()` (`db/bigquery.py:171-203`) — nigdy nie wywoływana z `main.py` ani `post_main.py`; ustawia `analysis_type`, `post_text`, `supervisor_attempts`, `processed_at`. Martwa od momentu gdy S-03 przeszło na `save_analysis_result()`.
- `analysis_type` (`_SCHEMA:27`) — zawsze NULL w produkcji; jedyny setter to martwa `save_analysis()`.
- `processed_at` — ustawiane przez `save_post_text` (`db/bigquery.py:349-354`), czyli dopiero przy generacji posta. NULL między analizą a generacją posta. Nazwa myląca — sugeruje "cały pipeline", w rzeczywistości znaczy "generacja posta".
- `insert_announcement` (`db/bigquery.py:128-168`) — przyjmuje `company`/`ticker` jako parametry, ale `main.py:47` zawsze wywołuje z `None, None`. Dataclass `Announcement` (scraper) nie ma tych pól — wyciąga je tylko parser przez drugi HTTP hop.
- `analysis_reject_reason` — zapisywane do BQ przy odrzuceniu, ale nie pojawia się w logach Cloud Logging.

### Key Discoveries

- `Announcement` dataclass (`src/scraper.py:18-24`): brak pól `company`/`ticker` — scraper ich nie wyciąga z HTML
- `_extract_ticker_company` (`src/parser.py:175-201`): wymaga **drugiego HTTP requesta** do profilu spółki — dopiero parser może te pola dostarczyć
- `ensure_schema_current()` (`db/bigquery.py:90-113`): addytywna migracja only — dodaje brakujące kolumny, nie usuwa ani nie rename'uje
- `scripts/test_bq.py:41-47`: jedyne inne miejsce wywołujące `insert_announcement` z `company`/`ticker`
- Analyzer (`src/analyzer.py:217-223`): jeśli `parsed_content` lub `ticker` to None → zwraca `null_result` bez Gemini call; downstream BQ zapis działa poprawnie (wszystkie analysis pola NULL)

## Desired End State

Po wdrożeniu:
- `analysis_type` i `save_analysis()` usunięte z kodu; kolumna usunięta z BQ (krok manualny)
- `processed_at` → `posted_at` (ustawiane przez `save_post_text`); nowe pole `analyzed_at` (ustawiane przez `save_analysis_result`)
- `insert_announcement` bez `company`/`ticker` w sygnaturze; INSERT nie wiąże tych pól
- `main.py` loguje WARNING gdy `analysis_approved is False`, z treścią `analysis_reject_reason`
- Każde nullable pole ma udokumentowaną semantykę w docstringu
- Testy jednostkowe potwierdzają że każdy krok pipeline'u ustawia właściwe pola

### Weryfikacja

```
uv run pytest tests/test_bigquery.py -v   # wszystkie przechodzą
uv run mypy db/bigquery.py main.py        # brak błędów typów
grep -n "analysis_type\|save_analysis\b\|processed_at" db/bigquery.py  # brak wyników
```

## What We're NOT Doing

- Nie dodajemy `company`/`ticker` do scrapera — parser jest jedynym źródłem prawdy i pozostaje nim
- Nie śledzimy prób analizatora w BQ (`supervisor_attempts` pozostaje "próby post supervisora")
- Nie rename'ujemy `supervisor_attempts` — koszt BQ rename > zysk z czytelności
- Nie surfacujemy `analysis_reject_reason` w emailach — tylko Cloud Logging
- Nie tworzymy automatycznej migracji danych BQ — `ensure_schema_current()` doda nowe kolumny, historyczne dane `processed_at` migruje człowiek

## Implementation Approach

Zmiany tylko w `db/bigquery.py`, `main.py`, `scripts/test_bq.py` i `tests/test_bigquery.py`. Faza 1 (kod) i Faza 3 (testy) są czysto programistyczne. Faza 2 to zestaw kroków BQ do wykonania ręcznie po deployu Fazy 1 — plan dokumentuje dokładne komendy.

## Critical Implementation Details

**Kolejność deploy vs BQ migration:** Kod z Fazy 1 musi być zdeploy'owany (lub uruchomiony lokalnie) zanim wykonamy DROP COLUMN w Fazie 2, bo `ensure_schema_current()` przy starcie automatycznie doda `posted_at` i `analyzed_at`. Dopiero potem można bezpiecznie dropować `processed_at` i `analysis_type`.

---

## Phase 1: Code cleanup

### Overview

Usuwa martwy kod, upraszcza sygnatury, aktualizuje `_SCHEMA`, dodaje `analyzed_at` do `save_analysis_result`, zamienia `processed_at` → `posted_at` w `save_post_text`, loguje odrzucenia.

### Changes Required

#### 1. `_SCHEMA` — aktualizacja listy pól

**File**: `db/bigquery.py`

**Intent**: Usunąć `analysis_type` i `processed_at` z definicji schematu; dodać `analyzed_at` i `posted_at`. `ensure_schema_current()` przy starcie automatycznie doda nowe kolumny do istniejącej tabeli BQ.

**Contract**: Usunąć `SchemaField("analysis_type", ...)` (linia 27) i `SchemaField("processed_at", ...)` (linia 25). Dodać dwa nowe wpisy po `post_text`:
- `SchemaField("posted_at", "TIMESTAMP", mode="NULLABLE")`
- `SchemaField("analyzed_at", "TIMESTAMP", mode="NULLABLE")`

#### 2. Usunąć `save_analysis()`

**File**: `db/bigquery.py`

**Intent**: Usunąć całą funkcję `save_analysis()` (linie 171-203) — martwy kod, nigdy nie wywoływany, jedyny setter `analysis_type`.

**Contract**: Usunąć blok funkcji wraz z docstringiem i walidacją `analysis_type`. Żaden import ani callsite nie odwołuje się do tej funkcji w pipeline (zweryfikowane: `main.py` importuje tylko `save_analysis_result`, nie `save_analysis`).

#### 3. Uprościć `insert_announcement()`

**File**: `db/bigquery.py`

**Intent**: Usunąć `company` i `ticker` z sygnatury i z treści INSERT — parser zawsze ustawia je przez `update_parsed_content`, nigdy scraper.

**Contract**: Nowa sygnatura: `insert_announcement(url, published_at, title, priority=None) -> str`. INSERT wymienia tylko kolumny z rzeczywistymi wartościami: `announcement_id, url, published_at, title, priority`. Pozostałe nullable kolumny BQ przyjmują NULL domyślnie — nie trzeba ich wymieniać.

#### 4. `save_analysis_result()` — dodać `analyzed_at`

**File**: `db/bigquery.py`

**Intent**: Ostemplować moment zakończenia analizy Gemini — `analyzed_at = CURRENT_TIMESTAMP()` w UPDATE, analogicznie do tego jak `save_post_text` ustawia `posted_at`.

**Contract**: Dodać `analyzed_at = CURRENT_TIMESTAMP()` do klauzuli SET w UPDATE query (linia ~259-266). Nie wymaga nowego parametru funkcji — wartość pochodzi z BQ server-side timestamp.

#### 5. `save_post_text()` — rename `processed_at` → `posted_at`

**File**: `db/bigquery.py`

**Intent**: Zaktualizować DML UPDATE żeby pisał do nowej kolumny `posted_at` zamiast `processed_at`.

**Contract**: W query (linia ~349-354) zamienić `processed_at = CURRENT_TIMESTAMP()` na `posted_at = CURRENT_TIMESTAMP()`.

#### 6. Docstringi — semantyka nullable pól

**File**: `db/bigquery.py`

**Intent**: Dodać w docstringu modułu lub w komentarzu przy `_SCHEMA` tabelę z semantyką każdego nullable pola — co oznacza NULL vs populated, który krok pipeline'u ustawia dane pole.

**Contract**: Komentarz blokowy przed `_SCHEMA` (lub docstring modułu) ze zwięzłą tabelą:

```
# Nullable field semantics (NULL = step not yet reached or failed):
# company, ticker      — set by parser (update_parsed_content); NULL if parse failed
# parsed_content       — set by parser; NULL if parse failed; analyzer skips if NULL
# analyzed_at          — set by save_analysis_result; NULL if analyzer skipped/failed
# structured_analysis  — set by save_analysis_result; NULL if analyzer skipped/failed
# analysis_approved    — set by save_analysis_result; NULL if analyzer skipped/failed
# analysis_reject_reason — set only when analysis_approved=FALSE; NULL otherwise
# event_type           — set by save_analysis_result; NULL if analyzer skipped/failed
# analysis_score       — set by save_analysis_result; NULL if analyzer skipped/failed
# post_text            — set by save_post_text; NULL if generation failed (3 attempts)
# posted_at            — set by save_post_text; NULL until post generation attempted
# supervisor_attempts  — set by save_post_text; counts post supervisor retries (1-3)
# priority             — set by scraper (HTML badge); NULL if no priority badge
```

#### 7. `main.py` — zaktualizować wywołanie `insert_announcement`

**File**: `main.py`

**Intent**: Usunąć `None, None` dla `company`/`ticker` z wywołania `insert_announcement` (linia 47) — sygnatura już ich nie przyjmuje.

**Contract**: `insert_announcement(ann.bankier_url, ann.published_at, ann.title, ann.priority)`

#### 8. `main.py` — logować odrzucenia analizy

**File**: `main.py`

**Intent**: Surfacować `analysis_reject_reason` w Cloud Logging gdy Gemini gate odrzuca ogłoszenie.

**Contract**: Po wywołaniu `save_analysis_result` (linia ~52-59), jeśli `result.analysis_approved is False`, dodać:
```python
logger.warning(
    "Analyzer: rejected %s — %s", ann_id, result.analysis_reject_reason
)
```

#### 9. `scripts/test_bq.py` — zaktualizować wywołanie

**File**: `scripts/test_bq.py`

**Intent**: Usunąć `company`/`ticker` z wywołania `insert_announcement` w skrypcie manualnym (linia 41-47).

**Contract**: Wywołanie bez `company="Test Spółka S.A."` i `ticker="TST"`.

### Success Criteria

#### Automated Verification

- Testy przechodzą: `uv run pytest tests/test_bigquery.py -v`
- Brak importu `save_analysis` w całym projekcie: `grep -rn "save_analysis\b" . --include="*.py"` zwraca tylko definicję (która zostanie usunięta) — po usunięciu: brak wyników
- Brak referencji do `processed_at` i `analysis_type` w plikach `.py`: `grep -rn "processed_at\|analysis_type" . --include="*.py"` — brak wyników
- Type checking: `uv run mypy db/bigquery.py main.py scripts/test_bq.py`

#### Manual Verification

- `insert_announcement` nie przyjmuje `company`/`ticker` — wywołanie z tymi argumentami daje `TypeError`
- `_SCHEMA` zawiera `posted_at` i `analyzed_at`, nie zawiera `processed_at` ani `analysis_type`

**Implementation Note**: Po zakończeniu tej fazy i przejściu weryfikacji, zatrzymaj się — następnym krokiem jest ręczna migracja BQ (Faza 2).

---

## Phase 2: BQ migration (human-only)

### Overview

Wykonać ręcznie po deployu Fazy 1. `ensure_schema_current()` przy pierwszym starcie automatycznie doda `posted_at` i `analyzed_at`. Człowiek dropuje legacy kolumny i opcjonalnie migruje historyczne dane.

### Changes Required

#### 1. Uruchomić pipeline lub `ensure_schema_current()` raz

**File**: BQ table `espi_ebi.announcements`

**Intent**: `ensure_schema_current()` wywoływane przy starcie `main.py` automatycznie doda `posted_at` i `analyzed_at` do istniejącej tabeli BQ.

**Contract**: Wystarczy uruchomić `uv run python main.py` lub wywołać `ensure_schema_current()` raz po deployu.

#### 2. Zmigrować historyczne dane `processed_at` → `posted_at`

**File**: BQ table `espi_ebi.announcements`

**Intent**: Przepisać istniejące wartości `processed_at` do nowej kolumny `posted_at` przed dropem starej kolumny.

**Contract**: Wykonać w BQ console lub `bq query`:
```sql
UPDATE `{PROJECT}.espi_ebi.announcements`
SET posted_at = processed_at
WHERE posted_at IS NULL AND processed_at IS NOT NULL;
```

#### 3. Dropnąć legacy kolumny

**File**: BQ table `espi_ebi.announcements`

**Intent**: Usunąć `analysis_type` (zawsze NULL) i `processed_at` (dane przeniesione do `posted_at`).

**Contract**: Dwie komendy — wykonać w BQ console lub `bq query`:
```sql
ALTER TABLE `{PROJECT}.espi_ebi.announcements` DROP COLUMN IF EXISTS analysis_type;
ALTER TABLE `{PROJECT}.espi_ebi.announcements` DROP COLUMN IF EXISTS processed_at;
```

### Success Criteria

#### Manual Verification

- BQ schema nie zawiera `analysis_type` ani `processed_at`: `bq show --schema {PROJECT}:espi_ebi.announcements`
- BQ schema zawiera `posted_at` i `analyzed_at`
- Historyczne wartości przeniesione: `SELECT COUNT(*) FROM ... WHERE processed_at IS NOT NULL AND posted_at IS NULL` zwraca 0

---

## Phase 3: Tests

### Overview

Dodać testy jednostkowe (mocki BQ) potwierdzające że każdy krok pipeline'u ustawia właściwe pola — szczególnie nowe timestampy i uproszczone INSERT.

### Changes Required

#### 1. Nowe testy w `tests/test_bigquery.py`

**File**: `tests/test_bigquery.py`

**Intent**: Pokryć cztery kontrakt-testy: INSERT bez company/ticker, UPDATE parsed content ustawia wszystkie trzy pola, `save_analysis_result` zawiera `analyzed_at`, `save_post_text` zawiera `posted_at`.

**Contract**: Cztery niezależne funkcje testowe (wzorzec: istniejący `_mock_bq_client`):

- `test_insert_announcement_omits_company_ticker` — weryfikuje że query string INSERT nie zawiera `@company` ani `@ticker`; query_parameters nie mają tych nazw
- `test_update_parsed_content_sets_three_fields` — weryfikuje że query string zawiera `parsed_content`, `ticker`, `company` w SET clause
- `test_save_analysis_result_stamps_analyzed_at` — weryfikuje że query string zawiera `analyzed_at = CURRENT_TIMESTAMP()`
- `test_save_post_text_stamps_posted_at` — weryfikuje że query string zawiera `posted_at = CURRENT_TIMESTAMP()` (nie `processed_at`)

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_bigquery.py -v` — wszystkie testy przechodzą, 4 nowe widoczne w output
- `uv run pytest` — cały suite bez regresji

#### Manual Verification

- Każdy nowy test weryfikuje inny krok pipeline (INSERT / parsed / analysis / post) — brak duplikacji pokrycia

---

## Testing Strategy

### Unit Tests

Wzorzec: mock BQ client (`_mock_bq_client`) + `patch("db.bigquery._get_client")`. Sprawdzamy query string i parametry — nie uderzamy w BQ.

### Integration Tests

Brak nowych — `scripts/test_bq.py` jest manualnym smoke testem; wystarczy go zaktualizować (Faza 1, punkt 9).

### Manual Testing Steps

1. Po Fazie 1: `uv run pytest -v` — brak błędów
2. `grep -rn "save_analysis\b\|processed_at\|analysis_type" . --include="*.py"` — brak wyników
3. Po Fazie 2: `bq show --schema {PROJECT}:espi_ebi.announcements` — schema zgodna z `_SCHEMA` w kodzie

## Migration Notes

Faza 2 musi nastąpić po deployu Fazy 1 (nowy kod musi być aktywny, `ensure_schema_current()` musi się wykonać). Kolejność: deploy → auto-add new cols → migrate data → drop legacy cols.

## References

- BQ schema: `db/bigquery.py:17-35`
- Parser ticker/company extraction: `src/parser.py:175-201`
- Scraper dataclass (bez company/ticker): `src/scraper.py:17-24`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Code cleanup

#### Automated

- [x] 1.1 Testy przechodzą: `uv run pytest tests/test_bigquery.py -v` — e0e2e65
- [x] 1.2 Brak referencji `save_analysis\b` w `.py`: `grep -rn "save_analysis\b" . --include="*.py"` — e0e2e65
- [x] 1.3 Brak `processed_at` i `analysis_type` w `.py`: `grep -rn "processed_at\|analysis_type" . --include="*.py"` — e0e2e65
- [x] 1.4 Type check: mypy nie zainstalowane w projekcie; `python -c "import db.bigquery; import main"` OK — e0e2e65

#### Manual

- [x] 1.5 `insert_announcement` bez `company`/`ticker` — TypeError przy próbie przekazania — e0e2e65
- [x] 1.6 `_SCHEMA` zawiera `posted_at` i `analyzed_at`, nie zawiera `processed_at` ani `analysis_type` — e0e2e65

### Phase 2: BQ migration (human-only)

#### Manual

- [ ] 2.1 `ensure_schema_current()` dodała `posted_at` i `analyzed_at` — zweryfikowane `bq show --schema`
- [ ] 2.2 Historyczne dane zmigrowane: `processed_at IS NOT NULL AND posted_at IS NULL` = 0 wierszy
- [ ] 2.3 Legacy kolumny usunięte: schema BQ nie zawiera `analysis_type` ani `processed_at`

### Phase 3: Tests

#### Automated

- [x] 3.1 `uv run pytest tests/test_bigquery.py -v` — 4 nowe testy widoczne i przechodzą
- [x] 3.2 `uv run pytest` — cały suite bez regresji

#### Manual

- [x] 3.3 Każdy nowy test weryfikuje inny krok pipeline (INSERT / parsed / analysis / post) — brak duplikacji pokrycia
