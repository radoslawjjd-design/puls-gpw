# Portfolio Calendar MTD Value Difference — Implementation Plan

## Overview

Dodanie pola `mtd_diff` (month-to-date różnica wartości portfela) do kalendarza portfela. Backend oblicza MTD per dzień handlowy; frontend wyświetla aktualne MTD w osobnym elemencie podsumowującym pod siatką kalendarza.

## Current State Analysis

`compute_calendar_pnl()` w `src/portfolio_calendar.py:26-115` buduje miesięczną siatkę z `pnl_abs` (dzienny P&L) per dzień, ale nie ma MTD. BQ query (`db/bigquery.py:362-457`) pobiera 35-dniowy lookback — wartość z końca poprzedniego miesiąca jest już w `rows`, więc **SQL nie wymaga zmian**.

### Key Discoveries

- `rows` zawiera `snapshot_date`, `portfolio_value`, `daily_change_pln`, `prices_found`, `total_positions` — wszystko co potrzeba do MTD
- Lookback rows (przed `month_start`) są w `rows` ale dotychczas ignorowane; będą baseline'em MTD
- `PortfolioCalendarDay` (`src/api.py:175-184`) ma `model_config = ConfigDict(extra="ignore")` — nowe pole w dict jest pomijane dopóki nie dodamy go do modelu
- Testy (`tests/test_portfolio_calendar.py`) mają wzorzec `_make_row()` gotowy do rozszerzenia

## Desired End State

Endpoint `GET /api/portfolio/calendar` zwraca per dzień `mtd_diff: float | None`. Dla dni `state='data'` pole zawiera `portfolio_value[day] - baseline_value`, gdzie baseline = last trading day ≤ day 1 of month. Frontend pokazuje aktualny MTD w elemencie pod siatką kalendarza ze znakiem +/- i walutą PLN.

Weryfikacja: `curl /api/portfolio/calendar?...` → każdy dzień `state='data'` ma `mtd_diff` != null; w UI widoczne "MTD: +X PLN" lub "MTD: −X PLN" pod siatką.

### Key Discoveries (Decision Context)

- Baseline: `max(snapshot_date)` z rows gdzie `snapshot_date <= date(year, month, 1)` — obsługuje weekendy i święta na początku miesiąca
- Dzień 1. miesiąca, gdy jest dniem handlowym: `mtd_diff = 0` (matematycznie)
- Brak baseline (brak historycznych rows): `mtd_diff = None` dla wszystkich dni
- Tylko `state='data'` dostaje `mtd_diff != None` — `partial`, `weekend`, `holiday`, `no_data`, `future` → None

## What We're NOT Doing

- Nie zmieniamy BQ query — 35-dniowy lookback wystarczy
- Nie pokazujemy MTD wewnątrz każdej komórki siatki — tylko osobny element podsumowujący
- Nie pokazujemy MTD dla partial/no_data/weekend/holiday/future
- Nie dodajemy historycznego wykresu MTD
- Nie zmieniamy cache TTL (300s jest OK — po deploy stary cache wygaśnie naturalnie)

## Implementation Approach

Zmiana w 3 plikach, 2 fazy:
1. Backend: baseline computation w `compute_calendar_pnl()` + nowe pole `mtd_diff` w modelu Pydantic + testy
2. Frontend: element summary pod `pp-cal-grid` z JS renderem aktualnego MTD

## Critical Implementation Details

**Baseline lookup musi używać `<=` (nie `<`)**: gdy dzień 1. miesiąca jest dniem handlowym, jego wiersz musi zostać wybrany jako baseline; wtedy `mtd_diff[day_1] = 0` automatycznie. Użycie `<` dałoby baseline z poprzedniego miesiąca i day 1 miałby nie-zerowe MTD.

**`model_config = ConfigDict(extra="ignore")`**: dopóki `mtd_diff` nie trafi do `PortfolioCalendarDay`, wartość będzie cicho gubiona przez `PortfolioCalendarResponse(**cal).model_dump()`. Pydantic model i compute muszą być zmienione w tej samej fazie.

---

## Phase 1: Backend — Compute + Pydantic + Tests

### Overview

Obliczenie `mtd_diff` per dzień w `compute_calendar_pnl()`, dodanie pola do modelu Pydantic, pokrycie testami.

