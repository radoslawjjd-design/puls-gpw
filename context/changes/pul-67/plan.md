# ETF/ETC/ETN Quotes Ingestion and Portfolio Integration — Implementation Plan

## Overview

Dodajemy obsługę instrumentów ETF/ETC/ETN notowanych na GPW (np. `ETFBW20TR`, `ETFBS80TR`, `ETCGLDRMAU`, `ETNVIRBTCP`) w systemie puls-gpw. Użytkownik będzie mógł dodać taki instrument do portfela, widzieć go w treemapie, a kalendarz P&L uwzględni jego wycenę dzienną. Źródło danych: `gpw.pl/etfy-pelna-wersja-notowan` (statyczny HTML, BeautifulSoup).

## Current State Analysis

Codebase wycenia pozycje portfela przez `LEFT JOIN company_daily_stats ON ticker` (ROW_NUMBER PARTITION BY ticker). Ten wzorzec jest w dwóch miejscach:
1. `db/bigquery.py:589–636` — `list_user_portfolio_positions()` (portfolio + treemapa)
2. `db/bigquery.py:352–442` — `get_portfolio_calendar_data()` (kalendarz P&L)

Walidacja tickera przy `POST /api/portfolio/positions` (`src/api.py:481–483`) sprawdza tabelę `companies`. ETF-y tam nie istnieją → HTTP 422. Autocomplete pochodzi z `list_distinct_tickers()` (`db/bigquery.py:1666–1678`) → `SELECT ticker FROM companies`.

Scraper `src/bankier_metrics.py` i HTTP client `src/http_client.py` są reużywalne. Wzorzec Cloud Run Job istnieje w `company_stats_main.py` + `.github/workflows/deploy.yml:65–71`.

### Key Discoveries:

- `db/bigquery.py:589` — price JOIN używany zarówno przez portfolio jak i treemapę (treemapa nie wymaga osobnych zmian)
- `db/bigquery.py:352` — kalendarz używa `zmiana_kwotowa` (PLN/unit) z company_daily_stats; to samo pole musi być w `etf_quotes`, wyliczone jako `kurs_odn × zmiana_procentowa / 100`
- `src/api.py:481–483` — walidacja tickera wymaga rozszerzenia `list_distinct_tickers()`
- `static/index.html:1708–1716` — `_resolveCompanyForTicker()` używa `/announcements`; ETF-y nie mają ogłoszeń → potrzebny osobny endpoint dla nazw ETF
- GPW ETF page: tabele nie mają CSS class/id → parsing po nagłówku kolumny "Instrument"
- Lessons: `load_dotenv()` musi być pierwsze w nowym entrypoincie; `_get_client()` już ma quota_project guard

## Desired End State

- Użytkownik dodaje pozycję z tickerem np. `ETFBW20TR` — ticker jest w autocomplete, cross-fill pokazuje nazwę instrumentu
- Pozycja ETF/ETC/ETN jest wyceniana w portfelu (`/api/portfolio/positions`) i treemapie (`/api/portfolio/treemap`) tak samo jak akcja
- Kalendarz P&L (`/api/portfolio/calendar`) uwzględnia ETF-y w dziennym P&L przez pole `zmiana_kwotowa`
- Job `puls-gpw-etf-quotes` działa na tej samej częstotliwości co `puls-gpw-company-stats` i aktualizuje dane w BQ

### Verification:
1. POST /api/portfolio/positions z tickerem `ETFBW20TR` → HTTP 200 (nie 422)
2. GET /api/portfolio/positions → pozycja ETF ma `current_price` (nie null)
3. GET /api/portfolio/treemap → ETF widoczny na treemapie z wyceną
4. GET /api/portfolio/calendar → dni z ETF pozycjami mają pnl_abs ≠ null

## What We're NOT Doing

- Nie obsługujemy surowych indeksów punktowych (WIG, WIG20 jako wartości punktowe) — tylko instrumenty notowane w PLN
- Nie tworzymy BQ VIEW `latest_prices` — używamy COALESCE w 2 query (view to overengineering na tym etapie)
- Nie zmieniamy modeli danych dla `user_portfolio_positions` — ETF działa tak samo jak akcja (ticker + shares + avg_buy_price)
- Nie dodajemy ETF-ów do tabeli `companies` — osobna tabela `etf_instruments`
- Nie budujemy intraday refresh — raz dziennie (ta sama częstotliwość co company-stats)

## Implementation Approach

