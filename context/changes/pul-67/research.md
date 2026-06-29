---
date: 2026-06-29T12:00:00+02:00
researcher: Radosław Jankowski
git_commit: 2e24075
branch: pul-67-wig-index-quotes-ingestion
repository: puls-gpw
topic: "WIG index quotes ingestion and portfolio integration"
tags: [research, codebase, portfolio, treemap, calendar, scheduler, bigquery, bankier]
status: complete
last_updated: 2026-06-29
last_updated_by: Radosław Jankowski
---

# Research: WIG Index Quotes Ingestion and Portfolio Integration

**Date**: 2026-06-29  
**Git Commit**: 2e24075  
**Branch**: pul-67-wig-index-quotes-ingestion  
**Repository**: puls-gpw

## Research Question

Jak zintegrować indeksy GPW (WIG, WIG20, mWIG40, sWIG80) z istniejącym systemem?  
Użytkownik musi móc dodać indeks do portfela; indeksy muszą być widoczne w treemapie, uwzględniane w kalendarzu P&L i wyceniane na bieżąco ze scraperów Bankier.

## Summary

Codebase używa jednego wzorca cenowego wszędzie: `LEFT JOIN company_daily_stats ON ticker` z `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC)`. Wzorzec ten jest skopiowany w 3 miejscach (portfolio positions, treemap, calendar). Nie istnieje żaden wspólny helper `price_lookup()`.

Kluczowe ograniczenia implementacji:
1. **Walidacja tickera** przy dodawaniu pozycji (`POST /api/portfolio/positions`) odpytuje tabelę `companies` — indeksy tam nie istnieją, więc zostaną odrzucone z HTTP 422.
2. **Kalendarz P&L** używa `zmiana_kwotowa` (dzienna zmiana w PLN na jednostkę) z `company_daily_stats` — nowa tabela `index_quotes` musi dostarczyć analogiczne pole.
3. **Scraper Bankier** już obsługuje tabele notowań (m-quotes-data-table) — URL dla indeksów to `bankier.pl/gielda/notowania/indeksy-gpw`, ta sama klasa HTML.
4. **Wzorzec Cloud Run Job** jest gotowy — wystarczy nowy entrypoint `index_quotes_main.py` + nowy job w CI/CD.

---

## Detailed Findings

### 1. Portfolio — price resolution

**Główna funkcja:** `db/bigquery.py:589–636` — `list_user_portfolio_positions()`

```sql
WITH latest_stats AS (
  SELECT ticker, kurs_zamkniecia, zmiana_procentowa,
         CAST(snapshot_date AS STRING) AS price_as_of,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `company_daily_stats`
)
SELECT p.*, ls.kurs_zamkniecia AS current_price,
       ls.zmiana_procentowa AS daily_change_pct, ls.price_as_of
FROM `user_portfolio_positions` p
LEFT JOIN latest_stats ls ON p.ticker = ls.ticker AND ls.rn = 1
WHERE p.user_id = @user_id
```

- Brak price'a → pola `current_price`, `daily_change_pct` = NULL → UI wyświetla „—"
- P&L wyliczane w handlerze API: `src/api.py:450–462` (None jeśli brak ceny)
- Modele: `PortfolioPositionIn` (`src/api.py:182–188`), `PortfolioPositionOut` (`src/api.py:191–201`)
- **Brak helpers `price_lookup()`** — logika wbudowana bezpośrednio w SQL

### 2. Treemap — valuation pipeline

**Endpointy:**
- Admin: `src/api.py:390` — `GET /admin/portfolio/treemap`
- User: `src/api.py:573` — `GET /api/portfolio/treemap`

**Wycena:** `src/portfolio_treemap.py:4–65` — `compute_user_portfolio_treemap_positions()`
- Ten sam LEFT JOIN co portfolio (ten sam wynik `list_user_portfolio_positions()`)
- `position_value_pln = shares * current_price` (line 30)
- `daily_change_pln` wyliczany z pct (line 36)
- `since_purchase_pct/pln` z `avg_buy_price` (lines 41–46)

**Model odpowiedzi:** `TreemapPosition` — `src/api.py:134–142`

```python
class TreemapPosition(BaseModel):
    ticker: str
    position_value_pln: float | None = None
    daily_change_pln: float | None = None
    daily_change_pct: float | None = None
    portfolio_share_pct: float | None = None
    since_purchase_pct: float | None = None
    since_purchase_pln: float | None = None
```

