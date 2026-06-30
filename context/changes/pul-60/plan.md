# Performance: sub-second load for announcements, watchlist, portfolio, treemap and calendar

## Overview

Eliminacja wielosekwencyjnych BQ round-tripów, full-table scanów bez partition pruning i braku server-side cache, aby wszystkie 5 widoków osiągało TTFB < 500 ms na ciepłej ścieżce (warm Cloud Run instance).

## Current State Analysis

Trzy niezależne warstwy spowalniają każdy z 5 widoków:

**Warstwa 1 — BigQuery queries:**
- `list_user_portfolio_positions()` (`db/bigquery.py:592`) wykonuje dwa `ROW_NUMBER() OVER ALL` bez filtra daty na `company_daily_stats` (~570 tickers × 500+ dni ≈ 285 K wierszy) i `etf_quotes` — pełny scan przy każdym żądaniu portfela i treemapy
- Admin treemap (`src/api.py:405`): 5 sekwencyjnych BQ round-tripów w pętli
- Calendar (`db/bigquery.py:352`): 35-dniowy lookback skanowany, przesyłany po sieci i ignorowany przez `compute_calendar_pnl()` (`src/portfolio_calendar.py:26`)
- Announcements (`db/bigquery.py:1260`): LIMIT/OFFSET na nieistniejącej partycji — full scan + pomiń N wierszy

**Warstwa 2 — Brak server-side cache:**
- Jedyny cache: `_AC_CACHE` (`src/api.py:54–67`) — wzorzec dict + TTL 300 s, wyłącznie dla 3 endpointów autocomplete
- Żaden z endpointów portfela, treemap, calendar ani watchlist nie cache'uje odpowiedzi

