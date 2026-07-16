---
date: 2026-06-30T12:00:00+02:00
researcher: Claude (Sonnet 4.6)
git_commit: 4f048655bc5c978e402c8bb80307f4e9a3095b12
branch: radoslawjjd/pul-60-performance-announcements-table-and-portfolio-treemap-load
repository: radoslawjjd-design/puls-gpw
topic: "Performance: sub-second load for announcements, watchlist, portfolio, treemap and calendar"
tags: [research, performance, bigquery, caching, frontend, cloud-run]
status: complete
last_updated: 2026-06-30
last_updated_by: Claude (Sonnet 4.6)
---

# Research: Performance — Ogłoszenia, Obserwowane, Mój portfel, Treemapa, Kalendarz

**Date**: 2026-06-30  
**Git Commit**: 4f048655bc5c978e402c8bb80307f4e9a3095b12  
**Branch**: radoslawjjd/pul-60-performance-announcements-table-and-portfolio-treemap-load  
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

Zidentyfikuj faktyczne wąskie gardła na 5 wolno ładujących się widokach (Ogłoszenia, Obserwowane, Mój portfel + przełączanie portfeli, Treemapa, Kalendarz + przełączanie portfeli) i zaproponuj konkretne naprawy, które sprawią, że ładowanie będzie odczuwane jako natychmiastowe (sub-second) na ciepłej ścieżce.

## Summary

Badanie ujawniło **4 warstwy problemów**, działające kumulatywnie:

1. **BQ full-table scan** — `list_user_portfolio_positions()` (`db/bigquery.py:592`) wykonuje dwa skanowania całych tabel (`company_daily_stats` + `etf_quotes`) z funkcją okienkową `ROW_NUMBER()`, bez żadnego filtra daty. Partycjonowanie tabel (DAY by `snapshot_date`) jest całkowicie bezużyteczne. Dotyczy: Mój portfel, Treemapa (user variant), pośrednio Watchlist.

2. **Sekwencyjne BQ round-tripy** — Żaden endpoint nie używa `asyncio.gather`. Admin Treemapa: 5 BQ calls w pętli. User Treemapa: 3. Każdy z 5 widoków robi min. 2 sekwencyjne round-tripy (auth check + data). Każdy BQ round-trip na ciepłej ścieżce kosztuje ~500–1500 ms.

3. **Brak server-side cache** — Jedynym cache'em w aplikacji jest `_AC_CACHE` dla autocomplete (`src/api.py:54–67`). Wzorzec istnieje od PUL-25, nigdy nie był rozszerzony na żaden inny endpoint. Wszystkie dane portfela, ogłoszeń i watchlistu wracają z BQ przy każdym żądaniu.

4. **Cold starts Cloud Run** — `--min-instances=0` gwarantuje zimny start po każdym okresie bezczynności. Single-worker uvicorn, brak instrumentacji wydajności.

**Najwyżej-dźwigniowe naprawy:**
- Dodać `WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` w obu CTE w `list_user_portfolio_positions()` → eliminuje 2 full-table scany → szybsza Treemapa + portfel
- Dodać TTL cache (TTL 60–300 s) na endpointy treemap i positions, wzorując się na `_AC_CACHE`
- Zrównoleglić admin treemap (5 → 2 BQ calls, via `asyncio.gather`)
- Usunąć 35-dniowy lookback z Calendar — używany jest tylko `zmiana_kwotowa` (direct), nie wartość dnia poprzedniego

---

## Detailed Findings

### 1. Ogłoszenia (`GET /announcements`)

**Route:** `src/api.py:234–276`  
**BQ admin:** `db/bigquery.py:1260` — `list_announcements_admin()`  
**BQ user:** `db/bigquery.py:1420` — `list_announcements_user()`

**SQL (admin, uproszczony):**
```sql
SELECT a.*, COALESCE(x.post_text, a.post_text), ...
FROM announcements AS a
LEFT JOIN x_posts AS x ON a.x_post_id = x.x_post_id
[WHERE ...]
ORDER BY a.published_at DESC
LIMIT @page_size OFFSET @offset
```

