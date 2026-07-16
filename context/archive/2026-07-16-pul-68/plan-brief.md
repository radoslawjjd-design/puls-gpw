# Portfolio Calendar MTD Value Difference — Plan Brief

> Full plan: `context/changes/pul-68/plan.md`
> Research: `context/changes/pul-68/research.md`

## What & Why

Dodanie pola MTD (month-to-date różnica wartości portfela) do widoku kalendarza. Użytkownik widzi jak portfel zmienił się od początku miesiąca do dziś — niezależnie od dziennych wahań.

## Starting Point

Kalendarz portfela (PUL-59) już działa: `GET /api/portfolio/calendar` zwraca per-dzień `pnl_abs` (dzienny P&L). BQ query w `db/bigquery.py:362` pobiera 35-dniowy lookback — dane z końca poprzedniego miesiąca są już dostępne. Brakuje tylko obliczenia MTD i jego wyświetlenia.

## Desired End State

Pod siatką kalendarza pojawia się element "MTD: +X PLN" lub "MTD: −X PLN" pokazujący skumulowaną zmianę od początku miesiąca do ostatniego dnia handlowego. Kolor zielony/czerwony zgodny ze znakiem. Element znika gdy portfel nie ma danych.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Baseline gdy day 1 = weekend | Ostatni trading day ≤ day 1 | MTD działa dla każdego miesiąca, lookback już w BQ rows | Plan |
| UI placement | Osobny element pod siatką (nie w komórce) | Nie zagęszcza komórek (56px min-height), spójne z legendą | Plan |
| Które stany dostają MTD | Tylko `state='data'` | Spójne z `pnl_abs`, partial ma niekompletne ceny | Plan |
| Zmiana BQ query | Brak — 35-day lookback wystarczy | Lookback rows są już w `rows` przekazywanych do compute | Research |

## Scope

**In scope:**
- `mtd_diff: float | None` per dzień w API response
- Baseline = last trading day ≤ day 1 of month
- Summary element pod siatką kalendarza
- Unit tests dla compute logic

**Out of scope:**
- MTD wewnątrz każdej komórki siatki
- MTD dla stanów partial/weekend/holiday/no_data/future
- Historyczny wykres MTD
- Zmiany w BQ query

## Architecture / Approach

Lokalna zmiana logiki obliczeniowej — 3 pliki, 0 nowych endpointów, 0 zmian SQL. Baseline obliczany z istniejących lookback rows w `compute_calendar_pnl()`. Model Pydantic rozszerzony o jedno pole. Frontend odczytuje ostatni non-null `mtd_diff` z `data.days` i renderuje element podsumowujący.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend — Compute + Model + Tests | `mtd_diff` w API response, testy jednostkowe | Baseline edge case gdy brak lookback rows |
| 2. Frontend — MTD Summary | Element "MTD: +X PLN" pod siatką kalendarza | Layout na mobile (56px komórki już ciasne) |

**Prerequisites:** Działający serwer lokalny z portfelem z danymi historycznymi  
**Estimated effort:** ~1 sesja, 2 fazy

## Open Risks & Assumptions

- Portfele bez historycznych danych (nowe portfele) — `baseline_value = None` → `mtd_diff = None` wszędzie → element ukryty. Akceptowalne.
- Cache 300s: przez max 5 min po deploy stare odpowiedzi bez `mtd_diff`. Frontend musi obsługiwać `null`/`undefined` gracefully (co będzie — sprawdzamy `!== null && !== undefined`).

## Success Criteria (Summary)

- API: każdy dzień `state='data'` ma `mtd_diff` != null; dzień 1. miesiąca (trading day) ma `mtd_diff == 0`
- UI: widoczny element "MTD: X PLN" ze znakiem i kolorem pod siatką kalendarza
- Nawigacja między miesiącami aktualizuje MTD poprawnie