**Warstwa 3 — Frontend:**
- `showMyWalletView()` (`static/index.html:1394`) re-fetches watchlist przy każdej wizycie (brak guard'a jak `_portfoliosFetched`)
- Po dodaniu/usunięciu tickera: `await fetchWatchlistTickers(); await fetchMyWalletAnnouncements()` — dwa sekwencyjne fetche które mogłyby być równoległe (`static/index.html:1768–1789`)

## Desired End State

TTFB < 500 ms dla wszystkich 5 widoków na ciepłej ścieżce:
- `/api/portfolio/positions`: eliminacja 2 full-table scanów (date filter); cache TTL 30 s
- `/api/portfolio/treemap`: eliminacja full-table scan + cache TTL 60 s; `/admin/portfolio/treemap`: 5 → 2 równoległe rundy + cache TTL 60 s
- `/api/portfolio/calendar`: bez zbędnego lookback; cache TTL 300 s
- `GET /announcements`: partition pruning po `published_at` + domyślny filtr 90 dni
- Obserwowane: frontend guard eliminuje re-fetch przy każdej wizycie

`X-Process-Time` header na każdej odpowiedzi — mierzalny baseline i weryfikacja poprawy.

### Key Discoveries:

- `list_user_portfolio_positions()` (`db/bigquery.py:592–648`): brak `WHERE snapshot_date >= ...` w obu CTE — partition pruning bezużyteczny; `snapshot_date DATE` zgodne typowo z `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)`
- `_AC_CACHE` pattern (`src/api.py:54–67`): duplikować jako `_PERF_CACHE: dict[str, tuple[Any, float]]` z parametrycznym TTL
- Admin treemap: BQ functions są blocking sync — do parallelizacji wymagają `asyncio.to_thread()` (Python 3.13+, dostępne w projekcie); `asyncio.gather(fn1(), fn2())` na sync functions NIE zapewnia parallelizmu
- `compute_calendar_pnl()` (`src/portfolio_calendar.py:26`): używa `zmiana_kwotowa` (dzienna zmiana PLN/akcję) bezpośrednio — lookback 35 dni nie jest używany
- Announcements partition migration: BQ nie obsługuje `ALTER TABLE ADD PARTITION` — wymagana re-kreacja tabeli (skrypt + ręczne uruchomienie per CLAUDE.md: destructive infra = human-only)

## What We're NOT Doing

- `--min-instances=1` — decyzja cost vs. UX odłożona (PUL-30 lub osobny ticket)
- Keyset pagination dla ogłoszeń — zbyt duży zasięg zmian API; partycjonowanie + domyślny filtr wystarczy
- Cache invalidation przy mutacjach — TTL-only; multi-instance (max-instances=2) i tak uniemożliwia pełną spójność; TTL 30–60 s jest akceptowalne dla wolno-zmieniającego się portfela
- AbortController dla prev/next — poza zakresem tego ticketa
- BQ VIEW `latest_prices` — 3 miejsca używają COALESCE wzorca, ale unifikacja to osobny refaktor
- Zmiany w Cloud Run min-instances

## Implementation Approach

Sekwencja faz celowo uporzadkowana: instrumentation first (mierzymy baseline), potem najwyższy-dźwigniowy BQ fix, potem caching, potem bardziej inwazyjne zmiany (partycjonowanie, frontend). Każda faza jest wdrażalna niezależnie i daje mierzalną poprawę.

## Critical Implementation Details

**asyncio.to_thread() dla admin treemap (Faza 4):** BQ client w projekcie to synchroniczny `google.cloud.bigquery.Client`. Wywołanie `asyncio.gather(sync_fn1(), sync_fn2())` w async handlerze NIE zrównolegla — event loop i tak czeka sekwencyjnie. Prawidłowy wzorzec: `asyncio.gather(asyncio.to_thread(fn, arg), asyncio.to_thread(fn2, arg))` — oddelegowuje do thread pool. Python 3.13 dostępne w projekcie.

**Cache wartości (Faza 3):** `_AC_CACHE` przechowuje `tuple[list, float]`. `_PERF_CACHE` przechowuje `tuple[Any, float]` — wartości to dict (JSON body). Cache'uj pythonowy dict przed `JSONResponse` — nie cache'uj samego obiektu Response. Nie mieszać `_AC_CACHE` i `_PERF_CACHE`.

**Partition migration (Faza 5):** Weryfikuj typ kolumny `published_at` w `_SCHEMA` przed napisaniem SQL migracji — TIMESTAMP → `PARTITION BY DATE(published_at)`, DATE → `PARTITION BY published_at`. Wszystkie istniejące INSERT do `announcements` pozostają niezmienione — BQ partition jest transparentna dla DML.

---

## Phase 1: Baseline Instrumentation

### Overview

Dodanie `X-Process-Time` headera i logowania czasu BQ queries. Faza deployuje się niezależnie i daje mierzalny baseline przed kolejnymi naprawami. Bez tego nie wiemy czy kolejne fazy osiągają cel TTFB < 500 ms.

### Changes Required:

#### 1. FastAPI request-timing middleware

**File**: `src/api.py`

**Intent**: Dodać middleware `@app.middleware("http")` w `create_app()`, który mierzy czas od startu do końca żądania i wstawia go jako `X-Process-Time: <ms>ms` w response headers. Czas liczyć jako `(time.time() - start) * 1000` z dokładnością do 1 miejsca po przecinku.

**Contract**: Middleware dodany natychmiast po `app = FastAPI()` w `create_app()`. Header format: `X-Process-Time: 234.5ms`.

#### 2. BQ call timing debug logger

**File**: `db/bigquery.py`

**Intent**: Owinąć 6 kluczowych funkcji BQ w timer debugowy: `time.time()` przed/po wywołaniu, `logger.debug(f"BQ {fn_name}: {elapsed:.0f}ms")`. Dotyczy: `list_user_portfolio_positions`, `get_portfolio_calendar_data`, `get_latest_snapshot_for_wallet`, `get_latest_snapshot_before`, `list_announcements_admin`, `list_announcements_user`, `list_announcements_for_watchlist`.

**Contract**: Poziom DEBUG — nie pojawia się w produkcji (domyślny log level INFO). Nie zmienia interfejsu żadnej funkcji. `import time` jeśli brak w module.

### Success Criteria:

#### Automated Verification:

- `pytest tests/` — wszystkie testy przechodzą bez zmian
- `X-Process-Time` header obecny w odpowiedzi: `curl -s -I http://localhost:8080/announcements | grep -i x-process-time`

#### Manual Verification:

- Zmierzyć TTFB w DevTools Network dla każdego z 5 widoków na adminie prod — zapisać jako baseline
- `X-Process-Time` widoczny w DevTools > Headers dla każdego endpointu

**Implementation Note**: Po fazie 1 i weryfikacji — zapisz baseline przed przejściem do Fazy 2.

---

## Phase 2: BQ Query Fixes (highest leverage)

### Overview

Dwie ortogonalne zmiany SQL eliminujące najdroższe scany: (1) filtr daty w `list_user_portfolio_positions()` — eliminuje 2 full-table scany wpływające na Mój portfel i Treemapę; (2) usunięcie martwego 35-dniowego lookback w `get_portfolio_calendar_data()`.

### Changes Required:

#### 1. Date filter w obu CTE list_user_portfolio_positions()

**File**: `db/bigquery.py`

**Intent**: Dodać `WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` do CTE `latest_stats` (skanuje `company_daily_stats`) i CTE `latest_etf` (skanuje `etf_quotes`). 7-dniowy bufor gwarantuje dane nawet po 3-dniowym weekendzie lub święcie. Semantyka nie zmienia się — `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn` i tak zwróci rn=1 = ostatni dzień.

**Contract**: Zmiana w funkcji `list_user_portfolio_positions()` (`db/bigquery.py:592`). W CTE `latest_stats` (ok. linia 609): dodać `WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` przed `GROUP BY` / `ORDER BY`. Analogicznie w CTE `latest_etf` (ok. linia 621). Typ `snapshot_date DATE` — `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` zwraca DATE, typy zgodne, partition pruning aktywowany.

#### 2. Usunięcie 35-dniowego lookback w get_portfolio_calendar_data()

**File**: `db/bigquery.py`

**Intent**: Zmienić obliczanie `lookback_start` z `month_start - 35 days` na `month_start` (pierwszy dzień miesiąca). `compute_calendar_pnl()` w `src/portfolio_calendar.py:26` używa `zmiana_kwotowa` bezpośrednio — lookback nie jest konsumowany przez żaden downstream kod.

**Contract**: Zmiana w `db/bigquery.py:369–371` (obliczenie `lookback_start`). CTE `trading_days` w query ma `WHERE snapshot_date BETWEEN @lookback_start AND @end_date` — po zmianie scan ogranicza się do jednego miesiąca (~22 trading days) zamiast ~66.

### Success Criteria:

#### Automated Verification:

- `pytest tests/` — wszystkie testy przechodzą
- `X-Process-Time` dla `GET /api/portfolio/positions` < 1500 ms (po cache-miss, warm instance)
- SQL string `list_user_portfolio_positions()` zawiera `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` (assert na query string)

#### Manual Verification:

- Zmierzyć TTFB w DevTools dla Mój portfel (tabela), Treemapa, Kalendarz — porównać z baseline z Fazy 1
- Pozycje wyświetlają poprawne ceny po weekendzie (7-dniowy bufor wystarczy)
- Dane kalendarza dla bieżącego miesiąca identyczne przed i po zmianie lookback

**Implementation Note**: Faza 2 jest najwyższy-dźwigniowy fix — spodziewana redukcja TTFB o 60–80% dla portfela i treemap.

---

## Phase 3: TTL Cache for Portfolio Endpoints

### Overview

Nowy `_PERF_CACHE` dict (identyczny wzorzec co `_AC_CACHE` z `src/api.py:54`) z parametrycznym TTL. Cache na 4 endpointach: user treemap (60 s), positions (30 s), calendar (300 s), admin treemap (60 s). TTL-only, bez invalidacji przy mutacjach.

### Changes Required:

#### 1. Cache infrastruktura (_PERF_CACHE)

**File**: `src/api.py`

**Intent**: Dodać moduł-level `_PERF_CACHE: dict[str, tuple[Any, float]] = {}` i dwie funkcje `_perf_get(key: str, ttl: int) -> Any | None` / `_perf_set(key: str, data: Any) -> None` — identyczny wzorzec co `_ac_get`/`_ac_set` (linia 57–67), z różnicą: wartość `Any` zamiast `list` i TTL jako parametr (nie stała globalna).

**Contract**: Dodać bezpośrednio po bloku `_AC_CACHE` (po linii ~67). `_perf_get` sprawdza `time.time() - ts < ttl` z przekazanym `ttl`. `from typing import Any` jeśli brak w imporcie.

#### 2. Cache na GET /api/portfolio/treemap

**File**: `src/api.py`

**Intent**: Na początku handlera (linia ~588): `key = f"treemap:{client_id}"` (gdzie `client_id = request.headers.get("X-Client-Id", "")`). Sprawdzić `_perf_get(key, ttl=60)` — jeśli hit, zwrócić `JSONResponse(cached_data)`. Na końcu, przed `return`, wywołać `_perf_set(key, response_dict)`.

**Contract**: Cache'ować pythonowy dict (nie `JSONResponse`). `client_id` z headera `X-Client-Id`.

#### 3. Cache na GET /api/portfolio/positions

**File**: `src/api.py`

**Intent**: Analogicznie do cache'u treemap, TTL 30 s. `portfolio_id` z query param.

**Contract**: Cache key: `f"positions:{client_id}:{portfolio_id}"` gdzie `portfolio_id = request.query_params.get("portfolio_id", "")`.

#### 4. Cache na GET /api/portfolio/calendar

**File**: `src/api.py`

**Intent**: Analogicznie, TTL 300 s. Cache key zawiera `year` i `month` z query params.

**Contract**: Cache key: `f"calendar:{client_id}:{portfolio_id}:{year}:{month}"`.

#### 5. Cache na GET /admin/portfolio/treemap

**File**: `src/api.py`

**Intent**: Admin treemap zwraca dane globalne (nie per-user), TTL 60 s.

**Contract**: Cache key: `"admin:treemap"` (stały string).

### Success Criteria:

#### Automated Verification:

- `pytest tests/` — przechodzą (cache hit nie powinien zepsuć istniejących testów)

#### Manual Verification:

- Odświeżyć Treemapę 2× w ciągu 60 s — 2. wywołanie: `X-Process-Time < 50 ms` (cache hit)
- 1. wywołanie > 300 ms, 2. wywołanie < 50 ms — kontrast widoczny
- Po 70 s (TTL expired): 3. wywołanie znów > 300 ms (świeże BQ query)

---

## Phase 4: Admin Treemap Parallelization

### Overview

Refaktor `GET /admin/portfolio/treemap` (`src/api.py:405`): 5 sekwencyjnych BQ round-tripów w pętli → 2 równoległe rundy przez `asyncio.to_thread()` + `asyncio.gather()`.

### Changes Required:

#### 1. Refaktor pętli walletów na równoległe rundy

**File**: `src/api.py`

**Intent**: Zastąpić sekwencyjną pętlę `for wallet in _TREEMAP_WALLETS:` dwuetapowym równoległym gather:
- Runda 1 (parallel): `asyncio.gather` na `get_latest_snapshot_for_wallet("main")` i `get_latest_snapshot_for_wallet("ikze")` — wrappowane w `asyncio.to_thread()`
- Runda 2 (parallel): `asyncio.gather` na `get_latest_snapshot_before("main", main_date)`, `get_latest_snapshot_before("ikze", ikze_date)` i `get_latest_company_stats_fetched_at(main_date)` — wszystkie trzy przez `asyncio.to_thread()`

**Contract**: **`import asyncio` BRAKUJE w `src/api.py` (potwierdzone grep) — dodać na górze pliku jako pierwszy krok tej fazy.** Wyniki Rundy 1 rozpakowywane przed Rundą 2 (`main_snap, ikze_snap = await asyncio.gather(...)`). Daty snapshots pobierane z wyników Rundy 1 jako argumenty do Rundy 2.

### Success Criteria:

#### Automated Verification:

- `pytest tests/` przechodzą

#### Manual Verification:

- `X-Process-Time` dla `GET /admin/portfolio/treemap` spada o ~50% vs. baseline (5 seq → 2 parallel rounds)
- Admin treemap renderuje poprawnie oba portfele (main + ikze) z poprawnymi wartościami

---

## Phase 5: Announcements Table Partitioning

### Overview

Migracja tabeli `announcements` w BQ: dodanie `PARTITION BY DATE(published_at)` i `CLUSTER BY ticker`. Migracja jest destruktywna (re-create table) — skrypt przygotowany w repozytorium, uruchomienie przez człowieka. Po migracji dodanie domyślnego filtra 90 dni aktywuje partition pruning dla typowych zapytań.

### Changes Required:

#### 1. Migration script (human-run)

**File**: `scripts/migrate_announcements_partition.py`

**Intent**: Jednorazowy skrypt do ręcznego uruchomienia przez człowieka. Tworzy backup tabeli, następnie re-tworzy ją z partition i cluster. Idempotentny (backup tworzy tylko jeśli nie istnieje). Obsługuje flagi `--dry-run` (tylko wypisuje SQL) i `--execute` (wykonuje).

**Contract**: Sekwencja przy `--execute`:
1. `CREATE TABLE IF NOT EXISTS {dataset}.announcements_backup AS SELECT * FROM {dataset}.announcements`
2. `CREATE OR REPLACE TABLE {dataset}.announcements PARTITION BY [DATE(published_at) | published_at] CLUSTER BY ticker AS SELECT * FROM {dataset}.announcements_backup`

Skrypt weryfikuje typ kolumny `published_at` przed generowaniem SQL — autodetekcja:
```python
ref = client.dataset(dataset_id).table("announcements")
schema = client.get_table(ref).schema
field = next(f for f in schema if f.name == "published_at")
partition_clause = "DATE(published_at)" if field.field_type == "TIMESTAMP" else "published_at"
```

#### 2. Schema update w db/bigquery.py

**File**: `db/bigquery.py`

**Intent**: Zaktualizować definicję tabeli `announcements` — dodać `TimePartitioning` i `clustering_fields` na obiekcie `bigquery.Table` w miejscu gdzie tabela jest tworzona/definiowana. Zapewnia że ewentualna przyszła re-kreacja zachowa partycję.

**Contract**: Znaleźć gdzie `announcements` jest inicjalizowana (prawdopodobnie przez `ensure_schema_current()` lub `create_table_if_not_exists()`) i dodać `table.time_partitioning = bigquery.TimePartitioning(field="published_at", type_="DAY")` + `table.clustering_fields = ["ticker"]`.

#### 3. Domyślny filtr 90 dni w _build_filter_clauses()

**File**: `db/bigquery.py`

**Intent**: Gdy `date_from` jest None, dodać `WHERE published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)` (lub `DATE` wariant zależnie od typu). Aktywuje partition pruning dla zapytań bez filtrów daty użytkownika.

**Contract**: Zmiana w `_build_filter_clauses()` (`db/bigquery.py:1230–1257`). Dodać stałą `_ANNOUNCEMENTS_DEFAULT_DAYS = 90` na poziomie modułu. Jeśli `date_from` jest podany przez użytkownika, jego wartość jest nadrzędna.

### Success Criteria:

#### Automated Verification:

- `pytest tests/` przechodzą
- `python scripts/migrate_announcements_partition.py --dry-run` bez błędów (wypisuje SQL)

#### Manual Verification:

- **[HUMAN-ONLY]**: Uruchomić `python scripts/migrate_announcements_partition.py --execute` na prod po review
- BQ Console: tabela `announcements` ma `Time partition field: published_at`, `Clustering fields: ticker`
- `X-Process-Time` dla `GET /announcements` (strona 1, bez filtrów) < 500 ms po migracji

---

## Phase 6: Frontend Watchlist Optimizations

### Overview

Dwie drobne poprawki w `static/index.html`: guard zapobiegający re-fetch przy każdej wizycie + równoległe fetche po add/remove.

### Changes Required:

#### 1. _watchlistFetched guard

**File**: `static/index.html`

**Intent**: Dodać moduł-level `let _watchlistFetched = false` (analogia do `_portfoliosFetched`, linia ~1860). W `showMyWalletView()` (linia ~1394): wywołać `fetchWatchlistTickers()` i `fetchMyWalletAnnouncements()` tylko gdy `!_watchlistFetched`, ustawić `_watchlistFetched = true` po starcie fetchy. Zresetować do `false` na końcu `addWatchlistTicker()` i `removeWatchlistTicker()` — następna wizyta re-fetches po mutacji.

**Contract**: `fetchWatchlistTickers()` ustawia `_watchlistFetched = true` WEWNĄTRZ funkcji po udanym fetchu (analogia `_portfoliosFetched:2110`). `addWatchlistTicker()` i `removeWatchlistTicker()` resetują do `false` PRZED wywołaniem funkcji fetch — fetch function ustawia z powrotem na `true`. Nie resetować przy nawigacji — tylko przy mutacjach.

#### 2. Promise.all w add/remove watchlist

**File**: `static/index.html`

**Intent**: W `addWatchlistTicker()` (linia ~1768) i `removeWatchlistTicker()` (~1789): zastąpić sekwencyjne `await fetchWatchlistTickers(); await fetchMyWalletAnnouncements()` przez `await Promise.all([fetchWatchlistTickers(), fetchMyWalletAnnouncements()])`. Obie funkcje są niezależnymi odczytami po zakończonej mutacji.

**Contract**: Tylko przy add/remove (po mutacji). Istniejące wywołania w `showMyWalletView()` (bez `await`, równoległe) pozostają bez zmian.

### Success Criteria:

#### Automated Verification:

- `playwright test` — istniejące E2E testy watchlist przechodzą bez zmian

#### Manual Verification:

- Przejść do Obserwowane → zakładka ładuje się → przejść do innego widoku → wrócić do Obserwowane → dane widoczne natychmiast, brak widocznego re-fetching w DevTools Network
- Dodać ticker: sprawdzić w DevTools Network że oba fetche po mutacji startują w tym samym czasie (równoległe timestamps)

---

## Testing Strategy

### Unit Tests:

- `_perf_get`/`_perf_set`: test hit/miss/expiry — analogia do wzorca `_ac_get`/`_ac_set`
- SQL string assert: `list_user_portfolio_positions()` zawiera `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)`

### Integration Tests:

- E2E Playwright: istniejące testy portfolio/watchlist/calendar muszą przechodzić bez zmian

### Manual Testing Steps:

1. Baseline (po Fazie 1): zmierzyć i zapisać TTFB dla każdego z 5 widoków
2. Po Fazie 2: sprawdzić TTFB dla portfela, treemap, kalendarza — redukcja o ~60–80%
3. Po Fazie 3: 2× Treemapa < 60 s — 1. call > 300 ms, 2. call < 50 ms
4. Po Fazie 4: admin treemap TTFB spada o ~50%
5. Po Fazie 5: ogłoszenia strona 1 < 500 ms; dane poprawne
6. Po Fazie 6: 3× nawigacja do Obserwowane — tylko 1. wejście powoduje network call

## Performance Considerations

Ranking napraw według szacowanego wpływu na TTFB:

1. **Faza 2** (BQ date filter): ~60–80% redukcja czasu zapytań portfela/treemap — eliminacja full-table scan na partycjonowanej tabeli
2. **Faza 3** (Cache TTL): eliminacja BQ calls na ciepłej ścieżce → TTFB < 50 ms na cache hit
3. **Faza 5** (Partycjonowanie ogłoszeń): ~70–90% redukcja czasu strony 1 ogłoszeń; głębsze strony wymagają keyset (poza zakresem)
4. **Faza 4** (asyncio.gather admin treemap): ~50% redukcja czasu admin treemap
5. **Faza 6** (Frontend watchlist): eliminacja 1 zbędnego round-trip na wizytę

Multi-instance uwaga: `_PERF_CACHE` jest per-container (max 2 instancje Cloud Run). Zimny start drugiej instancji startuje z pustym cache — max 2 dodatkowe BQ calls na TTL window. Akceptowalne.

## Migration Notes

Faza 5 wymaga ręcznego uruchomienia `scripts/migrate_announcements_partition.py --execute` przez człowieka. Skrypt tworzy backup przed re-kreacją. Weryfikacja: BQ Console po migracji.

## References

- Research: `context/changes/pul-60/research.md`
- Cache pattern: `src/api.py:54–67` (`_AC_CACHE`)
- BQ date filter target: `db/bigquery.py:592–648` (`list_user_portfolio_positions`)
- Admin treemap loop: `src/api.py:405–445`
- Frontend guard wzorzec: `static/index.html:2455` (`_portfoliosFetched`)
- Announcements queries: `db/bigquery.py:1230–1257` (`_build_filter_clauses`), `db/bigquery.py:1260`, `db/bigquery.py:1420`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Baseline Instrumentation

#### Automated

- [x] 1.1 pytest tests/ — wszystkie testy przechodzą — f779a15
- [x] 1.2 X-Process-Time header obecny w odpowiedzi (curl -I) — f779a15

#### Manual

- [x] 1.3 Zmierzyć i zapisać TTFB baseline dla wszystkich 5 widoków w DevTools prod — f779a15

### Phase 2: BQ Query Fixes

#### Automated

- [x] 2.1 pytest tests/ przechodzą — 5843dd0
- [x] 2.2 X-Process-Time dla GET /api/portfolio/positions < 1500 ms (warm, cache-miss) — 5843dd0
- [x] 2.6 SQL string list_user_portfolio_positions zawiera DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) — 5843dd0