**Problemy:**
- `announcements` nie ma partycji ani klastrowania — każde zapytanie to full table scan + sort
- LIMIT/OFFSET O(n): page 50 × 20 = 1000 wierszy do pominięcia
- Admin path: LEFT JOIN z `x_posts` przy każdym żądaniu (większość wierszy ma `x_post_id = NULL`)
- Filtr `company` używa `LOWER(company) LIKE LOWER('%...%')` — non-sargable, nie może używać żadnego indeksu
- **Frontend**: brak `AbortController` → race condition przy szybkim klikaniu prev/next (`static/index.html:1109–1115`)
- **Frontend cache**: żaden — każda zmiana strony/filtra to pełny re-fetch

**Payload:** 20 wierszy × 18 kolumn (admin, włącznie z `parsed_content` + `structured_analysis` JSON blob)

---

### 2. Obserwowane (`GET /watchlist` + `GET /announcements/my-wallet`)

**Routes:** `src/api.py:317` + `src/api.py:357`  
**BQ tickers:** `db/bigquery.py:890` — `list_watchlist_tickers()`  
**BQ ogłoszenia:** `db/bigquery.py:1473` — `list_announcements_for_watchlist()`

**SQL (ogłoszenia dla watchlist):**
```sql
SELECT a.company, a.ticker, a.event_type, a.structured_analysis, a.published_at
FROM announcements AS a
INNER JOIN (
    SELECT ticker FROM watchlist WHERE client_id = @client_id LIMIT 200
) AS w ON a.ticker = w.ticker
WHERE analysis_approved = TRUE [AND ...]
ORDER BY a.published_at DESC
LIMIT @page_size OFFSET @offset
```

**Problemy:**
- Takie same jak Ogłoszenia: LIMIT/OFFSET na unpartitioned table, ORDER BY na unindexed column
- INNER JOIN z subquery watchlist nie korzysta z żadnej struktury indeksowej BQ
- **Frontend**: re-fetch przy każdej wizycie w widoku Obserwowane (brak `_portfoliosFetched`-style guard) (`static/index.html:1394`)
- **Frontend**: po dodaniu/usunięciu tickera: `await fetchWatchlistTickers()` + `await fetchMyWalletAnnouncements()` — dwa sekwencyjne fetch które mogłyby być `Promise.all` (`static/index.html:1768–1789`)
- Na backend: `POST /watchlist/{ticker}` wywołuje `list_distinct_tickers()` (full scan `companies UNION etf_instruments`) tylko do walidacji przed INSERT — N+1 na każdym dodaniu

---

### 3. Mój portfel — Tabela (`GET /api/portfolio/positions`)

**Route:** `src/api.py:448`  
**BQ call 1:** `db/bigquery.py:687` — `list_user_portfolios()` (auth check)  
**BQ call 2:** `db/bigquery.py:592` — `list_user_portfolio_positions()`

**SQL (kluczowe):**
```sql
WITH latest_stats AS (
  -- BRAK WHERE snapshot_date = ... → full table scan company_daily_stats
  SELECT ticker, kurs_zamkniecia, zmiana_procentowa, snapshot_date,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `company_daily_stats`
),
latest_etf AS (
  -- BRAK WHERE → full table scan etf_quotes
  SELECT ticker, kurs_zamkniecia, zmiana_procentowa, snapshot_date,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `etf_quotes`
)
SELECT p.*, COALESCE(ls.kurs_zamkniecia, etf.kurs_zamkniecia) AS current_price, ...
FROM user_portfolio_positions p
LEFT JOIN latest_stats ls ON p.ticker = ls.ticker AND ls.rn = 1
LEFT JOIN latest_etf etf  ON p.ticker = etf.ticker AND etf.rn = 1
WHERE p.user_id = @user_id [AND p.portfolio_id = @portfolio_id]
```

**To jest najdroższe zapytanie w bazie kodu.** Tabele `company_daily_stats` (~570 tickers × 500+ dni ≈ 285K+ wierszy) i `etf_quotes` są partycjonowane po `snapshot_date` (DAY) i klastrowane po `ticker`, ale brak filtra daty sprawia, że partycjonowanie jest bezużyteczne. `ROW_NUMBER() OVER ALL` czyta i sortuje całą tabelę.

**Napraw wystarczy jedna linia na CTE:**
```sql
WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
```
lub:
```sql
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `company_daily_stats`)
```

