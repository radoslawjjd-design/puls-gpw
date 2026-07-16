---
date: 2026-07-16T00:00:00+02:00
researcher: Claude (Sonnet 4.6)
git_commit: c4acb1f813de0458fbb7cd0b5656c04d60780b69
branch: pul-68-portfolio-calendar-mtd-value-difference
repository: puls-gpw
topic: "Portfolio calendar MTD value difference — pul-68"
tags: [research, portfolio, calendar, mtd, bigquery, portfolio_calendar]
status: complete
last_updated: 2026-07-16
last_updated_by: Claude (Sonnet 4.6)
---

# Research: Portfolio Calendar MTD Value Difference (PUL-68)

**Date**: 2026-07-16  
**Researcher**: Claude (Sonnet 4.6)  
**Git Commit**: c4acb1f813de0458fbb7cd0b5656c04d60780b69  
**Branch**: pul-68-portfolio-calendar-mtd-value-difference  
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

Jak zaimplementować pole `mtd_diff` (month-to-date różnica wartości portfela) w widoku kalendarza portfela?

Formuła: `mtd_diff = portfolio_value[day] − portfolio_value[first_day_of_month]`

## Summary

Funkcja kalendarza portfela (PUL-59) jest w pełni zaimplementowana. Feature MTD jest **lokalną zmianą logiki obliczeniowej** — nie wymaga zmian w BigQuery, ponieważ BQ już pobiera 35-dniowy lookback (obejmuje koniec poprzedniego miesiąca). Zmiana dotyka 3 plików: `src/portfolio_calendar.py` (obliczenia), `src/api.py` (model Pydantic), `static/index.html` (render).

## Detailed Findings

### BigQuery Layer

**Tabela z danymi kalendarza**: dane nie pochodzą z `portfolio_snapshots`, lecz są obliczane on-the-fly z pozycji i cen:

- `db/bigquery.py:362-457` — `get_portfolio_calendar_data(portfolio_id, user_id, year, month)`
- Zapytanie pobiera zakres: `month_start − 35 dni` do `month_end`
- 4 CTE: `trading_days` → `positions` → `daily_prices` → `daily_portfolio`
- Wynik per dzień: `snapshot_date`, `portfolio_value`, `daily_change_pln`, `prices_found`, `total_positions`
- **Kluczowe**: 35-dniowy lookback oznacza, że wartość z dnia 1. bieżącego miesiąca (lub ostatni dzień poprzedniego miesiąca) jest już w zbiorze wynikowym — **nie potrzeba nowego SQL**

Źródła danych BQ:
- `user_portfolio_positions` (db/bigquery.py:491) — ticker, shares per portfel
- `company_daily_stats` (db/bigquery.py:1813) — kurs_zamkniecia, zmiana_kwotowa
- `etf_quotes` (db/bigquery.py:2013) — j.w. dla ETF/ETC/ETN

Tabela `portfolio_snapshots` (db/bigquery.py:194) — **nie jest używana przez kalendarz**; przechowuje codzienne snapshoty całego portfela, ale kalendarz rekonstruuje wartości z pozycji × ceny.

### API / Backend Layer

**Endpoint**: `GET /api/portfolio/calendar` — `src/api.py:692-724`

Parametry: `year: int`, `month: int`, `portfolio_id: str`  
Auth: `X-API-Key` + `X-Client-Id`  
Cache: 300 sekund (`cache_key = f"calendar:{client_id}:{portfolio_id}:{year}:{month}"`)

**Model Pydantic** — `src/api.py:175-184`:
```python
class PortfolioCalendarDay(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: str
    day: int
    weekday: int
    state: str              # 'data'|'weekend'|'holiday'|'no_data'|'partial'|'future'
    portfolio_value: float | None = None
    pnl_abs: float | None = None   # dzienny P&L — jest; MTD — brak
    prices_found: int = 0
    total_positions: int = 0
```

`mtd_diff` **nie istnieje** — to jest pole do dodania.

**Logika obliczeniowa** — `src/portfolio_calendar.py:26-115` (`compute_calendar_pnl()`):
- Przyjmuje `rows: list[dict]` z BQ (snapshot_date, portfolio_value, daily_change_pln, ...)
- Buduje pełną siatkę miesiąca z klasyfikacją stanu i `pnl_abs`
- Tutaj należy dodać obliczenie `mtd_diff`

**Testy** — `tests/test_portfolio_calendar.py`

### Frontend Layer

**HTML** — `static/index.html:2286-2299`:
```html
<div id="pp-calendar-wrap">
  <div id="pp-cal-nav"> ... </div>
  <div id="pp-cal-grid"></div>
  <div id="pp-cal-legend"> ... </div>
</div>
```

