# F-02: BigQuery Schema `announcements` + Python Client — Implementation Plan

## Overview

Tworzymy tabelę `announcements` w BigQuery (dataset `espi_ebi`, region `europe-central2`), dodajemy `google-cloud-bigquery` do zależności projektu i implementujemy wrapper read/write w `db/bigquery.py`. Foundation odblokuje S-01 (dedup check) i S-03 (zapis analizy).

## Current State Analysis

- Dataset `espi_ebi` w `europe-central2` **istnieje** (ręcznie stworzony wcześniej)
- `GOOGLE_CLOUD_PROJECT` i `BIGQUERY_DATASET` skonfigurowane w `.env.example`
- `google-cloud-bigquery` **brak** w `pyproject.toml`
- Brak folderu `db/`, brak żadnych wrapperów
- Projekt flat: `main.py` w rocie, bez `src/`
- Auth: Cloud Run używa IAM service account automatycznie; lokalnie ADC (`gcloud auth application-default login`)

## Desired End State

Tabela `announcements` istnieje w BQ z pełnym schematem. `db/bigquery.py` eksportuje trzy funkcje: `is_processed()`, `insert_announcement()`, `save_analysis()`. `main.py` wywołuje `create_table_if_not_exists()` przy starcie. Skrypt `scripts/test_bq.py` potwierdza że end-to-end round-trip działa.

### Key Discoveries

- `google-cloud-bigquery` używa ADC automatycznie — zero konfiguracji auth w kodzie (działa zarówno lokalnie jak i w Cloud Run IAM)
- BQ nie wymusza PK — dedup implementowany przez `SELECT WHERE announcement_id = ?`; UPDATE przez DML `UPDATE SET ... WHERE announcement_id = ?`
- `uv sync --frozen` w Dockerfile wymaga aktualizacji `uv.lock` po dodaniu zależności

## What We're NOT Doing

- Migracje (tabela tworzona raz, schemat projektowany na gotowo)
- Testy jednostkowe z mockiem BQ (moduł 3 kursu)
- Obsługa wielu tabel — tylko `announcements`
- Partycjonowanie / clustering tabeli (overkill dla MVP)
- Terraform ani bq CLI jako narzędzie tworzenia tabeli

## Implementation Approach

Trzy fazy: (1) zależność + moduł z definicją schematu, (2) implementacja wrapperów, (3) integracja z `main.py` i skrypt testowy.

---

## Phase 1: Dependency + Schema Definition

### Overview

Dodaj `google-cloud-bigquery` do `pyproject.toml`, utwórz `db/bigquery.py` z definicją schematu i funkcją `create_table_if_not_exists()`.

### Changes Required

#### 1. pyproject.toml — dodaj dependency

**File**: `pyproject.toml`

**Intent**: Dodaj `google-cloud-bigquery` do sekcji `[project] dependencies`. Uruchom `uv sync` po edycji aby zaktualizować `uv.lock`.

**Contract**: `google-cloud-bigquery>=3.25` w `dependencies`. Po `uv sync` plik `uv.lock` musi się zaktualizować.

#### 2. db/__init__.py — utwórz pakiet

**File**: `db/__init__.py`

**Intent**: Pusty plik inicjalizujący pakiet `db`.

**Contract**: Plik pusty lub z jednolinijkowym komentarzem.

#### 3. db/bigquery.py — klient + schemat + create_table_if_not_exists

**File**: `db/bigquery.py`

**Intent**: Moduł BigQuery pipeline'u. Definiuje schemat tabeli `announcements`, inicjalizuje klienta BQ z ADC i eksportuje funkcję `create_table_if_not_exists()`.

**Contract**:
- Klient: `google.cloud.bigquery.Client()` — tworzy raz na poziomie modułu; odczytuje projekt z `GOOGLE_CLOUD_PROJECT` env var (fallback: `client.project` z ADC); dataset z `BIGQUERY_DATASET` (default: `espi_ebi`).
- Schemat tabeli (10 pól):