### Changes Required

#### 1. `src/portfolio_calendar.py`

**File**: `src/portfolio_calendar.py`

**Intent**: Przed pętlą po dniach obliczyć baseline MTD. W gałęzi `state='data'` dodać `mtd_diff = portfolio_value - baseline_value`. We wszystkich pozostałych gałęziach (`weekend`, `holiday`, `future`, `no_data`, `partial`) dodać `mtd_diff: None` do dict.

**Contract**: Nowa zmienna `baseline_value: float | None` obliczana przed pętlą:
```python
month_start = date(year, month, 1)
lookback = [r for r in rows if r["snapshot_date"] <= month_start]
baseline_value = (
    max(lookback, key=lambda r: r["snapshot_date"])["portfolio_value"]
    if lookback else None
)
```
Każdy dict w `days` zyskuje klucz `"mtd_diff"` (`float | None`). Sygnatura funkcji i zwracany `{"year", "month", "days"}` nie zmieniają się.

#### 2. `src/api.py`

**File**: `src/api.py`

**Intent**: Dodać nowe pole do modelu Pydantic `PortfolioCalendarDay`, żeby nie było gubione przez `extra="ignore"`.

**Contract**: W `PortfolioCalendarDay` (linia 175) dodać:
```python
mtd_diff: float | None = None
```

#### 3. `tests/test_portfolio_calendar.py`

**File**: `tests/test_portfolio_calendar.py`

**Intent**: Pokryć 5 przypadków MTD: baseline z dnia handlowego, baseline z lookback (dzień 1 = weekend), akumulacja przez miesiąc, brak baseline → None, non-data days → None.

**Contract**: Nowa sekcja `# ── MTD diff ──` z testami używającymi istniejącego `_make_row()`. Testy nie modyfikują istniejących — tylko dodają.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_portfolio_calendar.py` — wszystkie testy przechodzą
- `uv run pytest` — cały suite przechodzi

#### Manual Verification

- `curl "http://localhost:8000/api/portfolio/calendar?year=2026&month=7&portfolio_id=<uuid>" -H "X-API-Key: ..." -H "X-Client-Id: ..."` — każdy dzień z `state='data'` ma `"mtd_diff"` != null; weekendy i święta mają `"mtd_diff": null`
- Dzień 1. miesiąca (gdy trading day) ma `mtd_diff == 0.0`

**Implementation Note**: Po zakończeniu tej fazy i potwierdzeniu automated + manual verification, zatrzymaj się i czekaj na potwierdzenie przed przejściem do Fazy 2.

---

## Phase 2: Frontend — MTD Summary Element

### Overview

Wyświetlenie aktualnego MTD jako elementu podsumowującego pod siatką kalendarza. Element pokazuje MTD dla ostatniego dnia z `state='data'` z dostępnym `mtd_diff`.

### Changes Required

#### 1. HTML — `static/index.html`

**File**: `static/index.html`

**Intent**: Dodać `<div id="pp-cal-mtd-summary"></div>` wewnątrz `#pp-calendar-wrap`, między `#pp-cal-grid` a `#pp-cal-legend` (linia ~2298).

**Contract**: Element domyślnie ukryty (`display:none`); `_renderPortfolioCalendar()` go pokazuje gdy `mtd_diff` dostępny.

#### 2. CSS — `static/index.html`

**File**: `static/index.html`

**Intent**: Ostylować element summary analogicznie do `#pp-cal-legend` (border, border-radius, padding, muted font-size). Kolor tekstu zielony dla dodatniego MTD, czerwony dla ujemnego, muted dla zera.

**Contract**: Klasy CSS w sekcji `<style>` (linia ~427-488):
```css
#pp-cal-mtd-summary {
  margin-top: .5rem; padding: .6rem .75rem;
  background: #fff; border: 1px solid var(--border);
  border-radius: var(--radius-md); font-size: .85rem;
  display: none; /* shown by JS when mtd_diff available */
}
#pp-cal-mtd-summary.mtd-gain { color: var(--positive); font-weight: 600; }
#pp-cal-mtd-summary.mtd-loss { color: var(--negative); font-weight: 600; }
#pp-cal-mtd-summary.mtd-zero { color: var(--text-muted); }
```

#### 3. JavaScript — `static/index.html`

