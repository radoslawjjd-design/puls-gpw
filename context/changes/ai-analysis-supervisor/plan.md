# S-03: Analiza AI + scoring komunikatów ESPI/EBI — Implementation Plan

## Overview

Rozszerzamy 15-minutowy pipeline o etap analizy AI: każde nowe ogłoszenie z `parsed_content`
dostaje strukturyzowaną analizę Gemini Flash (Phase 1), weryfikację halucynacji przez drugi
call Gemini (Phase 2) i score łączący tier spółki + typ zdarzenia + badge priorytetu (Phase 3).
Wyniki zapisywane do BQ — gotowe do agregacji przez S-04.

## Current State Analysis

Pipeline (`main.py`) wykonuje: `scrape → insert_announcement → parse_announcement → update_parsed_content`.
Po S-02 każde ogłoszenie ma `parsed_content` (string ≤15k znaków lub NULL).

Istniejące aktywa:
- `db/bigquery.py:84` — `ensure_schema_current()`: dodaje brakujące kolumny przy starcie, zero ręcznej migracji
- `db/bigquery.py:17` — `_SCHEMA`: wystarczy dopisać nowe pola
- `src/scraper.py:18` — `Announcement` dataclass: brak pola `priority`
- `db/bigquery.py:122` — `insert_announcement()`: brak parametru `priority`
- `pyproject.toml`: brak `google-genai` — trzeba dodać
- Testy używają `unittest.mock.patch` — wzorzec dla mockowania SDK

## Desired End State

Po wdrożeniu S-03, po każdym 15-minutowym loopie:

1. Każde nowe ogłoszenie ma w BQ: `priority`, `structured_analysis` (JSON), `analysis_approved` (bool),
   `analysis_reject_reason` (lub NULL), `event_type`, `analysis_score` (float lub NULL gdy odrzucone).
2. Ogłoszenia z `analysis_approved=TRUE` mają `analysis_score > 0` — gotowe do wybrania przez S-04.
3. Ogłoszenia z `parsed_content=NULL` — pola analizy NULL (skip bez błędu).

### Weryfikacja end state

```
SELECT announcement_id, analysis_approved, analysis_score, event_type
FROM `<project>.espi_ebi.announcements`
WHERE DATE(processed_at) = CURRENT_DATE()
ORDER BY analysis_score DESC
LIMIT 10
```

## Key Discoveries

- `db/bigquery.py:84` — `ensure_schema_current()` sprawdza diff kolumn i dodaje brakujące:
  wystarczy dopisać pola do `_SCHEMA`, zero dodatkowej logiki migracji
- `src/scraper.py:95` — selector `.a-quotes-badge .value` pobiera źródło (espi/ebi);
  badge priorytetu to osobny element z klasą `-priority`: `.a-quotes-badge.-priority`
- `db/bigquery.py:198` — wzorzec `update_parsed_content()`: UPDATE z parametryzowanym DML —
  `save_analysis_result()` będzie analogiczne
- `lessons.md` — `load_dotenv()` musi być pierwszym importem; Gemini client (inny niż BQ)
  nie używa ADC — inicjalizuje się przez `GEMINI_API_KEY`, więc guard `with_quota_project`
  nie dotyczy, ale singleton + lock obowiązują

## What We're NOT Doing

- Nie generujemy X-posta (to S-04)
- Nie implementujemy re-analizy istniejących wierszy BQ (batch backfill) — pipeline przetwarza tylko nowe
- Nie dodajemy OCR ani nowych źródeł treści — `parsed_content` pochodzi z S-02
- Nie robimy rate limitingu przez asyncio/semaphore — sekwencyjne wywołania wystarczą
  (X-post generator odczytuje BQ dopiero o 8:30/12:00/15:00/17:00, czas analizy nie jest krytyczny)
- Nie weryfikujemy czy `analysis_approved NOT NULL` w main.py — scraper już deduplikuje
  po `announcement_id`, więc raz wstawione ogłoszenie nie wróci do loopa

## Implementation Approach

Nowy moduł `src/analyzer.py` z trzema warstwami:
1. Klient Gemini (singleton, thread-safe) — `google-genai` SDK z `GEMINI_API_KEY`
2. Dwa prywatne calle: `_call_analysis()` (analiza treści → JSON) i `_call_gate()` (weryfikacja)
3. `_compute_score()` (czysta Python, bez LLM) + `analyze_announcement()` jako orchestrator

