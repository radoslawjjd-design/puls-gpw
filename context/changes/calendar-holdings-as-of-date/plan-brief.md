# Holdings as of each day — Plan Brief

> Full plan: `context/changes/calendar-holdings-as-of-date/plan.md`
> Research: `context/changes/calendar-holdings-as-of-date/research.md`

## What & Why

Kalendarz P&L raportuje dzienne zyski i straty za dni, w których portfel nie istniał —
włącznie z całym 2024 rokiem, ponad rok przed pierwszą transakcją. Ta sama wada zniekształca
każdą datę *wewnątrz* okresu inwestowania: pozycja kupiona w lipcu 2026 wnosi swój dzienny
ruch do stycznia 2025. Wykres wartości ma dokładnie ten sam defekt, tylko lepiej ukryty
zakresem `1y`.

## Starting Point

Oba zapytania (`get_portfolio_calendar_data`, `get_portfolio_history`) robią
`CROSS JOIN` bieżącego snapshotu `user_portfolio_positions` z osią dni sesyjnych — tabela
pozycji nie ma wymiaru czasu, więc dzisiejsze liczby akcji trafiają na każdą datę historii.
To było przybliżenie przyjęte **świadomie i warunkowo**: warunkiem był brak dat transakcji.
Import XTB (PUL-95) ten warunek usunął — `user_broker_operations` ma 1120 operacji z
`occurred_at` od 2025-01-28.

## Desired End State

Czerwiec 2024 renderuje się całkowicie biało. Styczeń 2025 rusza 29-go, w dniu pierwszego
zakupu KRU. Kalendarz IKZE milczy przed 2025-07-09. Każdy dzień wewnątrz zakresu liczy się
z akcji posiadanych **tego dnia**. Prawa krawędź wykresu nadal równa się wartości z „Mój
portfel" co do grosza, a portfel prowadzony ręcznie nie regresuje do pustego widoku.

## Key Decisions Made

| Decyzja | Wybór | Dlaczego | Źródło |
| -- | -- | -- | -- |
| Kierunek rekonstrukcji | **Korekta wsteczna nad snapshotem**, nie budowa od zera | Rekonstrukcja zbiega do snapshotu co do 4 miejsc; reszta zostaje stała zamiast zniknąć, więc gotówka i pozycje ręczne przeżywają | Research |
| Dzień sprzed inception | Brak wiersza → `no_data` | Zero zmian we froncie i modelach; ścieżka już pokryta testami | Plan |
| Gotówka `_CASH` | Stała w czasie (reszta) | Suma `amount_pln` myli się o 59,91 zł zależnie od ścieżki w czasie i łamie niezmiennik PUL-100 | Plan |
| Ujawnienie reszt | Skrypt round-trip + log DEBUG | Spełnia wymóg ticketu bez wiecznej stopki na każdym wykresie | Plan |
| Zakres | Kalendarz i wykres, osobne fazy | Zostawienie wykresu = dwa sprzeczne widoki tego samego portfela obok siebie | Plan |
| Oś X wykresu | Bez zmian, notatka w `notes` | Oś jest indeksowa; naprawa geometrii dotyka SVG, gradientu i obu slotów | Plan |
| Portfel bez importu | Granica z `user_portfolios.created_at` | `positions.created_at` to data importu (2026-07-29/30), bezużyteczna | Research |
| BOCF (PUL-100) | Zachować + test rozłączności | Chroni tickery resztowe (spin-off), dla których rekonstrukcja jest bezsilna | Research |
| Siatka bezpieczeństwa | Nowy round-trip z zasianymi danymi | Mocki nie parsują SQL-a, e2e podmienia całe funkcje — nic nie łapie złej arytmetyki | Plan |

## Scope

**W zakresie:** wymiar czasu w obu zapytaniach; granica inception (operacje albo data
założenia portfela); dedup cen w kalendarzu; notatka o skróconym zakresie wykresu; round-trip
na realnym BQ; weryfikacja produkcyjna.