**File**: `static/index.html`

**Intent**: W `_renderPortfolioCalendar()` (linia ~2567) znaleźć ostatni dzień z `state='data'` i `mtd_diff !== null`, sformatować tekst "MTD: +X PLN" / "MTD: −X PLN" i wstrzyknąć do `#pp-cal-mtd-summary`. Gdy brak MTD — ukryć element.

**Contract**: Po pętli budującej siatke:
```javascript
const mtdEl = $('pp-cal-mtd-summary');
const lastMtd = [...data.days].reverse().find(
  d => d.state === 'data' && d.mtd_diff !== null && d.mtd_diff !== undefined
);
if (lastMtd) {
  const sign = lastMtd.mtd_diff >= 0 ? '+' : '−';
  const amt = Math.round(Math.abs(lastMtd.mtd_diff));
  mtdEl.textContent = `MTD: ${sign}${amt} PLN`;
  mtdEl.className = lastMtd.mtd_diff > 0 ? 'mtd-gain'
                  : lastMtd.mtd_diff < 0 ? 'mtd-loss' : 'mtd-zero';
  mtdEl.style.display = '';
} else {
  mtdEl.style.display = 'none';
}
```

### Success Criteria

#### Automated Verification

- `uv run pytest` — cały suite przechodzi (brak regresji backendowych)

#### Manual Verification

- Otwarcie kalendarza portfela w przeglądarce → pod siatką widoczny element "MTD: +X PLN" lub "MTD: −X PLN"
- Kolor zielony dla dodatniego MTD, czerwony dla ujemnego
- Nawigacja do poprzedniego miesiąca — MTD aktualizuje się poprawnie
- Gdy portfel nie ma danych (pusty portfel) — element MTD jest ukryty
- Mobilny viewport (< 600px) — element czytelny i nie łamie layoutu

---

## Testing Strategy

### Unit Tests

- MTD = 0 gdy baseline row = day 1 (baseline_value == portfolio_value[day_1])
- MTD rośnie monotonicznie przy zysku przez kilka dni
- Lookback jako baseline gdy day 1 = weekend (snapshot_date < month_start)
- baseline_value = None gdy brak jakichkolwiek rows ≤ month_start → mtd_diff = None dla wszystkich
- Non-data days (weekend, holiday, no_data, partial) mają `mtd_diff = None`

### Manual Testing Steps

1. Zaloguj się, przejdź do zakładki "Mój portfel" → "Kalendarz"
2. Sprawdź bieżący miesiąc — element MTD widoczny pod siatką
3. Nawiguj do poprzedniego miesiąca — MTD aktualizuje się
4. Sprawdź miesiąc bez danych — element MTD ukryty
5. Sprawdź miesiąc zaczynający się w weekend (np. sierpień 2026, sobota) — MTD działa

## References

- Research: `context/changes/pul-68/research.md`
- Prior calendar implementation: `context/archive/2026-06-29-pul-59-portfolio-calendar/plan.md`
- Compute function: `src/portfolio_calendar.py:26-115`
- Pydantic model: `src/api.py:175-184`
- API handler: `src/api.py:692-724`
- BQ query (35-day lookback): `db/bigquery.py:362-457`
- Frontend render: `static/index.html:2567-2608`
- Tests: `tests/test_portfolio_calendar.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Backend — Compute + Pydantic + Tests

#### Automated

- [x] 1.1 `uv run pytest tests/test_portfolio_calendar.py` — wszystkie testy przechodzą
- [x] 1.2 `uv run pytest` — cały suite przechodzi

#### Manual

- [x] 1.3 API zwraca `mtd_diff` dla dni `state='data'`, null dla reszty
- [x] 1.4 Dzień 1. miesiąca (trading day) ma `mtd_diff == 0.0`

### Phase 2: Frontend — MTD Summary Element

#### Automated

- [ ] 2.1 `uv run pytest` — brak regresji

#### Manual

- [ ] 2.2 Element "MTD: +X PLN" widoczny pod siatką kalendarza
- [ ] 2.3 Kolor zielony/czerwony zgodny ze znakiem MTD
- [ ] 2.4 Nawigacja między miesiącami aktualizuje MTD
- [ ] 2.5 Pusty portfel → element MTD ukryty