Podejście addytywne i niedestruktywne:
1. Budujemy nową warstwę danych (BQ tables + scraper + job) niezależnie od istniejących
2. Rozszerzamy istniejące query przez dodanie drugiego LEFT JOIN + COALESCE (nie modyfikujemy logiki biznesowej)
3. Rozszerzamy walidację i autocomplete przez UNION (backward-compatible)
4. CI/CD: nowy job block — nie dotykamy istniejących jobów

## Critical Implementation Details

**Parsing GPW HTML**: tabele na `gpw.pl/etfy-pelna-wersja-notowan` nie mają atrybutów id/class. Parsuj przez `soup.find_all("table")` + sprawdzenie nagłówka (`<th>` lub `<td>`) zawierającego "Instrument". Instrument type (ETF/ETC/ETN) identyfikuj po poprzedzającym `<h2>` lub `<h3>`. Mapping kolumn rób dynamicznie po tekście nagłówka — nie po pozycji (kolejność może się zmienić).

**`—` jako brak danych**: GPW używa `—` (myślnik em) gdy nie było transakcji danego dnia (np. `ETFHANESGO`). Parsuj jako `None` → NULL w BQ. Nie rzucaj wyjątku.

**zmiana_kwotowa derivation**: obliczaj jako `kurs_odn × zmiana_procentowa / 100` gdy oba pola są non-null; inaczej `None`. Ten wzorzec identyczny jak dla spółek (Bankier też podaje `kurs_odn` i `zmiana_procentowa`).

**`load_dotenv()` jako pierwszy import** w `etf_quotes_main.py` — lesson z bigquery.py: `BIGQUERY_DATASET` i `GOOGLE_CLOUD_PROJECT` są czytane przy imporcie modułu db.

---

## Phase 1: BigQuery Layer — nowe tabele i funkcje

### Overview

Tworzymy dwie nowe tabele w BQ (`etf_instruments`, `etf_quotes`), wzorując się na `company_daily_stats`. Rozszerzamy `list_distinct_tickers()` o UNION z `etf_instruments`. Ta faza jest prerequisitem dla wszystkich pozostałych.

### Changes Required:

#### 1. Stałe i referencje tabel

**File**: `db/bigquery.py`

**Intent**: Zadeklaruj nazwy nowych tabel jako stałe modułowe, wzorem `_COMPANY_DAILY_STATS_TABLE_NAME`.

**Contract**: Dwie stałe: `_ETF_INSTRUMENTS_TABLE_NAME = "etf_instruments"` oraz `_ETF_QUOTES_TABLE_NAME = "etf_quotes"` w sekcji stałych modułu (blisko linii 1758).

#### 2. Schema tabeli `etf_instruments`

**File**: `db/bigquery.py`

**Intent**: Zdefiniuj schema BQ dla tabeli master danych ETF/ETC/ETN. Tabela jest mała (~36 wierszy), odświeżana przy każdym scrape.

**Contract**: Schema jako `list[bigquery.SchemaField]`:
- `ticker` STRING REQUIRED
- `name` STRING NULLABLE
- `isin` STRING NULLABLE
- `instrument_type` STRING NULLABLE (wartości: `"ETF"`, `"ETC"`, `"ETN"`)
- `created_at` TIMESTAMP REQUIRED
- `updated_at` TIMESTAMP REQUIRED

Brak partycji. Cluster na `ticker`.

#### 3. Schema tabeli `etf_quotes`

**File**: `db/bigquery.py`

**Intent**: Zdefiniuj schema BQ dla dziennych kwotowań ETF/ETC/ETN. Lustrzana struktura do `company_daily_stats`, z dodatkowym polem `kurs_odn` (potrzebnym do derivacji `zmiana_kwotowa` historycznie).

**Contract**: Schema:
- `ticker` STRING REQUIRED
- `snapshot_date` DATE REQUIRED — klucz partycji (DAY)
- `kurs_zamkniecia` FLOAT64 NULLABLE
- `zmiana_procentowa` FLOAT64 NULLABLE
- `zmiana_kwotowa` FLOAT64 NULLABLE — `kurs_odn × zmiana_procentowa / 100`, wyliczane przy scrape
- `kurs_odn` FLOAT64 NULLABLE
- `kurs_otwarcia` FLOAT64 NULLABLE
- `kurs_min` FLOAT64 NULLABLE
- `kurs_max` FLOAT64 NULLABLE
- `wolumen_skum` FLOAT64 NULLABLE
- `fetched_at` TIMESTAMP REQUIRED

