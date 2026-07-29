---
change_id: xtb-portfolio-import
title: Import positions and dividends from an XTB broker export
status: implementing
created: 2026-07-29
updated: 2026-07-29
archived_at: null
tracking:
  linear: PUL-95
  github: 186
---

## Notes

Import pozycji i dywidend z eksportu XTB. Nowe okno importu z dropdown listą domów
maklerskich (na razie tylko XTB) + wyborem pliku, uruchamiane nowym przyciskiem
obok `Eksport CSV` w Moim portfelu.

### Ustalenia z analizy realnych eksportów (2026-07-29)

Próbki: `export_xtb/PLN_51667579_2006-01-01_2026-07-29.xlsx` (Główny),
`export_xtb/IKZE_52472380_2006-01-01_2026-07-29.xlsx` (IKZE). Nie commitować —
zawierają dane osobowe rachunku.

**Format to historia transakcji, nie zrzut pozycji.** Dwa arkusze: `Cash Operations`
(467 / 106 wierszy) i `Closed Positions` (72 / 26). Arkusza z otwartymi pozycjami
nie ma — trzeba je rekonstruować.

**Rekonstrukcja: kupna minus sprzedaże z `Cash Operations`.** Komentarze mają postać
`OPEN BUY 9/11 @ 188.40` i `CLOSE BUY 31/31.7931 @ 33.330`. Pierwsza liczba to
zrealizowany wolumen, druga to całe zlecenie — jedno zlecenie rozbija się na kilka
wierszy (fill-e). Wzięcie drugiej liczby dubluje wolumen.

Walidacja krzyżowa przeszła: dla **każdego** tickera w obu plikach suma sprzedaży
z `Cash Operations` zgadza się co do 4 miejsc po przecinku z wolumenem z arkusza
`Closed Positions`.

**Średnia cena liczona FIFO po pozostałych lotach**, nie średnia ważona ze wszystkich
kupn. FIFO odtwarza dokładnie to, co XTB pokazuje w UI. Różnice są duże, np. SNT
284,28 (wszystkie) vs 297,90 (FIFO), XTB 72,17 vs 74,64.

Weryfikacja liczbowa vs stan produkcyjny (koszt nabycia, pozycja po pozycji):
IKZE różnica −0,47 zł na 8 pozycjach, Główny 119,80 zł — z czego 121 zł to literówka
w CBF (199,40 zamiast 188,40), reszta to zaokrąglenia groszowe.

**Ślepa plamka formatu: dywidendy rzeczowe (spin-off) nie występują w eksporcie.**
S2B (Syn2Bio, 4 szt.) otrzymane za posiadanie akcji Synektika, siedzi na rachunku
51667579, ale nie ma go w pliku pod żadnym tickerem ani nazwą — bo nie jest ani
ruchem gotówki, ani pozycją zamkniętą. Sprawdzone wyczerpująco: pełny inwentarz
28 + 17 tickerów, brak ukrytych arkuszy/wierszy/kolumn. To samo powtórzy się
z Shoperem i Cyber_Folks, więc jest to stała właściwość, nie przypadek brzegowy.

Konsekwencja — **import nie może być destrukcyjny**:
- ticker jest w pliku → nadpisz wartościami z pliku (to naprawia CBF)
- tickera nie ma w pliku → zostaw nietknięty, ale pokaż w podglądzie w osobnej
  sekcji „są w portfelu, nie ma ich w pliku", żeby S2B był widoczny, a nie cicho
  pominięty

S2B ma `avg_buy_price` 0,01 zł celowo — tyle pokazuje XTB dla pozycji nabytej
za darmo. To nie jest prowizorka do naprawy.

**Dywidendy** — dane są bogate i wymagają nowego magazynu (dziś nie ma gdzie ich
trzymać):

| | Główny | IKZE |
|---|---|---|
| 2025 | 1 253,40 brutto / −238,17 podatek / 1 015,23 netto (57 wypłat) | 57,80 / 0 / 57,80 (2) |
| 2026 | 1 037,31 / −197,11 / 840,20 (32) | 371,95 / 0 / 371,95 (11) |
| razem | 2 290,71 / −435,28 / 1 855,43 | 429,75 / 0 / 429,75 |

Podsumowanie z dropdownem lat (2025, 2026, wszystkie — lista rozszerza się sama),
podział na spółki jest dostępny bez dodatkowej pracy (Główny: KRU 722 zł, XTB 444,
VOT 421, PAS 189). **IKZE ma zerowy podatek u źródła**, więc brutto = netto —
podsumowanie musi pokazywać obie kwoty, inaczej zestawienie kont wygląda na zepsute.
Nazwa musi brzmieć „dywidendy gotówkowe" — rzeczowych w plikach nie ma.

### Pozostałe gotchas parsera

- Ułamkowe akcje wszędzie (`0.4962`, `31.7931`); `shares` i `avg_buy_price` są FLOAT w BQ
- Instrumenty zagraniczne (`.DE`): cena w komentarzu w EUR, kwota w `Amount` w PLN;
  kurs wyliczalny jako `amount / (vol * price)`. Dziś wszystkie zamknięte, ale
  parser potrzebuje polityki
- Wiersz `Total` na końcu obu arkuszy — pomijać
- Mapowanie tickerów: obcięcie sufiksu `.PL`. Zweryfikowane w BQ — wszystkie
  20 otwartych tickerów istnieje w `companies` / `etf_instruments` (w tym ETF-y
  `ETFBW20TR`, `ETFBS80TR`, `ETFBM40TR`)
- Typ konta wykrywalny z nazwy pliku / nagłówka (`PLN_` vs `IKZE_`) → `portfolio_type`
  (`glowny` / `ikze`), dobry domyślny wybór portfela w UI

### Punkty zaczepienia w kodzie

- `static/index.html:3671` — `#pp-export-csv-btn` obok `#pp-add-toggle-btn`
  w `#pp-table-wrap`; nowy przycisk importu ląduje tuż za nim
- `src/api.py:816` — `POST /api/portfolio/positions`, walidacja
  `shares <= 0 or avg_buy_price <= 0` → 422
- `db/bigquery.py` — `user_portfolio_positions`, `list_distinct_portfolio_tickers`

### Zakres

Docelowo trzy rzeczy: silnik rekonstrukcji lotów FIFO z historii transakcji,
import pozycji z podglądem, oraz widok dywidend z nowym magazynem danych.
Do rozbicia na slice'y przy planowaniu, żeby pierwszy dał się wdrożyć i zweryfikować
osobno.