**Frontend:**
- Fetch: `static/index.html:2493–2506` — `fetchPortfolioTreemap()`
- Render: `static/index.html:2650–2695` — `renderTreemap(data, container)`
- Layout: `static/js/treemap-layout.js:21–77` — squarified algorithm
- Brak ceny → CSS klasa `.no-data` (dashed border, neutral tło)

### 3. P&L Calendar — data flow

**Endpoint:** `src/api.py:620` — `GET /api/portfolio/calendar?year=&month=&portfolio_id=`

**BQ Query:** `db/bigquery.py:352–442` — `get_portfolio_calendar_data()`, 4 CTE:

| CTE | Źródło | Cel |
|-----|--------|-----|
| `trading_days` | `company_daily_stats` | Distinct daty sesyjne w miesiącu ± 35 dni |
| `positions` | `user_portfolio_positions` | Aktualne pozycje użytkownika |
| `daily_prices` | CROSS JOIN positions × trading_days + LEFT JOIN `company_daily_stats` | Cena zamknięcia i `zmiana_kwotowa` per ticker per dzień |
| `daily_portfolio` | GROUP BY snapshot_date | Suma wartości i dzienna zmiana PLN |

**Kluczowe pole kalendarza:** `zmiana_kwotowa` (dzienna zmiana w PLN na jednostkę), nie różnica cen.
- Codzienny P&L = `SUM(shares × zmiana_kwotowa)` — `db/bigquery.py:399–410`
- Dni bez danych: state = "partial", pnl_abs = None

**Modele:** `PortfolioCalendarDay` (`src/api.py:148`), `PortfolioCalendarResponse` (`src/api.py:160`)

**Compute:** `src/portfolio_calendar.py:26–115` — czysta Python logika, niezależna od źródła danych  
**Święta:** hardcoded list `_GPW_HOLIDAYS` — `src/portfolio_calendar.py:10–23`

**Implikacja dla indeksów:** CTE `trading_days` jest wyprowadzane z `company_daily_stats` — indeksy sesjonują w te same dni, więc to nie jest problem. Natomiast CTE `daily_prices` musi obsłużyć LEFT JOIN do `index_quotes` dla tickerów indeksów.

### 4. Scheduler — infrastruktura

**Trzy Cloud Run Jobs** (`.github/workflows/deploy.yml:47–71`):

| Job | Entrypoint | Cel |
|-----|-----------|-----|
| `puls-gpw` | `main.py` | Scraper ESPI/EBI |
| `puls-gpw-post` | `post_main.py` | Publikacja X postów |
| `puls-gpw-company-stats` | `company_stats_main.py` | Ingestion stats spółek |

**Wzorzec ingestion job** (`company_stats_main.py:28–87`):
```python
gpw  = fetch_listing_page("akcje")        # Bankier scrape
nc   = fetch_listing_page("new-connect")  # Bankier scrape
listing = {**gpw, **nc}                   # Merge
rows = {}
for company in list_companies_with_hop_info():
    symbol = symbol_from_hop_url(company.hop_url)
    rows[ticker] = {trading fields from listing[symbol]}
merge_company_daily_stats(rows)           # BQ MERGE
```

**HTTP client:** `src/http_client.py:34–56` — httpx, 0.5s rate limit, 3 retries, exponential backoff

**HTML scraper:** `src/bankier_metrics.py:57–113` — BeautifulSoup, tabela `.m-quotes-data-table`
- GPW URL: `bankier.pl/gielda/notowania/akcje`
- NewConnect URL: `bankier.pl/gielda/notowania/new-connect`
- **Indeksy URL:** `bankier.pl/gielda/notowania/indeksy-gpw` — ta sama klasa tabeli

**GCP config** (deploy-plan.md):
- Region: `europe-central2`
- `--max-retries=0`, `--task-timeout=300s`, `--memory=512Mi`, `--cpu=1`
- SA: `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com`

**CI/CD pattern** (`.github/workflows/deploy.yml:65–71`):
```yaml
gcloud run jobs update puls-gpw-company-stats \
  --command=uv --args="run,--no-dev,python,company_stats_main.py" \
  --region=europe-central2
```

### 5. BigQuery — schema i upsert

**`company_daily_stats` schema** (`db/bigquery.py:1758–1774`):