Cluster na `ticker`.

#### 4. Table creation functions

**File**: `db/bigquery.py`

**Intent**: Dwie funkcje tworzące tabele jeśli nie istnieją — wywoływane jako PIERWSZE przed ensure_*. `ensure_schema_current()` robi early return gdy tabela nie istnieje (db/bigquery.py:160–163), więc create_* musi poprzedzać ensure_*.

**Contract**:
- `create_etf_instruments_table_if_not_exists() → None` — tworzy `etf_instruments` z powyższym schema (bez partycji, cluster na ticker)
- `create_etf_quotes_table_if_not_exists() → None` — tworzy `etf_quotes` z partycją `snapshot_date` DAY, cluster na ticker

#### 5. Schema migration wrappers

**File**: `db/bigquery.py`

**Intent**: Dwa wrapperki `ensure_*_schema_current()` używające istniejącego `ensure_schema_current()`. Wywołaj je po create_* przy starcie job'a, analogicznie do `ensure_company_daily_stats_schema_current()`.

**Contract**: 
- `ensure_etf_instruments_schema_current() → None`
- `ensure_etf_quotes_schema_current() → None`

#### 6. `merge_etf_instruments(rows: list[dict]) → None`

**File**: `db/bigquery.py`

**Intent**: MERGE instruments master data do `etf_instruments`. Przy każdym scrape nadpisuje name/isin/instrument_type dla znanych tickerów.

**Contract**: Wzorzec identyczny jak `merge_company_daily_stats(rows: list[dict])` (linia 1870): temp table 24h → MERGE ON `ticker`. MATCHED: UPDATE name, isin, instrument_type, updated_at. NOT MATCHED: INSERT wszystkie pola z `created_at = CURRENT_TIMESTAMP`. Sygnatura `list[dict]` spójna z istniejącym wzorcem (nie `dict[str, dict]`).

#### 7. `merge_etf_quotes(rows: list[dict]) → None`

**File**: `db/bigquery.py`

**Intent**: MERGE dziennych kwotowań ETF do `etf_quotes`. Obsługuje wielokrotne uruchomienia tego samego dnia (idempotent).

**Contract**: Wzorzec identyczny jak `merge_company_daily_stats()`. Sygnatura `list[dict]`. MERGE ON `(ticker, snapshot_date)`. MATCHED: UPDATE kurs_zamkniecia, zmiana_procentowa, zmiana_kwotowa, kurs_odn, kurs_otwarcia, kurs_min, kurs_max, wolumen_skum, fetched_at. NOT MATCHED: INSERT.

#### 8. Rozszerzenie `list_distinct_tickers()`

**File**: `db/bigquery.py:1666–1678`

**Intent**: Dodaj ETF tickery do listy walidacji i autocomplete. UNION DISTINCT gwarantuje brak duplikatów. **Side effect**: funkcja jest wywoływana w 3 miejscach produkcyjnych — `GET /autocomplete/tickers`, `POST /watchlist/{ticker}` (api.py:321) i `POST /api/portfolio/positions` — więc ETF tickery będą też akceptowane w watchliście (pożądany efekt uboczny).

**Contract**: Zmień query na:
```sql
SELECT ticker FROM `{companies}` 
UNION DISTINCT 
SELECT ticker FROM `{etf_instruments}` 
ORDER BY ticker
```

### Success Criteria:

#### Automated Verification:

- `uv run python -c "from db.bigquery import merge_etf_instruments, merge_etf_quotes, list_distinct_tickers; print('OK')"` — importy bez błędów
- Testy jednostkowe passują: `uv run pytest tests/ -x -q`

#### Manual Verification:

- Lokalna weryfikacja round-trip: skrypt testowy tworzy obie tabele w BQ (wywołując ensure_schema_current) bez błędów
- `list_distinct_tickers()` zwraca tickery ETF po seeded test-insert

---

## Phase 2: GPW ETF Scraper

### Overview

Nowy moduł `src/gpw_etf_metrics.py` z funkcją `fetch_etf_page()` parsującą `gpw.pl/etfy-pelna-wersja-notowan`. Reużywa istniejącego HTTP client (`src/http_client.py`) i BeautifulSoup.

### Changes Required:

#### 1. Nowy moduł scraper

**File**: `src/gpw_etf_metrics.py` (nowy plik)

**Intent**: Pobierz i sparsuj stronę GPW z ETF/ETC/ETN. Zwróć dwie struktury danych: master instrumentów i dzienne kwotowania.