**Poza zakresem:** baza kosztowa (`avg_buy_price` — osobny ticket, FIFO wymaga Pythona);
oś X wykresu; modele i renderer kalendarza; usunięcie BOCF; tabela prekomputowana;
śledztwo Δ 123,11 PLN vs XTB.

## Architecture / Approach

```
shares(dzień) = dzisiejsze_akcje − Σ(±volume operacji późniejszych niż ten dzień)
```

Operacje **cofają** snapshot, zamiast go zastępować. Jedna formuła obsługuje wszystkie
przypadki, bo reszta (to, czego operacje nie tłumaczą) zostaje stała: ticker z pełną
historią daje czystą rekonstrukcję; gotówka i pozycje ręczne dają linię płaską; ticker
sprzedany do zera **wraca** w historii; oversell nie produkuje ujemnych akcji; a dzisiejszy
dzień z definicji daje dokładnie dzisiejsze akcje — czyli niezmiennik PUL-100 „prawa
krawędź = Mój portfel" spełnia się sam.

Uniwersum tickerów to `pozycje ∪ operacje` (FULL OUTER JOIN), okno partycjonowane po
`(portfolio_id, ticker)`, dni grupowane w `Europe/Warsaw`, wiersze poniżej progu pyłu
`1e-9` odfiltrowane.

## Phases at a Glance

| Faza | Co dostarcza | Główne ryzyko |
| -- | -- | -- |
| 1. Holdings w kalendarzu | Dzień liczy się z właściwych akcji | Uniwersum tickerów — pominięcie waloru sprzedanego do zera |
| 2. Granica inception | Czerwiec 2024 biały | Portfel bez operacji regresuje do pustego widoku |
| 3. Wymiar czasu w wykresie | Spójność obu widoków | Nałożenie się z BOCF i bramką `covered > 0` |
| 4. Round-trip na BQ | Jedyna realna siatka na arytmetykę | Wyciek realnych wierszy do asercji przy niepełnej podmianie tabel |
| 5. Weryfikacja produkcyjna | Dowód na realnych danych | — |

**Prerequisites:** dostęp do realnego BQ (ADC) do Faz 1, 2, 4 i 5; merge do master → CI
deployuje samo.
**Estimated effort:** ~2-3 sesje; Fazy 1-2 są największe, Faza 3 to w dużej mierze
przeniesienie gotowych CTE.

## Open Risks & Assumptions

- **Ślepa plamka spin-offów jest trwała.** Dywidendy rzeczowe nie występują w eksporcie XTB
  (archiwum: „to samo powtórzy się z Shoperem i Cyber_Folks"). Formuła resztowa je utrzyma,
  ale historycznie jako stałą, nie jako realny moment nabycia.
- **Rekonstrukcja wskrzesza pozycje skasowane ręcznie** — dla historii formalnie poprawne,
  ale user mógł je skasować z powodu błędnych danych. Świadomie zostawione bez tombstone'a.
- **Granica dla portfela ręcznego to data założenia portfela, nie zakupu akcji** —
  przybliżenie zmniejszone, nie usunięte.
- **`user_portfolio_positions.portfolio_id` jest NULLABLE** (osierocone pozycje sprzed
  PUL-64) — join musi to znieść.
- **Baza kosztowa zostaje nieczasowa**, więc `pnl_pln` po tej zmianie ma poprawne wagi i
  wciąż dzisiejszą `avg_buy_price` przeciw każdemu dniu historii.

## Success Criteria (Summary)

- Kalendarz i wykres nie pokazują żadnej liczby za dzień, w którym portfel nie istniał.
- Dzień wewnątrz zakresu liczy się z akcji posiadanych tego dnia — zweryfikowane asercją
  liczbową w round-tripie, nie tylko obecnością słów w SQL-u.
- Prawa krawędź wykresu nadal zgadza się z „Mój portfel" co do grosza, a żaden istniejący
  widok nie stracił danych.