| Pole | Typ | Wymagane |
|------|-----|---------|
| ticker | STRING | REQUIRED |
| snapshot_date | DATE | REQUIRED (partition key) |
| kurs_zamkniecia | FLOAT64 | NULLABLE |
| zmiana_procentowa | FLOAT64 | NULLABLE |
| zmiana_kwotowa | FLOAT64 | NULLABLE |
| kurs_otwarcia | FLOAT64 | NULLABLE |
| kurs_min | FLOAT64 | NULLABLE |
| kurs_max | FLOAT64 | NULLABLE |
| wartosc_obrotu | FLOAT64 | NULLABLE |
| liczba_transakcji | INTEGER | NULLABLE |
| fetched_at | TIMESTAMP | REQUIRED |

Partycja: `snapshot_date` (DAY), cluster: `ticker`

**MERGE pattern** (`db/bigquery.py:1870–1943`):
1. Load rows do temp table (24h expiry) via `client.load_table_from_json()`
2. MERGE: MATCHED → UPDATE all numeric fields; NOT MATCHED → INSERT
3. Temp table usuwana w `finally`

**Schema migration** — `ensure_schema_current()` (`db/bigquery.py:145–188`): additive (dodaje brakujące NULLABLE kolumny), no-op jeśli schema aktualna

**BQ client init** (`db/bigquery.py:83–103`): quota_project guard z `hasattr(credentials, "with_quota_project")`

### 6. Frontend — dodawanie pozycji

**Formularz:** `static/index.html:2146–2404` (`_buildPortfolioPositionsViewContent()`)
- Ticker autocomplete (id: `pp-ticker-input`, dropdown: `ac-pp-ticker`)
- Cross-fill: ticker → company name via `_resolveCompanyForTicker()` (`index.html:1708–1716`)
- Submit → `_upsertPortfolioPosition()` → `POST /api/portfolio/positions`

**Autocomplete ticker source:** `GET /autocomplete/tickers` → `list_distinct_tickers()` (`db/bigquery.py:1666–1678`)
```sql
SELECT ticker FROM companies ORDER BY ticker
```
Cache 5 minut (`_AC_CACHE` w api.py:52–65)

**KRYTYCZNA WALIDACJA** (`src/api.py:481–483`):
```python
known_tickers = list_distinct_tickers()
if body.ticker not in known_tickers:
    raise HTTPException(status_code=422, detail="Unknown ticker")
```
→ Indeksy nie są w tabeli `companies` → **zostaną odrzucone**

**`companies` table schema** (`db/bigquery.py:900–909`):
- ticker (STRING), name (STRING), hop_url (STRING), isin (STRING), created_at, updated_at

---

## Architecture Insights

### Wzorzec powtórzony 3× — kandydat do ujednolicenia