#### Manual

- [x] 2.3 TTFB Mój portfel, Treemapa, Kalendarz — porównać z baseline z Fazy 1 — 5843dd0
- [x] 2.4 Pozycje wyświetlają poprawne ceny po weekendzie (7-dniowy bufor działa) — 5843dd0
- [x] 2.5 Dane kalendarza dla bieżącego miesiąca identyczne przed i po — 5843dd0

### Phase 3: TTL Cache

#### Automated

- [ ] 3.1 pytest tests/ przechodzą

#### Manual

- [ ] 3.2 2. wywołanie Treemap w ciągu 60 s — X-Process-Time < 50 ms
- [ ] 3.3 Po 70 s: 3. wywołanie znów > 300 ms (TTL wygasł)

### Phase 4: Admin Treemap Parallelization

#### Automated

- [ ] 4.1 pytest tests/ przechodzą

#### Manual

- [ ] 4.2 X-Process-Time admin treemap spada o ~50% vs. baseline
- [ ] 4.3 Admin treemap renderuje poprawne dane (main + ikze)

### Phase 5: Announcements Table Partitioning

#### Automated

- [ ] 5.1 pytest tests/ przechodzą
- [ ] 5.2 python scripts/migrate_announcements_partition.py --dry-run bez błędów

#### Manual

- [ ] 5.3 [HUMAN] Uruchomić scripts/migrate_announcements_partition.py --execute na prod
- [ ] 5.4 BQ Console: tabela announcements ma partition field published_at i cluster by ticker
- [ ] 5.5 X-Process-Time dla GET /announcements (strona 1, bez filtrów) < 500 ms

### Phase 6: Frontend Watchlist

#### Automated

- [ ] 6.1 playwright test — E2E testy watchlist przechodzą

#### Manual

- [ ] 6.2 3× navigacja do Obserwowane — tylko 1. wejście robi network call w DevTools
- [ ] 6.3 Add ticker: oba fetche startują jednocześnie (Promise.all)