| Pole | Typ BQ | Mode | Opis |
|---|---|---|---|
| `announcement_id` | STRING | REQUIRED | SHA-256 hex z `bankier_url` |
| `url` | STRING | REQUIRED | `bankier_url` — pełny URL ogłoszenia |
| `published_at` | TIMESTAMP | REQUIRED | Data i czas publikacji ogłoszenia |
| `title` | STRING | REQUIRED | Tytuł ogłoszenia |
| `company` | STRING | NULLABLE | Nazwa spółki |
| `ticker` | STRING | NULLABLE | Symbol giełdowy |
| `post_text` | STRING | NULLABLE | Wygenerowany X-style post (NULL do S-03) |
| `processed_at` | TIMESTAMP | NULLABLE | Czas przetworzenia przez pipeline |
| `supervisor_attempts` | INTEGER | NULLABLE | Liczba prób supervisora (NULL do S-03) |
| `analysis_type` | STRING | NULLABLE | `FINANCIAL` lub `CORPORATE` (NULL do S-03) |

- `create_table_if_not_exists()`: sprawdza czy tabela istnieje przez `client.get_table()` (wyjątek `NotFound` = nie istnieje), tworzy przez `client.create_table()` z powyższym schematem. Idempotentna.
- `announcement_id` generowany przez `hashlib.sha256(url.encode()).hexdigest()`.

### Success Criteria

#### Automated Verification

- `uv sync` kończy się kodem 0 i aktualizuje `uv.lock`
- `uv run python -c "from db.bigquery import create_table_if_not_exists; print('OK')"` kończy się kodem 0

#### Manual Verification

- `uv.lock` zawiera `google-cloud-bigquery` i jego transitive deps
- Moduł importuje się bez błędów

---

## Phase 2: Wrapper Functions

### Overview

Implementacja trzech funkcji operacyjnych: `is_processed()`, `insert_announcement()`, `save_analysis()`.

### Changes Required

#### 1. db/bigquery.py — dodaj trzy funkcje wrappera

**File**: `db/bigquery.py`

**Intent**: Dodaj funkcje operacyjne używane przez S-01 i S-03. Każda funkcja używa modułowego klienta BQ.

**Contract**:

`is_processed(url: str) -> bool`
- Oblicza `announcement_id = sha256(url)`
- Wykonuje `SELECT COUNT(*) FROM {table} WHERE announcement_id = @id` (parametrized query)
- Zwraca `True` jeśli COUNT > 0

`insert_announcement(url: str, published_at: datetime, title: str, company: str | None, ticker: str | None) -> str`
- Oblicza `announcement_id`, wstawia wiersz przez DML: `client.query("INSERT INTO {table} VALUES (@id, @url, ...)")` z parametrami
- DML INSERT zamiast streaming insert — zapewnia natychmiastową spójność z DML UPDATE/DELETE (streaming buffer blokowałby późniejsze UPDATE w save_analysis i DELETE w test_bq.py)
- Pola `post_text`, `processed_at`, `supervisor_attempts`, `analysis_type` = `NULL`
- Zwraca `announcement_id`
- Rzuca `RuntimeError` jeśli job query zwróci błąd

`save_analysis(announcement_id: str, post_text: str, analysis_type: str, supervisor_attempts: int) -> None`
- Wykonuje DML `UPDATE {table} SET post_text=@pt, analysis_type=@at, supervisor_attempts=@sa, processed_at=CURRENT_TIMESTAMP() WHERE announcement_id=@id`
- Rzuca `ValueError` jeśli `analysis_type` nie jest `"FINANCIAL"` ani `"CORPORATE"`

### Success Criteria

#### Automated Verification

- `uv run python -c "from db.bigquery import is_processed, insert_announcement, save_analysis; print('OK')"` kończy się kodem 0

#### Manual Verification

- Podgląd kodu: każda funkcja ma jasny docstring z typami argumentów i wartością zwracaną

---

## Phase 3: Integration + Test Script

### Overview

Wywołaj `create_table_if_not_exists()` w `main.py`. Napisz skrypt `scripts/test_bq.py` weryfikujący pełny round-trip z prawdziwym BigQuery.

### Changes Required