**JS fetch** — `static/index.html:2544-2565` — `fetchPortfolioCalendar()`:
- Pobiera `/api/portfolio/calendar?year=&month=&portfolio_id=`
- Zapisuje w `_ppCalData`, wywołuje `_renderPortfolioCalendar(data)`

**JS render** — `static/index.html:2567-2608` — `_renderPortfolioCalendar(data)`:
- Iteruje `data.days`, tworzy cell per dzień
- Aktualnie renderuje: `day.day` + `day.pnl_abs` (jako "+320 PLN")
- MTD do dodania jako `<span class="pp-cal-mtd">` w cell lub jako osobny element podsumowujący

**CSS kluczowe klasy** — `static/index.html:427-488`:
- `.pp-cal-cell` — min-height: 56px, flex column
- `.pp-cal-day` — pozycja top-left, font .68rem, muted
- `.pp-cal-pnl` — środek, font-weight 700, .82rem
- `.pp-cal-gain` / `.pp-cal-loss` / `.pp-cal-neutral` — tła kolorowe

## Code References

- `db/bigquery.py:362-457` — `get_portfolio_calendar_data()` — BQ query z 35-dniowym lookback
- `src/portfolio_calendar.py:26-115` — `compute_calendar_pnl()` — **główne miejsce zmiany**
- `src/api.py:175-184` — `PortfolioCalendarDay` — dodać `mtd_diff: float | None = None`
- `src/api.py:692-724` — `GET /api/portfolio/calendar` handler
- `static/index.html:2567-2608` — `_renderPortfolioCalendar()` — dodać render MTD
- `static/index.html:427-488` — CSS cells — dodać `.pp-cal-mtd` styl
- `tests/test_portfolio_calendar.py` — testy jednostkowe `compute_calendar_pnl`

## Architecture Insights

**Kluczowa obserwacja — baseline MTD:**

Dane BQ zawierają wszystkie trading days od `month_start − 35 dni`. W `compute_calendar_pnl()` dostępny jest `portfolio_value` z dnia 1. miesiąca (jeśli był dniem handlowym) lub z ostatniego dnia handlowego poprzedniego miesiąca. Baseline MTD to wartość `portfolio_value` dla najwcześniejszego dnia spełniającego warunek `snapshot_date <= date(year, month, 1)`.

Jeśli 1. miesiąca to weekend/święto → baseline = ostatni trading day poprzedniego miesiąca.

**Gdzie obliczyć MTD:**

W `compute_calendar_pnl()` (nie w BQ, nie w API handlerze):
1. Z `rows` z BQ wyciągnąć baseline: `portfolio_value` dla max(`snapshot_date`) gdzie `snapshot_date <= date(year, month, 1)`
2. Dla każdego dnia z `state == 'data'`: `mtd_diff = portfolio_value - baseline_value`
3. Dla dnia 1. miesiąca: `mtd_diff = 0` (jeśli to dzień handlowy) lub `None` (jeśli weekend)
4. Dla weekendów/świąt: `mtd_diff = None`
5. Dla `future`: `mtd_diff = None`

**Cache invalidation:** Zmiana modelu nie wpływa na cache key — po deploy cache wygaśnie naturalnie w 300s lub zostanie unieważniony przez reboot Cloud Run.

**Lesson (BQ reserved keywords):** MTD nie wymaga nowego SQL, więc problem z backtick-ami (patrz lessons.md) nie dotyczy tej zmiany.

## Historical Context

- `context/archive/2026-06-29-pul-59-portfolio-calendar/` — PUL-59: implementacja kalendarza (4 fazy: BQ, compute, API, frontend). Wszystkie 4 fazy zakończone. To jest bezpośredni poprzednik PUL-68.
- PUL-59 plan.md zawiera schemat `compute_calendar_pnl()` i strukturę danych — warto przejrzeć przy planowaniu.

## Open Questions

1. **Czy MTD jest liczone od 1. dnia miesiąca (calendar day 1) czy od pierwszego dnia handlowego?**  
   Ticket mówi "first day of current month" (day 1 kalendarza). Jeśli day 1 to weekend, baseline = wartość z ostatniego dnia handlowego poprzedniego miesiąca. To jest edge case do uwzględnienia w planie.

2. **Czy MTD ma być widoczne TYLKO dla bieżącego miesiąca czy dla historycznych też?**  
   Ticket nie precyzuje. Rekomendacja: dla wszystkich miesięcy (dane historyczne są dostępne).

3. **Gdzie w UI wyświetlić MTD?**  
   Opcja A: Mały tekst pod `pnl_abs` w każdej komórce kalendarza.  
   Opcja B: Osobna sekcja/pasek podsumowujący pod kalendarzem.  
   Ticket nie precyzuje — wymaga decyzji przy planowaniu.

4. **Czy cache TTL 300s jest akceptowalny po tej zmianie?**  
   Tak — MTD jest deterministic z danych historycznych.