Każdy błąd API lub błąd parsowania JSON → skip z WARNING + NULL pola (nie blokuje kolejnych ogłoszeń).
Score liczony tylko gdy `analysis_approved=True`.

---

## Phase 0: Priority badge

### Overview

Dodajemy pole `priority` do `Announcement` dataclass, parsujemy badge z HTML Bankier.pl
i propagujemy przez `insert_announcement()` do BQ.

### Changes Required

#### 1. Announcement dataclass — nowe pole priority

**File**: `src/scraper.py`

**Intent**: Dodaj `priority: str | None` do `Announcement` — ostatnie pole dataclassy,
żeby nie zepsuć istniejących konstruktorów pozycyjnych (są testowe).

**Contract**: Pole `priority` z wartościami `"Ważny"` / `"Średni"` / `None`.

#### 2. Parsowanie badge priorytetu w pętli scrapera

**File**: `src/scraper.py`

**Intent**: Po wyciągnięciu `source`, wyciągnij badge priorytetu i przypisz do `priority`.

**Contract**: Selektor `.a-quotes-badge.-priority` — pobierz `get_text(strip=True)` jeśli
element istnieje, `None` w przeciwnym razie. Przekaż do `Announcement(... priority=priority)`.

#### 3. Nowe kolumny BQ w _SCHEMA — Priority

**File**: `db/bigquery.py`

**Intent**: Dodaj `priority STRING NULLABLE` do `_SCHEMA` po `parsed_content`.
`ensure_schema_current()` automatycznie doda kolumnę przy następnym starcie.

**Contract**: `bigquery.SchemaField("priority", "STRING", mode="NULLABLE")`

#### 4. Priority w insert_announcement()

**File**: `db/bigquery.py`

**Intent**: Dodaj parametr `priority: str | None` do sygnatury i wstaw do BQ przy INSERT.

**Contract**: Dodaj `priority` do listy kolumn INSERT i `@priority` do VALUES.
Nowy `ScalarQueryParameter("priority", "STRING", priority)` w `job_config`.

#### 5. Przekazanie priority w main.py

**File**: `main.py`

**Intent**: Przekaż `ann.priority` jako nowy argument do `insert_announcement()`.

**Contract**: `insert_announcement(ann.bankier_url, ann.published_at, ann.title, None, None, ann.priority)`

### Success Criteria

#### Automated Verification

- Import działa bez błędów: `uv run python -c "from src.scraper import Announcement; print(Announcement.__dataclass_fields__.keys())"`
- Testy scrapera przechodzą: `uv run pytest tests/test_scraper.py -v`
- Linting: `uv run ruff check src/ db/ main.py` (jeśli skonfigurowany)

#### Manual Verification

- Uruchom `main.py` lokalnie (z `.env`); sprawdź w BQ że kolumna `priority` istnieje
  i ma wartość dla ogłoszeń gdzie Bankier.pl pokazuje badge (np. ogłoszenia z WIG20)
- Ogłoszenia bez badge mają `priority=NULL`

**Implementation Note**: Po weryfikacji manualnej (BQ ma kolumnę `priority`) — przejdź do Phase 1.

---

## Phase 1: src/analyzer.py + BQ schema rozszerzenie

### Overview

Nowy moduł `src/analyzer.py`: klient Gemini, analiza treści → JSON, hallucination gate,
scoring. Rozszerzenie `_SCHEMA` w BQ o 5 nowych kolumn + nowa funkcja `save_analysis_result()`.

### Changes Required

#### 1. Zależność google-genai

**File**: `pyproject.toml`

**Intent**: Dodaj `google-genai` do `dependencies`. Uruchom `uv sync` po edycji.

**Contract**: `"google-genai>=1.0"` — unified Gemini SDK, obsługuje `GEMINI_API_KEY`.

#### 2. Nowe kolumny BQ w _SCHEMA — S-03 analysis fields

**File**: `db/bigquery.py`

**Intent**: Dodaj 5 kolumn S-03 po `priority` w `_SCHEMA`.

