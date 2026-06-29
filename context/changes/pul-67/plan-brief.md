# ETF/ETC/ETN Quotes Ingestion and Portfolio Integration — Plan Brief

> Full plan: `context/changes/pul-67/plan.md`
> Research: `context/changes/pul-67/research.md`

## What & Why

Dodajemy obsługę 36 instrumentów ETF/ETC/ETN notowanych na GPW (np. `ETFBW20TR`, `ETCGLDRMAU`, `ETNVIRBTCP`) jako pełnoprawnych pozycji portfela. Użytkownicy chcą trzymać te instrumenty w puls-gpw i widzieć ich wycenę, codzienny P&L oraz udział w portfelu — tak samo jak spółki giełdowe.

## Starting Point

System wycenia pozycje portfela przez LEFT JOIN z `company_daily_stats` (db/bigquery.py:589). ETF-y nie są w tabeli `companies`, więc `POST /api/portfolio/positions` odrzuca je z HTTP 422. Scraper `src/bankier_metrics.py` i wzorzec Cloud Run Job (`company_stats_main.py`) są gotowe do reużycia.

## Desired End State

Użytkownik wpisuje `ETFBW20TR` w formularzu dodawania pozycji, instrument pojawia się w autocomplete i jest zaakceptowany. Portfel, treemapa i kalendarz P&L wyceniają go tak samo jak akcję. Job `puls-gpw-etf-quotes` odpytuje `gpw.pl/etfy-pelna-wersja-notowan` na tej samej częstotliwości co `puls-gpw-company-stats` i aktualizuje dane w BigQuery.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Źródło danych | `gpw.pl/etfy-pelna-wersja-notowan` | Statyczny HTML, nie Bankier — ETF-y nie są na stronie indeksów | Conversation |
| Ticker master | Nowa tabela `etf_instruments` | Oddzielenie od `companies` — ETF-y to fundusze, nie spółki | Plan |
| Price query | COALESCE w 2 miejscach | 2 query = nie ma sensu tworzyć BQ VIEW | Conversation |
| `zmiana_kwotowa` | Derywuj przy scrape: `kurs_odn × zmiana_pct / 100` | Kalendarz reużywa `zmiana_kwotowa` bez zmian logiki | Plan |
| ETF name cross-fill | Pełna nazwa z `etf_instruments` via `/autocomplete/etf-instruments` | Spójne UX z istniejącym cross-fill dla spółek | Plan |
| Harmonogram | Ta sama częstotliwość co `puls-gpw-company-stats` | ETF-y mają 1 close price per dzień sesyjny | Conversation |
| CI/CD | Nowy job `puls-gpw-etf-quotes`, `gcloud run jobs update` | Wzorzec identyczny jak company-stats | Plan |

## Scope

**In scope:**
- 28 ETF + 1 ETC + 6 ETN z GPW (36 instrumentów łącznie, PLN)
- Wycena w portfelu (lista pozycji)
- Wycena w treemapie
- Dzienny P&L w kalendarzu
- Autocomplete + walidacja tickera przy dodawaniu pozycji
- Cloud Run Job + CI/CD

**Out of scope:**
- Surowe indeksy punktowe (WIG20 = X punktów) — nie są instrumentami do kupienia
- ETF-y na giełdach zagranicznych (tylko GPW)
- Intraday updates — raz dziennie
- BQ VIEW `latest_prices` — over-engineering na tym etapie

## Architecture / Approach

Nowy pipeline danych (`gpw_etf_metrics.py` → `etf_quotes_main.py` → BQ) działa niezależnie od istniejącego. Integracja z portfelem, treemapą i kalendarzem przez COALESCE w 2 istniejących BQ query — addytywne, niedestruktywne. Walidacja tickerów rozszerzona przez UNION DISTINCT na poziomie SQL.

```
GPW ETF page → fetch_etf_page() → merge_etf_instruments() + merge_etf_quotes()
                                                    ↓
list_user_portfolio_positions():  COALESCE(company_daily_stats, etf_quotes)
get_portfolio_calendar_data():    COALESCE(company_daily_stats, etf_quotes)
list_distinct_tickers():          companies UNION DISTINCT etf_instruments
```

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BigQuery Layer | Tabele `etf_instruments` + `etf_quotes`, MERGE functions, rozszerzony `list_distinct_tickers` | SQL reserved keywords w nazwach kolumn (sprawdź backtick) |
| 2. GPW Scraper | `src/gpw_etf_metrics.py` parsuje statyczny HTML GPW | Brak CSS class na tabelach — parser kruchy na zmiany layoutu |
| 3. Job Entrypoint | `etf_quotes_main.py` — load_dotenv first | Pominięcie `load_dotenv()` → 403 BQ (lesson) |
| 4. Portfolio + Treemap | ETF wyceniane w portfelu i treemapie | Regresja cen spółek przy złym COALESCE |
| 5. Kalendarz P&L | ETF uwzględniane w dziennym P&L | `zmiana_kwotowa` NULL dla ETF → state "partial" zamiast "data" |
| 6. Autocomplete + UX | ETF w autocomplete, cross-fill nazwy, POST 200 | Cache autocomplete nie odświeża po deploy (5 min TTL) |
| 7. CI/CD | `puls-gpw-etf-quotes` w deploy workflow | Job musi być utworzony ręcznie w GCP przed pierwszym deploy |

**Prerequisites:**
- Przed merge: Radek tworzy Cloud Run Job `puls-gpw-etf-quotes` w GCP Console (human-only action)
- Przed merge: Cloud Scheduler trigger dla nowego joba (ta sama częstotliwość co company-stats)

**Estimated effort:** ~3-4 sesje implementacyjne, 7 faz

## Open Risks & Assumptions

- GPW może zmienić layout strony ETF → parser po nagłówku jest odporniejszy niż po indeksie kolumny, ale nie jest bulletproof
- Nazwy ETF na stronie GPW (`name` = `ticker`, np. "ETFBW20TR") — czytelność autocomplete ograniczona; do poprawy w przyszłości
- Pierwszy deploy wymaga manualnego `gcloud run jobs create` (puls-gpw-etf-quotes) przed workflow `update`

## Success Criteria (Summary)

- `POST /api/portfolio/positions` z `ticker: "ETFBW20TR"` → HTTP 200 (nie 422)
- `GET /api/portfolio/positions` → `current_price` non-null dla ETF pozycji
- `GET /api/portfolio/calendar` → dni z ETF pozycjami mają `pnl_abs` non-null