**Contract**: 

```python
GPW_ETF_URL = "https://www.gpw.pl/etfy-pelna-wersja-notowan"

def fetch_etf_page(
    snapshot_date: date,
    fetched_at: datetime,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Returns:
        instruments: {ticker: {name, isin, instrument_type, created_at, updated_at}}
        quotes: {ticker: {ticker, snapshot_date, kurs_zamkniecia, zmiana_procentowa,
                          zmiana_kwotowa, kurs_odn, kurs_otwarcia, kurs_min, kurs_max,
                          wolumen_skum, fetched_at}}
    """
```

Logika parsowania:
1. `get(GPW_ETF_URL)` z `src/http_client.py`
2. `BeautifulSoup(response.text, "html.parser")`
3. Iteruj po `soup.find_all("table")` — znajdź tabele z nagłówkiem "Instrument" (column header)
4. Dla każdej tabeli: zidentyfikuj `instrument_type` z poprzedzającego `<h2>`/`<h3>` (szukaj tekstu "ETF"/"ETC"/"ETN")
5. Zbuduj `header_map: dict[str, int]` mapując nagłówek kolumny → indeks
6. Iteruj po wierszach `<tbody><tr>`, parsuj komórki po `header_map`
7. `"—"` lub pusta komórka → `None`
8. `zmiana_kwotowa = kurs_odn × zmiana_procentowa / 100` jeśli oba non-None
9. Agreguj do obu słowników, klucz = ticker

**Mapping nagłówek → pole** (dopasowanie case-insensitive, substring):
- "Instrument" → ticker (i name, bo GPW używa tickera jako nazwy)
- "ISIN" → isin
- "kurs odn" → kurs_odn
- "kurs otw" → kurs_otwarcia
- "kurs min" → kurs_min
- "kurs maks" → kurs_max
- "kurs ost" → kurs_zamkniecia
- "zm.do" lub "zmiana" → zmiana_procentowa
- "obr. skumul" lub "wol. -" → wolumen_skum

#### 2. Parser polskich liczb

**File**: `src/gpw_etf_metrics.py`

**Intent**: GPW używa polskiego formatu liczb (spacja jako separator tysięcy, przecinek jako separator dziesiętny). Reużyj lub zduplikuj `_parse_polish_float()` z `src/bankier_metrics.py:33–45`.