**Contract**:
```python
bigquery.SchemaField("structured_analysis", "STRING", mode="NULLABLE"),
bigquery.SchemaField("analysis_approved", "BOOL", mode="NULLABLE"),
bigquery.SchemaField("analysis_reject_reason", "STRING", mode="NULLABLE"),
bigquery.SchemaField("event_type", "STRING", mode="NULLABLE"),
bigquery.SchemaField("analysis_score", "FLOAT64", mode="NULLABLE"),
```

#### 3. save_analysis_result() w bigquery.py

**File**: `db/bigquery.py`

**Intent**: Nowa funkcja UPDATE zapisująca wyniki analizy S-03 do wiersza ogłoszenia.
Wzorzec identyczny jak `update_parsed_content()`.

**Contract**: Sygnatura:
```python
def save_analysis_result(
    announcement_id: str,
    structured_analysis: str | None,
    analysis_approved: bool | None,
    analysis_reject_reason: str | None,
    event_type: str | None,
    analysis_score: float | None,
) -> None:
```
UPDATE ustawia wszystkie 5 pól. Rzuca `BigQueryError` gdy 0 wierszy pasuje lub query failuje.

#### 4. AnalysisResult dataclass

**File**: `src/analyzer.py` (nowy plik)

**Intent**: Dataclass zwracana przez `analyze_announcement()` — mapuje 1:1 na pola BQ.

**Contract**:
```python
@dataclass
class AnalysisResult:
    announcement_id: str
    structured_analysis: str | None
    analysis_approved: bool | None
    analysis_reject_reason: str | None
    event_type: str | None
    analysis_score: float | None
```

#### 5. Gemini client singleton

**File**: `src/analyzer.py`

**Intent**: Thread-safe singleton klienta Gemini (wzorzec jak `_get_client()` w bigquery.py).

**Contract**: Inicjalizacja przez Vertex AI — brak nowego sekretu, ten sam service account co BQ:
```python
genai.Client(
    vertexai=True,
    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
    location=os.environ.get("GOOGLE_CLOUD_REGION", "europe-central2"),
)
```
Model: `os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")`. Używaj `threading.Lock()`.

#### 6. _call_analysis() — Gemini analiza treści

**File**: `src/analyzer.py`

**Intent**: Wywołaj Gemini z `parsed_content`, zwróć sparsowany dict lub `None` przy błędzie.
`response_mime_type="application/json"` wymusza czysty JSON (bez markdown wrapping).

**Contract**: Sygnatura: `_call_analysis(parsed_content: str) -> dict | None`

System prompt (dosłownie):
```
Jesteś analitykiem komunikatów ESPI/EBI spółek notowanych na GPW i NewConnect.
Twoim zadaniem jest wyciągnięcie kluczowych informacji z komunikatu giełdowego.

Zwróć JSON z polami:
- company: pełna nazwa spółki (string)
- ticker: symbol giełdowy (string, np. "PKO", "CDR")
- event_type: typ zdarzenia (string, jedna z wartości z listy poniżej)
- key_numbers: lista kluczowych liczb/kwot z komunikatu, sformatowanych czytelnie (array of strings)
- sentiment: ocena wydźwięku (string: "positive", "negative", "neutral")
- summary_pl: krótkie podsumowanie komunikatu po polsku, max 2 zdania (string)

Dozwolone wartości event_type:
wyniki_finansowe, upadlosc, restrukturyzacja, przejecie, fuzja, wezwanie,
dywidenda, emisja_akcji, kontrakt_znaczacy, transakcja_insiderow,
wyniki_sprzedazowe, skup_akcji, zmiana_zarzadu, compliance, inne

Jeśli nie możesz określić event_type — użyj "inne".
Liczby formatuj czytelnie: zamiast "120 100 000 PLN" pisz "120,1 mln PLN".
```

Obsługa błędów: `except Exception` → `logger.warning(...)`, zwróć `None`.

#### 7. _call_gate() — Gemini hallucination gate

**File**: `src/analyzer.py`

**Intent**: Drugi call Gemini: weryfikuje czy `structured_analysis` jest zgodna z `parsed_content`.
Zwraca `(approved: bool, reason: str | None)` lub `(None, None)` przy błędzie API.

**Contract**: Sygnatura: `_call_gate(parsed_content: str, structured_analysis: str) -> tuple[bool | None, str | None]`

Gate weryfikuje semantyczną równoważność numeryczną — `response_mime_type="application/json"`.