**Frontend (waterfall na pierwszej wizycie):**
1. `showPortfolioPositionsView()` → `fetchUserPortfolios()` (nie-awaited, ale i tak sekwencyjny względem fetch walletów)
2. Dopiero po odpowiedzi → `fetchPortfolioPositions()`
3. `_portfoliosFetched` guard: wallets pobierane tylko raz per session (dobry wzorzec)
4. Zmiana zakładki portfela zawsze triggeruje pełny re-fetch pozycji (brak cache per portfolio_id)
5. Deep-link `?tab=treemap` → 3 hop waterfall: wallets → positions → treemap

---

### 4. Mój portfel — Treemapa (`GET /api/portfolio/treemap`)

**Route:** `src/api.py:588`  
**BQ call 1:** `db/bigquery.py:687` — `list_user_portfolios()`  
**BQ call 2+:** `db/bigquery.py:592` — `list_user_portfolio_positions()` (bez `portfolio_id` → ALL wallets)  
**BQ call 3:** `db/bigquery.py:2185` — `get_latest_company_stats_fetched_at()`

**Sekwencja (3 round-tripy):**
- Call 1 (wallets) → czeka → Call 2 (positions ALL wallets, full-table scan ×2) → czeka → Call 3 (metadata timestamp)

**Uwaga:** `list_user_portfolio_positions()` bez `portfolio_id` uruchamia te same 2 full-table scany co dla pojedynczego portfela, ale dla WSZYSTKICH portfeli użytkownika jednocześnie.

**Admin treemap** (`src/api.py:405`) — jeszcze gorzej: **5 sekwencyjnych BQ round-tripów**:
```
FOR wallet IN ("main", "ikze"):
    get_latest_snapshot_for_wallet(wallet)    → round-trip 1, 3
    get_latest_snapshot_before(wallet, date)  → round-trip 2, 4
get_latest_company_stats_fetched_at()         → round-trip 5
```
Wszystkie mogłyby być zmergowane do 2 zapytań + 1 lub równoległe przez `asyncio.gather`.

**Frontend:**
- `_ppTreemapData` cache: re-klik zakładki Treemapa NIE trigggeruje re-fetch (dobra praktyka)
- Cache NIE jest resetowany przy przełączeniu portfela (potencjalnie stale data — ale treemap endpoint zwraca wszystkie portfele, więc tu trade-off jest do decyzji)

---

### 5. Mój portfel — Kalendarz (`GET /api/portfolio/calendar`)

**Route:** `src/api.py:635`  
**BQ call 1:** `db/bigquery.py:687` — `list_user_portfolios()` (auth check)  
**BQ call 2:** `db/bigquery.py:352` — `get_portfolio_calendar_data()`

**SQL (uproszczony):**
```sql
WITH
  trading_days AS (
    SELECT DISTINCT snapshot_date FROM company_daily_stats
    WHERE snapshot_date BETWEEN @lookback_start AND @end_date  -- partition-pruned ✓
  ),
  positions AS (
    SELECT ticker, shares FROM user_portfolio_positions
    WHERE user_id = @user_id AND portfolio_id = @portfolio_id
  ),
  daily_prices AS (
    SELECT td.snapshot_date, p.ticker, p.shares,
           COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia) AS close_price,
           COALESCE(cds.zmiana_kwotowa,  etq.zmiana_kwotowa)  AS daily_chg
    FROM trading_days td
    CROSS JOIN positions p
    LEFT JOIN company_daily_stats cds ON cds.ticker = p.ticker AND cds.snapshot_date = td.snapshot_date
    LEFT JOIN etf_quotes etq          ON etq.ticker = p.ticker AND etq.snapshot_date = td.snapshot_date
  )
SELECT snapshot_date, SUM(...), SUM(...) FROM daily_prices GROUP BY snapshot_date
```

**Wnioski:**
- Partition pruning działa — to najlepiej zaprojektowane zapytanie z 5 (`company_daily_stats` i `etf_quotes` filtrowane po `snapshot_date BETWEEN`)
- Lookback 35 dni (`@lookback_start = month_start - 35 days`): BQ fetchuje ~5 tygodni danych, ale `compute_calendar_pnl()` w `src/portfolio_calendar.py:26` używa `zmiana_kwotowa` bezpośrednio (dzienna zmiana), a nie diff wartości portfela między dniami. Lookback jest skanowany, przekazywany przez sieć, a następnie IGNOROWANY przez Python.
- CROSS JOIN `trading_days × positions`: dla 30 pozycji × 44 dni handlowych ≈ 1320 wierszy pośrednich — akceptowalne
- 2 sekwencyjne BQ round-tripy (auth + data)