**Contract**: Prywatna funkcja `_parse_float(raw: str) -> float | None` — obsługuje spacje, przecinki, `%`, `zł`, myślnik em (`—`) → None.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/ -x -q -k "etf"` — testy unit scraper (mock HTTP response)
- Linting: `uv run ruff check src/gpw_etf_metrics.py`

#### Manual Verification:

- `uv run python -c "from src.gpw_etf_metrics import fetch_etf_page; from datetime import date, datetime, timezone; i, q = fetch_etf_page(date.today(), datetime.now(timezone.utc)); print(len(i), 'instruments,', len(q), 'quotes')"` → ≥ 30 instrumentów, ≥ 30 kwotowań
- Weryfikacja że `ETFBW20TR` jest w wynikach z poprawnym kurs_zamkniecia (non-None)
- Weryfikacja że instrument z `—` (np. ETFHANESGO) ma kurs_zamkniecia = None (nie rzuca wyjątku)

---

## Phase 3: Cloud Run Job Entrypoint

### Overview

Nowy plik `etf_quotes_main.py` — entry point dla Cloud Run Job `puls-gpw-etf-quotes`. Wzorzec identyczny jak `company_stats_main.py`.

### Changes Required:

#### 1. Nowy entrypoint

**File**: `etf_quotes_main.py` (nowy plik, root projektu)

**Intent**: Orchestrate scrape → merge instruments → merge quotes. Obsłuż błędy gracefully (log + exit 1 jeśli scrape fail).

**Contract**: 

```python
# PIERWSZA linia po imports stdlib: load_dotenv()
from dotenv import load_dotenv
load_dotenv()
# potem importy modułów GCP/BQ
```

Flow:
1. `create_etf_instruments_table_if_not_exists()`
2. `ensure_etf_instruments_schema_current()`
3. `create_etf_quotes_table_if_not_exists()`
4. `ensure_etf_quotes_schema_current()`
5. `snapshot_date = datetime.now(WARSAW).date()`
4. `instruments, quotes = fetch_etf_page(snapshot_date, fetched_at=now)`
5. `merge_etf_instruments(instruments)`
6. `merge_etf_quotes(quotes)`
7. Log: liczba instrumentów i kwotowań

### Success Criteria:

#### Automated Verification:

- Linting: `uv run ruff check etf_quotes_main.py`

#### Manual Verification:

- Lokalne uruchomienie `uv run python etf_quotes_main.py` kończy się bez błędów
- BQ tables `etf_instruments` i `etf_quotes` zawierają dane po uruchomieniu
- Weryfikacja round-trip: `SELECT COUNT(*) FROM etf_instruments` ≥ 30, `SELECT COUNT(*) FROM etf_quotes WHERE snapshot_date = CURRENT_DATE` ≥ 30

---

## Phase 4: Portfolio + Treemap — price resolution

### Overview

Rozszerzamy `list_user_portfolio_positions()` o drugi CTE (`latest_etf`) i COALESCE na kurs/zmiana. Treemapa (`compute_user_portfolio_treemap_positions()`) używa wyników tej funkcji — nie wymaga osobnych zmian.

### Changes Required:

#### 1. Rozszerzenie `list_user_portfolio_positions()`

**File**: `db/bigquery.py:589–636`

**Intent**: Dodaj drugi CTE łączący etf_quotes (ta sama struktura ROW_NUMBER co latest_stats). Zastąp bezpośrednie referencje do `ls.*` przez `COALESCE(ls.*, etf.*)`.

**Contract**: Dodaj CTE przed głównym SELECT:

```sql
latest_etf AS (
  SELECT ticker, kurs_zamkniecia, zmiana_procentowa,
         CAST(snapshot_date AS STRING) AS price_as_of,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `{_table_ref(client, _ETF_QUOTES_TABLE_NAME)}`
)
```

W głównym SELECT dodaj drugi LEFT JOIN:
```sql
LEFT JOIN latest_etf etf ON p.ticker = etf.ticker AND etf.rn = 1
```

Zmień SELECT kolumn cenowych:
```sql
COALESCE(ls.kurs_zamkniecia,   etf.kurs_zamkniecia)   AS current_price,
COALESCE(ls.zmiana_procentowa, etf.zmiana_procentowa) AS daily_change_pct,
COALESCE(ls.price_as_of,       etf.price_as_of)       AS price_as_of
```

### Success Criteria:

#### Automated Verification:

- Testy jednostkowe passują: `uv run pytest tests/ -x -q`
- `uv run python -c "from db.bigquery import list_user_portfolio_positions; print('OK')"` — import bez błędów

#### Manual Verification:

- `GET /api/portfolio/positions` dla użytkownika z ETF pozycją (seeded testowo w BQ) → `current_price` non-null
- `GET /api/portfolio/treemap` → ETF widoczny na treemapie z wyceną (nie z `.no-data` class)
- Brak regresji: pozycje spółek nadal mają poprawne ceny

---

## Phase 5: Kalendarz P&L — price resolution

### Overview

Rozszerzamy `get_portfolio_calendar_data()` o obsługę ETF w CTE `daily_prices`. Logika compute_calendar_pnl() nie zmienia się — jest niezależna od źródła danych.

### Changes Required:

#### 1. Rozszerzenie CTE `daily_prices` w `get_portfolio_calendar_data()`

**File**: `db/bigquery.py:352–442`

**Intent**: Dodaj LEFT JOIN do etf_quotes w CTE daily_prices. COALESCE zmiana_kwotowa i kurs_zamkniecia z obu tabel.

**Contract**: W CTE `daily_prices` (linia ~393–404) dodaj drugi LEFT JOIN:
```sql
LEFT JOIN `{_table_ref(client, _ETF_QUOTES_TABLE_NAME)}` etq
  ON pos.ticker = etq.ticker AND etq.snapshot_date = td.snapshot_date