System prompt (dosłownie):
```
Jesteś audytorem analiz finansowych. Weryfikujesz czy analiza komunikatu giełdowego
jest zgodna z jego oryginalną treścią.

Sprawdź:
1. Czy liczby i kwoty w polu key_numbers mają odpowiedniki w oryginalnej treści?
   WAŻNE: Liczby mogą być sformatowane inaczej (np. "120 100 000 PLN" w tekście
   = "120,1 mln PLN" w analizie) — to jest POPRAWNE. Odrzuć tylko jeśli liczba
   z analizy nie ma żadnego odpowiednika w źródle lub rząd wielkości jest wyraźnie
   błędny (np. "120 mln" zamiast "12 mln").
2. Czy company i ticker są zgodne z treścią komunikatu?
3. Czy summary_pl jest spójne z treścią?

Zwróć JSON:
{"approved": true, "reason": null}
lub
{"approved": false, "reason": "krótkie wyjaśnienie co jest niezgodne"}
```

Wiadomość użytkownika zawiera dwie sekcje oznaczone nagłówkami:
`TREŚĆ KOMUNIKATU:` (parsed_content) i `ANALIZA:` (structured_analysis JSON).

Obsługa błędów: `except Exception` lub nieparsowalne JSON → zwróć `(None, None)`.

#### 8. Tabele scoringowe i _compute_score()

**File**: `src/analyzer.py`

**Intent**: Czysta Python funkcja — bez LLM. Oblicza `final_score` z tier + event_type + priority.
Nieznany `event_type` → `"inne"` (score 20).

**Contract**: Sygnatura: `_compute_score(event_type: str | None, ticker: str | None, priority: str | None) -> float`

Stałe na poziomie modułu (nie inline w funkcji):

```python
_TIER1 = {"DGN","ELT","SNT","TOA","VOT","XTB","PAS","KRU","LBW","APT"}
_TIER2 = {"PKO","KGH","PKN","PGE","PZU","CDR","KTY","LPP","DNP","ZAB",
           "PEO","ASB","CBF","DVL","CRI","DEK"}
_TIER3 = {"MDV","ALR","TPE","MBK","ALE","PCO","BDX"}

_TIER_BONUS = {**{t: 40 for t in _TIER1}, **{t: 25 for t in _TIER2}, **{t: 10 for t in _TIER3}}

_EVENT_TYPE_SCORES = {
    "wyniki_finansowe": 100, "upadlosc": 95, "restrukturyzacja": 95,
    "przejecie": 90, "fuzja": 90, "wezwanie": 90,
    "dywidenda": 85, "emisja_akcji": 80, "kontrakt_znaczacy": 75,
    "transakcja_insiderow": 65, "wyniki_sprzedazowe": 60, "skup_akcji": 55,
    "zmiana_zarzadu": 50, "compliance": 20, "inne": 20,
}
```

Formuła: `tier_bonus + event_type_score + priority_bonus`
gdzie `priority_bonus = 20` gdy `priority == "Ważny"`, `0` w pozostałych przypadkach.

#### 9. analyze_announcement() — orchestrator

**File**: `src/analyzer.py`

**Intent**: Główna publiczna funkcja: skip check → analysis call → gate call → score → `AnalysisResult`.
Nigdy nie rzuca — wszystkie błędy kończą się NULL polami i WARNING.

**Contract**: Sygnatura: `analyze_announcement(announcement_id: str, parsed_content: str | None, ticker: str | None, priority: str | None) -> AnalysisResult`

Logika:
1. Jeśli `parsed_content` jest None lub pusty → log INFO "skip", zwróć all-None result
2. `_call_analysis()` → jeśli `None` → log WARNING, zwróć all-None result
3. Sparsuj `event_type` z dict; jeśli nieznany typ → mapuj na `"inne"`
4. Serializuj dict do `structured_analysis` JSON string (`json.dumps`, `ensure_ascii=False`)
5. `_call_gate()` → jeśli zwróci `(None, None)` → log WARNING, zwróć partial result
   (structured_analysis i event_type wypełnione, approved/score NULL)
6. Jeśli `approved=True` → `_compute_score()`, jeśli `False` → `analysis_score=None`
7. Zwróć pełny `AnalysisResult`

### Success Criteria

#### Automated Verification