Ten sam LEFT JOIN z `company_daily_stats` istnieje w:
1. `db/bigquery.py:601–625` (portfolio positions)
2. `db/bigquery.py:393–404` (calendar daily_prices CTE)
3. `src/portfolio_treemap.py` (używa wyników z #1)

Zamiast kopiować logikę do 3 miejsc, optymalnym rozwiązaniem jest **BQ VIEW `latest_prices`** łączący (UNION ALL) obie tabele (`company_daily_stats` + `index_quotes`). Każde z 3 miejsc JOIN-uje do VIEW zamiast bezpośrednio do tabeli.

### Ticker namespace — brak kolizji

Tickery GPW: `PKN`, `PKO`, `CDR` itd.  
Tickery indeksów GPW: `WIG`, `WIG20`, `MWIG40`, `SWIG80` — format wielkie litery bez cyfr w nazwie spółki. Nie kolidują. Weryfikacja: żaden ticker w `companies` nie zawiera `WIG`.

### `zmiana_kwotowa` dla indeksów

Kalendarz używa `SUM(shares × zmiana_kwotowa)` gdzie `zmiana_kwotowa` = dzienna zmiana ceny jednostki w PLN.  
Dla indeksów: wartość indeksu jest podana w punktach (1 punkt ≠ 1 PLN). Jednak wzorzec jest spójny: `shares` to liczba jednostek ETF/certyfikatu, `avg_buy_price` to cena zakupu w PLN, `kurs_zamkniecia`/`index_value` to bieżąca cena rynkowa w PLN.  
→ Bankier podaje `zmiana_kwotowa` dla indeksów w punktach, nie PLN — **to pole może wymagać oddzielnego obliczenia lub dokumentacji zakresu dla użytkownika**.

---

## Code References

- `db/bigquery.py:589–636` — `list_user_portfolio_positions()` — core price join
- `db/bigquery.py:352–442` — `get_portfolio_calendar_data()` — calendar 4-CTE query
- `db/bigquery.py:1758–1774` — `company_daily_stats` schema
- `db/bigquery.py:1870–1943` — `merge_company_daily_stats()` — MERGE pattern do skopiowania
- `db/bigquery.py:145–188` — `ensure_schema_current()` — schema migration pattern
- `db/bigquery.py:83–103` — `_get_client()` — BQ client z quota_project guard
- `db/bigquery.py:1666–1678` — `list_distinct_tickers()` — ticker validation source
- `src/api.py:134–142` — `TreemapPosition` model
- `src/api.py:148–165` — `PortfolioCalendarDay`, `PortfolioCalendarResponse` models
- `src/api.py:182–201` — `PortfolioPositionIn`, `PortfolioPositionOut` models
- `src/api.py:465–490` — `POST /api/portfolio/positions` — ticker validation (line 481–483)
- `src/api.py:573–618` — `GET /api/portfolio/treemap`
- `src/api.py:620–646` — `GET /api/portfolio/calendar`
- `src/portfolio_treemap.py:4–65` — `compute_user_portfolio_treemap_positions()`
- `src/portfolio_calendar.py:10–23` — `_GPW_HOLIDAYS`
- `src/portfolio_calendar.py:26–115` — `compute_calendar_pnl()`
- `src/bankier_metrics.py:57–113` — `fetch_listing_page()` — Bankier scraper (reusable)
- `src/bankier_metrics.py:12–13` — URL stałe dla akcje/new-connect (do rozszerzenia o indeksy)
- `src/http_client.py:34–56` — `get()` — HTTP client z retry
- `company_stats_main.py:1–106` — wzorzec entrypoint do skopiowania
- `.github/workflows/deploy.yml:65–71` — wzorzec dodawania Cloud Run Job
- `static/index.html:2146–2404` — formularz dodawania pozycji
- `static/index.html:2493–2506` — `fetchPortfolioTreemap()`
- `static/index.html:2650–2695` — `renderTreemap()`
- `static/js/treemap-layout.js:21–77` — squarified layout algorithm

---

## Open Questions

1. **`zmiana_kwotowa` dla indeksów**: Bankier podaje zmianę punktową dla indeksów, nie w PLN. Kalkulacja kalendarzowego P&L bazuje na `shares × zmiana_kwotowa`. Czy użytkownicy powinni widzieć P&L kalendarzowy dla indeksów w punktach czy tylko wyłączyć to pole dla indeksów? → Decyzja projektowa przed pisaniem planu.

2. **Ticker validation — companies vs. osobna tabela**: Czy indeksy trafiają do tabeli `companies` (ticker, name, hop_url=null) czy do osobnej tabeli `index_tickers`? Tabela `companies` ma `hop_url` jako klucz do scrape'owania cen; dla indeksów ten mechanizm nie jest używany. Rekomendacja: osobna tabela.

3. **Harmonogram job'a `index_quotes`**: Jak często? Indeksy zmieniają się intraday, ale portfolio i treemapa używają close price. Raz dziennie po zamknięciu sesji (jak company_stats) lub w trakcie sesji co N minut?

4. **Canonical ticker names dla indeksów**: Bankier używa `WIG20`, GPW oficjalnie też. Potwierdzić mapping przed hardcodowaniem listy.

5. **BQ VIEW vs. COALESCE w każdym query**: VIEW `latest_prices` jest czystsze, ale wymaga uprawnień CREATE VIEW. COALESCE/UNION ALL w każdym query jest prostsze do wdrożenia bez dodatkowej infrastruktury. Do ustalenia z użytkownikiem.

---

## Historical Context

Brak poprzednich change'ów bezpośrednio dotyczących tej funkcjonalności.

Powiązane zrealizowane change'y:
- `context/archive/pul-64*` — treemapa portfela (multi-wallet) — dostarcza bieżący kształt `TreemapPosition`
- `context/archive/pul-59*` — miesięczny kalendarz P&L — dostarcza bieżący kształt kalendarza
- `company_daily_stats` schema i MERGE zbudowane w change `company-stats-upsert` (sesja 2026-06-27)