```

W SELECT tej CTE zastąp bezpośrednie referencje:
```sql
COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia) AS kurs_zamkniecia,
COALESCE(cds.zmiana_kwotowa,  etq.zmiana_kwotowa)  AS zmiana_kwotowa,
```

Zmienna `cds` to alias dla istniejącego LEFT JOIN company_daily_stats (dodaj alias jeśli go nie ma).

### Success Criteria:

#### Automated Verification:

- Testy jednostkowe passują: `uv run pytest tests/ -x -q`

#### Manual Verification:

- `GET /api/portfolio/calendar?year=2026&month=6&portfolio_id=<id>` dla portfela z ETF pozycją → dni z ETF mają `pnl_abs` non-null (state "data", nie "partial")
- Brak regresji: kalendarz dla portfela tylko ze spółkami działa identycznie jak poprzednio

---

## Phase 6: Autocomplete + Ticker Validation

### Overview

`list_distinct_tickers()` jest już rozszerzone (Phase 1). Dodajemy endpoint do rozwiązywania nazw ETF (dla cross-fill w formularzu) i rozszerzamy frontend.

### Changes Required:

#### 1. Nowy endpoint `GET /autocomplete/etf-instruments`

**File**: `src/api.py`

**Intent**: Zwróć słownik `{ticker: name}` dla wszystkich instrumentów w `etf_instruments`. Używany przez frontend do cross-fill nazwy przy dodawaniu pozycji ETF.

**Contract**: 
```python
@app.get("/autocomplete/etf-instruments")
async def autocomplete_etf_instruments(role: Role = Depends(_get_role)):
    # cache 5 min (wzorzec _AC_CACHE jak inne autocomplete endpoints)
    return {"instruments": list_etf_instruments_for_autocomplete()}
```

Odpowiedź: `{"instruments": [{"ticker": "ETFBW20TR", "name": "ETFBW20TR", "type": "ETF"}, ...]}`

> **Uwaga**: GPW w kolumnie "Nazwa pełna" zwraca ten sam string co ticker (np. "ETFBW20TR"). Cross-fill w formularzu pokaże ticker w polu company_name — jest to oczekiwane zachowanie. Nie szukaj zewnętrznego źródła czytelnych nazw — nie jest to wymagane w tym change.

#### 2. `list_etf_instruments_for_autocomplete()` w BQ

**File**: `db/bigquery.py`

**Intent**: Query zwracające listę {ticker, name, instrument_type} z `etf_instruments`, posortowaną po ticker.

**Contract**: `SELECT ticker, name, instrument_type FROM etf_instruments ORDER BY ticker`

#### 3. Frontend — extend `_resolveCompanyForTicker()`

**File**: `static/index.html`

**Intent**: Załaduj ETF instruments dict przy inicjalizacji; gdy user wybierze ticker z `_etfInstrumentsMap`, użyj ETF name zamiast szukać w `/announcements`.

**Contract**: 

Przy inicjalizacji (np. w `initPortfolioView()` lub przy pierwszym otwarciu formularza):
```javascript
let _etfInstrumentsMap = {};  // {ticker: {name, type}}
async function _loadEtfInstruments() {
    const data = await fetch('/autocomplete/etf-instruments').then(r => r.json());
    _etfInstrumentsMap = Object.fromEntries(data.instruments.map(i => [i.ticker, i]));
}
```

W `_resolveCompanyForTicker(ticker)`: jeśli `ticker in _etfInstrumentsMap`, zwróć `_etfInstrumentsMap[ticker].name` (bez wywołania `/announcements`).

Extend `_acTickers` (lista do autocomplete `pp-ticker-input`) o tickery ETF po załadowaniu `_etfInstrumentsMap`.

### Success Criteria:

#### Automated Verification:

- `GET /autocomplete/etf-instruments` → HTTP 200, odpowiedź zawiera `ETFBW20TR`
- Testy unit API passują: `uv run pytest tests/ -x -q`
- Zaktualizowane mocki w `tests/test_api.py`: testy POST /api/portfolio/positions z ETF tickerem mają `return_value=["PKO", "CDR", "ETFBW20TR"]` w patch `list_distinct_tickers`
- Zaktualizowany docstring `tests/test_bigquery.py:668`: "must read from companies and etf_instruments" (nie tylko companies)

#### Manual Verification:

- W formularzu "Dodaj pozycję": wpisanie `ETFB` wyświetla ETF tickery w autocomplete
- Wybranie `ETFBW20TR` → company_name auto-fills wartością "ETFBW20TR" (ticker = name w GPW)
- `POST /api/portfolio/positions` z `ticker: "ETFBW20TR"` → HTTP 200 (nie 422)
- Brak regresji: autocomplete spółek (np. `PKN`) nadal działa

---

## Phase 7: CI/CD

### Overview

Dodajemy Cloud Run Job `puls-gpw-etf-quotes` do pipeline'u CI/CD. Nie modyfikujemy istniejących jobów.

### Changes Required:

#### 1. Nowy job w GitHub Actions deploy workflow

**File**: `.github/workflows/deploy.yml`

**Intent**: Dodaj blok update dla `puls-gpw-etf-quotes` wzorowany na `puls-gpw-company-stats` (linie 65–71). Nowy job używa tego samego service account, regionu i config.

**Contract**: Nowy krok w workflow po `puls-gpw-company-stats`:
```yaml
- name: Deploy ETF quotes job
  run: |
    gcloud run jobs update puls-gpw-etf-quotes \
      --command=uv --args="run,--no-dev,python,etf_quotes_main.py" \
      --region=europe-central2 \
      --project=${{ secrets.GCP_PROJECT_ID }}