- Import: `uv run python -c "from src.analyzer import analyze_announcement; print('OK')"`
- BQ schema: `uv run python -c "from db.bigquery import ensure_schema_current; ensure_schema_current()"`
  (nowe kolumny pojawią się w BQ)

#### Manual Verification

- Wywołaj `analyze_announcement()` ręcznie z przykładowym `parsed_content` (string z prawdziwego
  ogłoszenia z BQ): sprawdź że zwraca `AnalysisResult` z wypełnionymi polami
- Sprawdź w logach że oba calle Gemini zakończyły się sukcesem (INFO level)
- Sprawdź że `analysis_score` jest liczbą > 0 dla ogłoszenia z WIG20 i `event_type=wyniki_finansowe`

**Implementation Note**: Po manualnej weryfikacji — przejdź do Phase 2.

---

## Phase 2: main.py integration

### Overview

Integracja `analyze_announcement()` i `save_analysis_result()` w głównej pętli pipeline'u.

### Changes Required

#### 1. Import nowych funkcji

**File**: `main.py`

**Intent**: Dodaj importy `analyze_announcement` i `save_analysis_result`.

**Contract**: Dodaj do bloku importów z `db.bigquery`: `save_analysis_result`.
Dodaj nowy import: `from src.analyzer import analyze_announcement`.

#### 2. Wywołanie analyzera w pętli

**File**: `main.py`

**Intent**: Po `update_parsed_content()` uruchom analizę i zapisz wynik do BQ.

**Contract**: Blok w pętli `for ann in new:` po `update_parsed_content()`:
```python
result = analyze_announcement(ann_id, parsed.parsed_content, parsed.ticker, ann.priority)
save_analysis_result(
    ann_id,
    result.structured_analysis,
    result.analysis_approved,
    result.analysis_reject_reason,
    result.event_type,
    result.analysis_score,
)
```
`save_analysis_result` rzuca `BigQueryError` → propaguje do outer `except BigQueryError: raise`.

#### 3. Aktualizacja log summary

**File**: `main.py`

**Intent**: Zaktualizuj końcowy log aby informował o trzech etapach pipeline'u.

**Contract**: `"Pipeline completed: %d announcements scraped, parsed, and analysed"`.

### Success Criteria

#### Automated Verification

- `uv run python -c "import main"` — brak błędów importu

#### Manual Verification

- Uruchom `uv run python main.py` lokalnie z `.env`
- Sprawdź logi: INFO "Analyzer: ..." dla każdego ogłoszenia (lub "skip" gdy parsed_content=NULL)
- Sprawdź BQ: `SELECT analysis_score, event_type, analysis_approved FROM ... WHERE ...`
  — co najmniej jedno ogłoszenie z wypełnionymi polami

**Implementation Note**: Po weryfikacji BQ — przejdź do Phase 3 (testy).

---

## Phase 3: Unit tests

### Overview

Testy jednostkowe `src/analyzer.py` z mockowaniem Gemini SDK. Wzorzec: `unittest.mock.patch`
jak w `test_parser.py`.

### Changes Required

#### 1. tests/test_analyzer.py — pełny zestaw testów

**File**: `tests/test_analyzer.py` (nowy plik)

**Intent**: 14 testów pokrywających happy path, błędy API, bad JSON, scoring i skip conditions.

**Contract**: Mockowany symbol: `src.analyzer._get_client` (zwraca MagicMock z metodą
`.models.generate_content()`). Alternatywnie patch `google.genai.Client` — dopasuj do
faktycznej ścieżki importu po implementacji.

Testy do zaimplementowania:

| # | Test | Co sprawdza |
|---|------|-------------|
| 1 | `test_skip_no_parsed_content` | `analyze_announcement(..., None, ...)` → all-None result |
| 2 | `test_skip_empty_parsed_content` | `parsed_content=""` → all-None result |
| 3 | `test_gemini_api_error_analysis` | `_call_analysis` raises → all-None result, WARNING logged |
| 4 | `test_gemini_api_error_gate` | analysis OK, gate raises → structured_analysis wypełnione, approved=None |
| 5 | `test_happy_path_approved` | oba calle OK, approved=True → score > 0 |
| 6 | `test_happy_path_rejected` | gate returns approved=False → analysis_score=None, reason wypełniony |
| 7 | `test_unknown_event_type_maps_to_inne` | Gemini zwraca `event_type="nieznany"` → mapowane na `"inne"` |
| 8 | `test_compute_score_tier1` | ticker z T1 (np. "XTB") → tier_bonus=40 |
| 9 | `test_compute_score_tier2` | ticker z T2 (np. "PKO") → tier_bonus=25 |
| 10 | `test_compute_score_tier4_unknown` | nieznany ticker → tier_bonus=0 |
| 11 | `test_compute_score_priority_bonus` | `priority="Ważny"` → +20 do score |
| 12 | `test_compute_score_no_priority` | `priority=None` → priority_bonus=0 |
| 13 | `test_compute_score_wyniki_finansowe` | event_score=100 |
| 14 | `test_compute_score_upadlosc` | event_score=95 |

### Success Criteria

#### Automated Verification

- Wszystkie testy przechodzą: `uv run pytest tests/test_analyzer.py -v`
- Cały suite: `uv run pytest tests/ -v`

#### Manual Verification

- Żaden test nie wykonuje realnego HTTP — weryfikacja przez `--co` (collect only) + brak sieci

---

## Testing Strategy

### Unit Tests

- `tests/test_analyzer.py` — 14 testów, mock Gemini SDK
- `tests/test_scraper.py` — istniejące testy weryfikują `priority` po Phase 0

### Integration Tests

- Manualne uruchomienie `main.py` z `.env` + BQ query po każdej fazie

### Manual Testing Steps

1. Phase 0: ogłoszenie z badge "Ważny" ma `priority="Ważny"` w BQ
2. Phase 1: `analyze_announcement()` na prawdziwym `parsed_content` → poprawny JSON + score
3. Phase 2: pełny loop → BQ ma `analysis_score` i `event_type` dla nowych ogłoszeń
4. Phase 3: `pytest tests/ -v` — 100% pass

## Migration Notes

`ensure_schema_current()` wywoływane przy każdym starcie `main.py` — dodaje brakujące kolumny
automatycznie. Nie wymaga ręcznej ingerencji w BQ.

## References

- change.md: `context/changes/ai-analysis-supervisor/change.md`
- Wzorzec BQ client: `db/bigquery.py:35` (`_get_client`)
- Wzorzec update: `db/bigquery.py:198` (`update_parsed_content`)
- Wzorzec testów: `tests/test_parser.py`
- Scoring spec: `context/changes/ai-analysis-supervisor/change.md` §Phase 3

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 0: Priority badge

#### Automated

- [x] 0.1 Import Announcement — pole priority widoczne w `__dataclass_fields__` — 0b1b27f
- [x] 0.2 `uv run pytest tests/test_scraper.py -v` — wszystkie testy zielone — 0b1b27f
- [x] 0.3 `uv run python -c "import main"` — brak błędów importu — 0b1b27f

#### Manual

- [x] 0.4 BQ ma kolumnę `priority STRING`; ogłoszenia z badge mają wartość, reszta NULL — 0b1b27f

### Phase 1: src/analyzer.py + BQ schema

#### Automated

- [x] 1.1 `uv run python -c "from src.analyzer import analyze_announcement; print('OK')"` — b6ccaaa
- [x] 1.2 `uv run python -c "from db.bigquery import ensure_schema_current; ensure_schema_current()"` — 5 nowych kolumn w BQ — b6ccaaa

#### Manual

- [x] 1.3 Ręczne wywołanie `analyze_announcement()` z prawdziwym `parsed_content` → `AnalysisResult` z wypełnionymi polami — b6ccaaa
- [x] 1.4 `analysis_score > 0` dla spółki T1/T2 z `event_type=wyniki_finansowe` — b6ccaaa

### Phase 2: main.py integration

#### Automated

- [x] 2.1 `uv run python -c "import main"` — brak błędów importu

#### Manual

- [ ] 2.2 Pełny `uv run python main.py` → logi Analyzer dla każdego ogłoszenia
- [ ] 2.3 BQ: nowe ogłoszenia mają `analysis_score` i `event_type` wypełnione

### Phase 3: Unit tests

#### Automated

- [ ] 3.1 `uv run pytest tests/test_analyzer.py -v` — 14/14 passed
- [ ] 3.2 `uv run pytest tests/ -v` — cały suite zielony