**Frontend:**
- `_ppCalData` jest resetowany przy przełączeniu portfela i nawigacji miesiąc ↔ miesiąc (poprawna logika)
- Przyciski prev/next WYŁĄCZANE synchronicznie PRZED fetch — jedyne miejsce w kodzie stosujące tę best practice (`static/index.html:2543–2544`)

---

## Code References

| File | Lines | Co jest tam |
|------|-------|-------------|
| `src/api.py` | 54–67 | `_AC_CACHE`, `_ac_get()`, `_ac_set()` — szablon dla nowych cache'y |
| `src/api.py` | 234–276 | `GET /announcements` route handler |
| `src/api.py` | 317, 357 | `GET /watchlist`, `GET /announcements/my-wallet` |
| `src/api.py` | 405–445 | `GET /admin/portfolio/treemap` — 5 seq. BQ calls |
| `src/api.py` | 448–480 | `GET /api/portfolio/positions` — 2 seq. BQ calls |
| `src/api.py` | 588–633 | `GET /api/portfolio/treemap` — 3 seq. BQ calls |
| `src/api.py` | 635–680 | `GET /api/portfolio/calendar` — 2 seq. BQ calls |
| `db/bigquery.py` | 592–648 | `list_user_portfolio_positions()` — 2 full-table scany |
| `db/bigquery.py` | 314, 273 | `get_latest_snapshot_for_wallet()`, `get_latest_snapshot_before()` |
| `db/bigquery.py` | 352–425 | `get_portfolio_calendar_data()` — 4-CTE query, partition-pruned |
| `db/bigquery.py` | 1260, 1420 | `list_announcements_admin()`, `list_announcements_user()` |
| `db/bigquery.py` | 1473 | `list_announcements_for_watchlist()` |
| `db/bigquery.py` | 2185 | `get_latest_company_stats_fetched_at()` — extra round-trip |
| `src/portfolio_calendar.py` | 26 | `compute_calendar_pnl()` — nie używa lookback |
| `static/index.html` | 1109–1115 | prev/next announcements — brak AbortController |
| `static/index.html` | 1394–1396 | `showMyWalletView()` — re-fetch bez guard |
| `static/index.html` | 1768–1789 | watchlist add/remove — 2 seq. fetch zamiast Promise.all |
| `static/index.html` | 2455 | `_portfoliosFetched` guard — dobry wzorzec |
| `static/index.html` | 2523 | `fetchPortfolioTreemap()` z `_ppTreemapData` cache |
| `static/index.html` | 2538 | `fetchPortfolioCalendar()` z `_ppCalData` cache |
| `static/index.html` | 2543–2544 | button disable przed fetch — best practice |
| `.github/workflows/deploy.yml` | 83–97 | `--min-instances=0`, `--max-instances=2`, `--timeout=60` |

---

## Architecture Insights

### Dlaczego „trivial at this scale" przestało być prawdą

Każdy change od PUL-45 do PUL-67 zawierał uzasadnienie „kilkanaście pozycji, brak cache potrzebny". Tak było prawdziwe dla wierszy z `portfolio_snapshots` (zawsze kilka wierszy). Problem pojawił się gdy:
1. PUL-54 dodał `company_daily_stats` (~570 tickers × rosnąca liczba dni)
2. PUL-64 + PUL-65 zmienił `list_user_portfolio_positions()` żeby joinować do tej tabeli przez `ROW_NUMBER() OVER ALL`
3. PUL-67 dodał `etf_quotes` i jeszcze jeden LEFT JOIN w tym samym wzorcu
4. Admin treemap (PUL-50) rozszerzył się z 2 do 4 sekwencyjnych BQ round-tripów, PUL-67 dodało piąty

Żadna z tych zmian nie była błędem z perspektywy tamtego dnia — wzrost kosztów był przyrostowy.

### Wzorzec `_AC_CACHE` — gotowy do rozszerzenia

```python
# src/api.py:54–67 — szablon
_AC_CACHE: dict[str, tuple[list, float]] = {}
_AC_TTL = 300

def _ac_get(key: str) -> list | None: ...
def _ac_set(key: str, data: list) -> None: ...
```