```

Job musi być wcześniej utworzony ręcznie w GCP (lub przez `gcloud run jobs create` jako krok jednorazowy przez człowieka). Workflow robi tylko `update`, nie `create`.

**Uwaga dla deploymentu**: Cloud Scheduler trigger dla nowego joba musi być skonfigurowany manualnie (human-only action per CLAUDE.md) — ta sama częstotliwość co `puls-gpw-company-stats`.

### Success Criteria:

#### Automated Verification:

- CI/CD pipeline przebiega bez błędów (brak syntax errors w YAML)
- `gcloud run jobs describe puls-gpw-etf-quotes --region=europe-central2` — job istnieje po deploy

#### Manual Verification:

- Ręczne uruchomienie joba w GCP Console → kończy się bez błędów
- BQ tables mają dane po uruchomieniu joba w GCP (nie tylko lokalnie)
- Job pojawia się na liście Cloud Run Jobs w konsoli GCP

---

## Testing Strategy

### Unit Tests:

- `tests/test_gpw_etf_metrics.py` — mock HTTP response z zapisanym HTML fixture; sprawdź parsing 3 sekcji (ETF/ETC/ETN), parsowanie `—` → None, derivację zmiana_kwotowa
- `tests/test_bigquery_etf.py` — mock BQ client; sprawdź merge_etf_instruments i merge_etf_quotes (wzorzec jak istniejące testy BQ)
- `tests/test_api_etf_autocomplete.py` — mock list_etf_instruments_for_autocomplete; sprawdź `/autocomplete/etf-instruments` response schema

### Integration Tests:

- Extend `tests/test_portfolio_positions.py`: dodaj test że pozycja ETF (seeded w mock BQ) zwraca current_price non-null
- Extend `tests/test_portfolio_calendar.py` (jeśli istnieje): portfel z ETF pozycją ma pnl_abs w dni sesyjne

### Manual Testing Steps:

1. Seed test ETF pozycję: `POST /api/portfolio/positions` z `{"ticker": "ETFBW20TR", ...}` → HTTP 200
2. `GET /api/portfolio/positions` → pozycja widoczna z current_price (może być null jeśli BQ nie ma danych — to OK na tym etapie)
3. Uruchom `etf_quotes_main.py` lokalnie → BQ `etf_quotes` zawiera dane
4. `GET /api/portfolio/positions` po zasileniu BQ → current_price non-null dla ETFBW20TR
5. `GET /api/portfolio/treemap` → ETF widoczny w treemapie
6. `GET /api/portfolio/calendar?year=2026&month=6&portfolio_id=<id>` → dni mają pnl_abs non-null
7. Weryfikacja że stare pozycje (PKN, CDR itd.) nie mają regresji we wszystkich 3 widokach

## Performance Considerations

Tabela `etf_quotes` jest mała (~36 wierszy × liczba dni). COALESCE z drugim CTE dodaje jeden scan tej tabeli per query — pomijalne. Autocomplete ETF response jest cache'owany 5 minut (ten sam wzorzec co companies).

## Migration Notes

Obie nowe tabele BQ są tworzone przez `ensure_schema_current()` przy pierwszym uruchomieniu joba — brak manualnej migracji. Istniejące dane w `user_portfolio_positions` i `company_daily_stats` nie są modyfikowane.

**Pre-requisite dla CI/CD (human action)**: Przed merge, Radek musi ręcznie utworzyć Cloud Run Job `puls-gpw-etf-quotes` w GCP i Cloud Scheduler trigger z tą samą częstotliwością co `puls-gpw-company-stats`.

**Bootstrap po deploy**: Po pierwszym merge i deploy `etf_instruments` tabela jest pusta — `list_distinct_tickers()` UNION nie zwraca ETF tickerów, więc użytkownicy nadal dostają HTTP 422. Uruchom job `puls-gpw-etf-quotes` raz manualnie z GCP Console (Run job) zanim zaczniesz testować feature.

## References

- Related research: `context/changes/pul-67/research.md`
- Pattern reference — entrypoint: `company_stats_main.py:1–106`
- Pattern reference — MERGE: `db/bigquery.py:1870–1943`
- Pattern reference — CI/CD job: `.github/workflows/deploy.yml:65–71`
- Pattern reference — autocomplete endpoint: `src/api.py` (existing `/autocomplete/tickers`)
- Pattern reference — scraper: `src/bankier_metrics.py:57–113`

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery Layer — nowe tabele i funkcje

#### Automated

- [x] 1.1 Import modułów BQ bez błędów (`merge_etf_instruments`, `merge_etf_quotes`, `list_distinct_tickers`) — 7a644eb
- [x] 1.2 Testy jednostkowe passują: `uv run pytest tests/ -x -q` — 7a644eb

#### Manual

- [x] 1.3 Round-trip: create_etf_*_table_if_not_exists() tworzy obie tabele w BQ bez błędów — 7a644eb
- [x] 1.4 `list_distinct_tickers()` zwraca tickery ETF po seeded test-insert — 7a644eb

### Phase 2: GPW ETF Scraper

#### Automated

- [x] 2.1 Testy unit scraper passują: `uv run pytest tests/ -x -q -k "etf"` — 4b107b3
- [x] 2.2 Linting: `uv run ruff check src/gpw_etf_metrics.py` — 4b107b3

#### Manual

- [x] 2.3 Lokalne uruchomienie `fetch_etf_page()` → ≥ 30 instrumentów, ≥ 30 kwotowań
- [x] 2.4 ETFBW20TR w wynikach z kurs_zamkniecia non-None
- [x] 2.5 Instrument z `—` (ETFHANESGO) → kurs_zamkniecia = None, bez wyjątku

### Phase 3: Cloud Run Job Entrypoint

#### Automated

- [x] 3.1 Linting: `uv run ruff check etf_quotes_main.py` — 18ba153

#### Manual

- [x] 3.2 Lokalne `uv run python etf_quotes_main.py` kończy się bez błędów — 18ba153
- [x] 3.3 BQ `etf_instruments` i `etf_quotes` mają dane po uruchomieniu — 18ba153

### Phase 4: Portfolio + Treemap — price resolution

#### Automated

- [x] 4.1 Testy jednostkowe passują: `uv run pytest tests/ -x -q` — bb60bde
- [x] 4.2 Import `list_user_portfolio_positions` bez błędów — bb60bde

#### Manual

- [x] 4.3 GET /api/portfolio/positions z ETF pozycją → `current_price` non-null
- [x] 4.4 GET /api/portfolio/treemap → ETF widoczny z wyceną
- [x] 4.5 Brak regresji: pozycje spółek mają poprawne ceny

### Phase 5: Kalendarz P&L — price resolution

#### Automated

- [x] 5.1 Testy jednostkowe passują: `uv run pytest tests/ -x -q` — 57f9329

#### Manual

- [x] 5.2 GET /api/portfolio/calendar → dni z ETF mają pnl_abs non-null
- [x] 5.3 Brak regresji: kalendarz dla portfela tylko ze spółkami działa identycznie

### Phase 6: Autocomplete + Ticker Validation

#### Automated

- [x] 6.1 GET /autocomplete/etf-instruments → HTTP 200, zawiera ETFBW20TR — 518f55e
- [x] 6.2 Testy unit API passują: `uv run pytest tests/ -x -q` — 518f55e
- [x] 6.3 Mocki `list_distinct_tickers` w test_api.py zawierają ETF ticker dla testów POST /api/portfolio/positions — 518f55e

#### Manual

- [x] 6.4 Autocomplete w formularzu pokazuje ETF tickery po wpisaniu `ETFB`
- [x] 6.5 POST /api/portfolio/positions z ETFBW20TR → HTTP 200
- [x] 6.6 Brak regresji: autocomplete spółek nadal działa

### Phase 7: CI/CD

#### Automated

- [x] 7.1 CI/CD pipeline bez błędów YAML syntax
- [ ] 7.2 `gcloud run jobs describe puls-gpw-etf-quotes` → job istnieje po deploy

#### Manual

- [ ] 7.3 Ręczne uruchomienie joba w GCP Console → sukces
- [ ] 7.4 BQ tables mają dane po uruchomieniu joba w GCP
