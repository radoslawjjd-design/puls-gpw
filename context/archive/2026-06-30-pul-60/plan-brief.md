# Performance: sub-second load — Plan Brief

> Full plan: `context/changes/pul-60/plan.md`
> Research: `context/changes/pul-60/research.md`

## What & Why

Użytkownicy czekają kilka sekund na dane w 5 widokach aplikacji: Ogłoszenia, Obserwowane, Mój portfel (tabela + przełączanie portfeli), Treemapa i Kalendarz. Trzy niezależne warstwy kumulują opóźnienie: BQ full-table scany bez partition pruning, brak server-side cache na endpointach portfela, i jeden re-fetch bez guard'a na froncie.

## Starting Point

Jedynym cache'em w aplikacji jest `_AC_CACHE` (`src/api.py:54–67`, TTL 300 s) — używany wyłącznie dla 3 endpointów autocomplete. Najdroższe zapytanie (`list_user_portfolio_positions`, `db/bigquery.py:592`) wykonuje dwa `ROW_NUMBER() OVER ALL` bez filtra dat na tabelach partycjonowanych po `snapshot_date` — partycjonowanie jest całkowicie bezużyteczne. Zero instrumentacji wydajności w produkcji.

## Desired End State

Wszystkie 5 widoków osiąga TTFB < 500 ms na ciepłej ścieżce. `X-Process-Time` header na każdej odpowiedzi umożliwia pomiar. Cache hit na portfelu/treemapie < 50 ms TTFB.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Target latency | TTFB < 500 ms (warm path) | Realistyczny dla BQ-backed API; full render < 1 s | Plan |
| BQ date filter | `DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)` | 7-dniowy bufor pokrywa weekendy + święta; prostszy niż subquery MAX | Plan |
| Cache strategy | TTL-only, bez invalidacji | Multi-instance (max-instances=2) i tak uniemożliwia pełną spójność; TTL 30–60 s akceptowalne dla portfela | Plan |
| Cache scope | 4 endpointy (treemap user/admin + positions + calendar) | Wszystkie mają wolne BQ paths i stabilne dane per session | Plan |
| Announcements fix | Partycjonowanie tabeli BQ + domyślny filtr 90 dni | Brak zmian API kontrakt; partition pruning aktywuje dla filtrowanych i nie-filtrowanych zapytań | Plan |
| Admin treemap | asyncio.to_thread + asyncio.gather | 5 seq → 2 parallel rounds; BQ calls są sync, wymagają to_thread dla rzeczywistego parallelizmu | Research |
| Cold start | min-instances=0 bez zmian | Koszt odłożony do PUL-30; ciepła ścieżka ważniejsza | Plan |
| Frontend scope | _watchlistFetched guard + Promise.all na add/remove | Dwa małe patche eliminujące zbędny round-trip; AbortController poza zakresem | Plan |

## Scope

**In scope:**
- Instrumentation: `X-Process-Time` header + BQ call timing DEBUG logs
- BQ fixes: date filter w `list_user_portfolio_positions()` + usunięcie martwego 35-dniowego lookback w calendar
- TTL cache (TTL-only) na 4 endpointach portfela
- Admin treemap: asyncio.gather parallelization (5→2 rundy)
- Announcements: partycjonowanie tabeli BQ + domyślny filtr 90 dni
- Frontend: watchlist re-fetch guard + Promise.all po add/remove

**Out of scope:**
- min-instances=1 (koszt, osobny ticket)
- Keyset pagination dla ogłoszeń
- Cache invalidation przy mutacjach
- AbortController dla prev/next
- BQ VIEW `latest_prices` (unifikacja 3 miejsc z COALESCE)
- Zmiany w Cloud Run config poza min-instances

## Architecture / Approach

Każdy endoint dostaje dwa poziomy obrony: (1) tańszy BQ query (date filter / partition / asyncio.gather) zmniejsza koszt cache-miss; (2) TTL cache eliminuje BQ call na ciepłej ścieżce. Wzorzec `_PERF_CACHE` jest klonem `_AC_CACHE` z parametrycznym TTL. Announcements partition migration jest jedyną destruktywną operacją — skrypt przygotowany, uruchomienie przez człowieka.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Baseline Instrumentation | X-Process-Time header + BQ debug timing | Brak — czysta addycja |
| 2. BQ Query Fixes | Eliminacja 2 full-table scanów portfela; usunięcie martwego lookback kalendarza | Ceny weekendowe — 7-dniowy bufor musi wystarczyć |
| 3. TTL Cache (4 endpointy) | Cache hit < 50 ms; eliminacja BQ calls na ciepłej ścieżce | TTL-only = stale data przez max 30–60 s po mutacji |
| 4. Admin Treemap Gather | 5 seq → 2 parallel rounds; ~50% redukcja TTFB admin | asyncio.to_thread musi być użyte (nie goły gather) |
| 5. Announcements Partitioning | Partition pruning + cluster by ticker; TTFB ogłoszeń < 500 ms | Migracja BQ = human-only; typ published_at do weryfikacji |
| 6. Frontend Watchlist | Brak re-fetch na każdej wizycie + równoległe fetche po mutacji | _watchlistFetched reset przy mutacji nie przy nawigacji |

**Prerequisites:** Faza 1 musi być wdrożona pierwsza (bez `X-Process-Time` headera weryfikacja kolejnych faz jest niemożliwa). Fazy 2–6 są niezależne od siebie; Faza 5 wymaga ręcznej migracji BQ (human).  
**Estimated effort:** ~2–3 sesje, 6 faz

## Open Risks & Assumptions

- Brak baseline measurement przed wdrożeniem — Faza 1 jako pierwsze deployuje i mierzy
- `published_at` typ (TIMESTAMP vs DATE) w BQ schema — weryfikacja przed napisaniem migration SQL
- Multi-instance cache incoherence przy max-instances=2 — akceptowalne przy TTL 30–60 s i jednym użytkowniku

## Success Criteria (Summary)

- TTFB < 500 ms dla wszystkich 5 widoków na ciepłej ścieżce (mierzalne przez `X-Process-Time` header)
- Cache hit na portfelu/treemapie: TTFB < 50 ms w DevTools
- Przejście do Obserwowane 3× — tylko 1. wejście robi network call