**Kandydaci do nowego cache'u (w kolejności priorytetów):**
1. `GET /api/portfolio/treemap` — per `client_id`, TTL 60 s, invalidacja przy upsert/delete pozycji
2. `GET /admin/portfolio/treemap` — globalny (admin-only), TTL 60 s
3. `GET /api/portfolio/positions` — per `client_id + portfolio_id`, TTL 30 s, invalidacja przy mutacjach
4. `GET /api/portfolio/calendar` — per `client_id + portfolio_id + year + month`, TTL 300 s, invalidacja przy mutacjach

**Bloker:** Brak `_ac_invalidate(key)` helpera — nie istnieje w codebase. Musi być dodany jako first step.

### Multi-instance cache incoherence

`_AC_CACHE` jest per-process, per-container. Przy `--max-instances=2` dwa kontenery mają niezależne cache'e. Przy TTL 60–300 s i jednym użytkowniku w tej chwili nie jest to problem, ale jest technicznym długiem przy skalowaniu.

### Brak instrumentacji = brak baseline

Zero `time.time()` wokół BQ calls w produkcji. Środki zaradcze dla PUL-60:
- Dodać FastAPI middleware logujący `X-Process-Time` na każde żądanie
- Dodać `logger.debug(f"BQ {fn_name}: {elapsed:.2f}s")` wrapper wokół BQ calls
- Bez tego baseline jest niemierzalny; decyzja o target latency (np. <300 ms) będzie bez danych

---

## Historical Context (from prior changes)

- `context/archive/2026-06-20-admin-ui-portfolio-treemap/plan.md:389–391` — "No caching needed; endpoint does exactly two BQ row reads per request." — aktualne wtedy, nieaktualne od PUL-54
- `context/archive/2026-06-20-portfolio-treemap-multi-wallet/plan.md:488–492` — "Four total BQ row reads, still trivial at this scale" — powiększone do 5 w PUL-67
- `context/archive/2026-06-12-pagination/plan.md` — "BQ OFFSET is a scan of all preceding rows — acceptable for the current data volume" — acknowledged risk, never revisited
- `context/archive/2026-06-18-panel-ui-redesign/plan.md` — jedyne miejsce gdzie cache był planowany (`_AC_CACHE` TTL 300 s)
- `context/archive/2026-06-29-pul-67/research.md:218–228` — "Nie tworzymy BQ VIEW `latest_prices` — overengineering na tym etapie" — ale wzorzec pojawia się teraz w 3 miejscach
- `context/archive/2026-06-27-pul-65/research.md` — "The position list is per-user and not suitable for module-level caching" — werdykt do rewizji (cache z kluczem `client_id` jest per-user)

## Related Research

- `context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/plan.md:393–399` — partycjonowanie `company_daily_stats` (tabela której pełny scan niszczy perf)
- `context/archive/2026-06-29-pul-59-portfolio-calendar/plan.md:603–610` — analiza CROSS JOIN kalendarza

---

## Open Questions

1. **Baseline** — Jakie są aktualne czasy ładowania na produkcji? Potrzebne manualnie (DevTools Network tab, admin panel), przed commitem jakiejkolwiek naprawy. Bez tego nie wiemy co jest „sub-second".

2. **Target** — Co znaczy „natychmiastowe"? Proponuję: TTFB < 300 ms na ciepłej ścieżce (Cloud Run nie w zimnym starcie). Full render < 500 ms.

3. **Cold start** — Czy podnieść `--min-instances=1`? Koszt: ~$5–15/miesiąc stały. Benefit: eliminacja 2–8 s opóźnienia przy pierwszym żądaniu po idle. To decyzja cost vs. UX.

4. **Keyset vs. OFFSET** — Czy implementować keyset pagination dla ogłoszeń (`WHERE published_at < @cursor`)? Wymaga zmiany API i frontendu. Alternatywa: partycjonować `announcements` po `published_at`.

5. **BQ VIEW `latest_prices`** — Wzorzec `COALESCE(company_daily_stats, etf_quotes)` pojawia się w 3 miejscach (positions, calendar, treemap). Materialized View w BQ eliminuje scan przy każdym query. Odrzucony w PUL-67 jako overengineering — czy to nadal aktualne?

6. **`asyncio.gather` dla admin treemap** — 5 → 2 BQ calls (równoległy fetch dwóch walletów + 1 shared). Czy admin treemap jest priorytetem, czy skupiamy się na user paths?