#### 1. main.py — wywołanie create_table_if_not_exists

**File**: `main.py`

**Intent**: Przy każdym starcie pipeline'u upewnij się że tabela istnieje. Funkcja jest idempotentna — w Cloud Run wywołanie to kosztuje jedno API call do BQ.

**Contract**: `from db.bigquery import create_table_if_not_exists` + wywołanie w `main()` przed logiką pipeline'u. Obecny stub `print("Hello from test-projekt!")` zostaje jako placeholder logiki.

#### 2. scripts/test_bq.py — skrypt weryfikacyjny

**File**: `scripts/test_bq.py`

**Intent**: Standalone skrypt potwierdzający że wrapper działa end-to-end z prawdziwym BigQuery. Spójny ze stylem `scripts/research/` z F-01.

**Contract**: Sekwencja kroków:
1. `create_table_if_not_exists()` — wypisz czy tabela istniała czy została stworzona
2. `insert_announcement(url=TEST_URL, ...)` — wypisz zwrócony `announcement_id`
3. `is_processed(TEST_URL)` → musi zwrócić `True`; wypisz wynik
4. `save_analysis(announcement_id, post_text="Test post", analysis_type="FINANCIAL", supervisor_attempts=1)`
5. Odczytaj rekord przez `client.query(SELECT * WHERE announcement_id=...)` i wypisz go
6. Usuń rekord testowy przez `client.query(DELETE WHERE announcement_id=...)` — sprząta po sobie

`TEST_URL = "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/test-bq-integration-F02"`

### Success Criteria

#### Automated Verification

- `uv run python main.py` kończy się kodem 0

#### Manual Verification

- `uv run scripts/test_bq.py` kończy się kodem 0 i wypisuje wszystkie 5 kroków bez błędów
- W Cloud Console (BigQuery UI) tabela `espi_ebi.announcements` widoczna ze schematem 10 pól
- Po uruchomieniu skryptu testowego rekord testowy **nie istnieje** (sprzątanie)

---

## Testing Strategy

### Manual Testing Steps

1. `gcloud auth application-default login` (jednorazowo, jeśli jeszcze nie zrobione)
2. `uv sync` — weryfikacja zależności
3. `uv run python -c "from db.bigquery import create_table_if_not_exists; create_table_if_not_exists(); print('Table OK')"` — test tworzenia tabeli
4. `uv run scripts/test_bq.py` — pełny round-trip
5. Sprawdź tabelę w BigQuery Console: `espi_ebi.announcements`, schema 10 pól

## References

- Roadmap F-02: `context/foundation/roadmap.md` §F-02
- Infrastructure: `context/foundation/infrastructure.md`
- `.env.example` — wymagane env vars
- BigQuery Python client docs: https://cloud.google.com/python/docs/reference/bigquery/latest

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Dependency + Schema Definition

#### Automated

- [x] 1.1 `uv sync` kończy się kodem 0 i aktualizuje `uv.lock`
- [x] 1.2 `uv run python -c "from db.bigquery import create_table_if_not_exists; print('OK')"` kończy się kodem 0

#### Manual

- [ ] 1.3 `uv.lock` zawiera `google-cloud-bigquery` i jego transitive deps
- [ ] 1.4 Moduł importuje się bez błędów

### Phase 2: Wrapper Functions

#### Automated

- [x] 2.1 `uv run python -c "from db.bigquery import is_processed, insert_announcement, save_analysis; print('OK')"` kończy się kodem 0

#### Manual

- [x] 2.2 Każda funkcja ma jasny docstring z typami i wartością zwracaną

### Phase 3: Integration + Test Script

#### Automated

- [ ] 3.1 `uv run python main.py` kończy się kodem 0

#### Manual

- [ ] 3.2 `uv run scripts/test_bq.py` kończy się kodem 0 i wypisuje wszystkie 5 kroków
- [ ] 3.3 Tabela `espi_ebi.announcements` widoczna w BigQuery Console ze schematem 10 pól
- [ ] 3.4 Rekord testowy nie istnieje po zakończeniu skryptu (sprzątanie)
