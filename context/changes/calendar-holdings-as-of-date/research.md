---
date: 2026-07-30T23:49:38+0200
researcher: Radek
git_commit: 8ca572354ce3bf215e7410125ecbc7f3d12e6666
branch: pul-103-calendar-holdings-as-of-date
repository: puls-gpw
topic: "PUL-103 — kalendarz i wykres liczą P&L z dzisiejszych stanów posiadania rzutowanych na każdy dzień historii"
tags: [research, codebase, bigquery, portfolio-calendar, portfolio-history, user-broker-operations, pul-103]
status: complete
last_updated: 2026-07-30
last_updated_by: Radek
---

# Research: holdings as of each day zamiast dzisiejszych akcji na każdym dniu

**Date**: 2026-07-30T23:49:38+0200
**Researcher**: Radek
**Git Commit**: `8ca5723`
**Branch**: `pul-103-calendar-holdings-as-of-date`
**Repository**: puls-gpw

## Research Question

Kalendarz P&L (`get_portfolio_calendar_data`, `db/bigquery.py:362`) i wykres wartości
(`get_portfolio_history`, `db/bigquery.py:489`) robią `CROSS JOIN` bieżącego snapshotu
`user_portfolio_positions` z każdym dniem sesyjnym. Dzisiejsza liczba akcji trafia więc na
każdą datę historii — również na czerwiec 2024, siedem miesięcy przed pierwszą transakcją.
Czy da się zrekonstruować stan posiadania na każdy dzień z `user_broker_operations`, co
przy tym pęknie, i jakie wcześniejsze decyzje projektowe nie wolno przy tym cofnąć?

Zakres ustalony z userem: śledztwo „Δ 123,11 vs XTB" **poza zakresem** (osobny wątek);
koszt nabycia (`avg_buy_price`) — **zbadany jako tło**, nieplanowany.

## Summary

**Rekonstrukcja działa i jest zweryfikowana na produkcji** — ale nie jako zamiennik
snapshotu, tylko jako **korekta wsteczna nad nim**.

Uruchomiłem na realnym BQ porównanie `SUM(±volume)` z operacji przeciw
`user_portfolio_positions.shares` dla wszystkich 5 portfeli. **Dla każdego tickera
akcyjnego rekonstrukcja zbiega do przechowywanej liczby akcji co do 4 miejsc po
przecinku.** Rozjazd występuje w dokładnie trzech wierszach — i wszystkie trzy są
z definicji poza zasięgiem operacji:

| portfel | ticker | z operacji | w pozycjach | reszta |
| -- | -- | -- | -- | -- |
| `10414536…` (IKZE) | `_CASH` | — | 2 160,11 | 2 160,11 |
| `d49d0121…` (Główny) | `_CASH` | — | 84,03 | 84,03 |
| `626e9da1…` (ręczny) | `XTB` | — | 1,0 | 1,0 |

To jest silniejszy wynik, niż zakładał ticket. Wynika z niego kształt naprawy:

> **`shares(dzień) = dzisiejsze_akcje − Σ(±volume operacji późniejszych niż ten dzień)`**

Ta jedna formuła załatwia wszystkie przypadki naraz, bo `reszta` (to, czego operacje nie
tłumaczą) zostaje stała w czasie zamiast zniknąć:

- ticker z pełną historią operacji → reszta = 0 → czysta rekonstrukcja, zero przed pierwszym kupnem ✓
- `_CASH`, pozycja ręczna, dywidenda rzeczowa (S2B) → brak operacji → reszta = dzisiejsze akcje → linia płaska zamiast zniknięcia ✓
- ticker sprzedany do zera (wiersz pozycji skasowany przy imporcie) → dzisiejsze akcje = 0, ale suma operacji po tym dniu jest ujemna → **historia go odzyskuje** ✓
- eksport zaczynający się po zakupie (oversell) → reszta dodatnia pochłania lukę zamiast dawać **ujemne akcje** ✓
- **prawa krawędź wykresu = wartość z „Mój portfel"** — bo na dziś nie ma operacji „po", więc formuła daje dokładnie `dzisiejsze_akcje`. To jest twardy, wprost zapisany niezmiennik z PUL-100 (`context/archive/2026-07-26-history-coverage-gate/plan.md:108-117`), który naiwna rekonstrukcja od zera by złamała.

Naiwne „licz stan wyłącznie z operacji" (literalna treść Fazy 1 w tickecie) **złamałoby
produkcję**: portfel `626e9da1…` ma 1 pozycję i 0 operacji, więc dostałby pusty kalendarz
i pusty wykres — regresję, nie korektę. Gotówka zniknęłaby z obu portfeli, a sumowanie
`amount_pln` jej nie odtworzy (zmierzony rozjazd 84,03 vs 143,94 — 59,91 zł z pominiętych
instrumentów zagranicznych, `src/brokers/xtb.py:256-260`).

Druga rzecz, której ticket nie przewidział: **granica inception nie wystarcza jako
`MIN(occurred_at)`**. Dla portfela bez importu takiej daty nie ma, a
`user_portfolio_positions.created_at` jest bezużyteczne — na prodzie wynosi 2026-07-29/30
dla **wszystkich** zaimportowanych pozycji, czyli datę importu, nie zakupu. Właściwym
proxy dla portfela ręcznego jest `user_portfolios.created_at`.

Trzecia: **BOCF z PUL-100 nie znika, tylko zawęża się dokładnie do tickerów resztowych** —
i to jest argument za jego zachowaniem, nie przeciw. Szczegóły w „Architecture Insights".

## Detailed Findings

### 1. Dwie zepsute funkcje — czym się różnią

Obie mają ten sam defekt, ale **inną semantykę czasu**, więc jedna poprawka nie wystarczy.

| | kalendarz `db/bigquery.py:362` | wykres `db/bigquery.py:489` |
| -- | -- | -- |
| mierzy | **przepływ**: `SUM(shares × zmiana_kwotowa)` | **stan**: `SUM(shares × close)` |
| dodatkowo | `prices_found` / `total_positions` | `pnl = value − Σ(shares × avg_buy_price)` |
| feralny `CROSS JOIN` | `:423` | `:605` |
| wypełnianie cen | LOCF (`:440`) | LOCF + BOCF (`:611-620`) |
| dedup cen | **brak — podatne na duplikaty** | `QUALIFY ROW_NUMBER` (`:582`) |
| okno skanu | miesiąc + 10 dni wstecz | `start_date − 400 dni` |

Dla wykresu czasowo-świadome `shares` **nie wystarczą do naprawy `pnl_pln`** — bo
`avg_buy_price` (`db/bigquery.py:717`) to też jedna liczba bez wymiaru czasu. Po tej
zmianie wykres PnL będzie miał poprawne wagi i wciąż błędną bazę kosztową. To trzeba
powiedzieć wprost w planie, a nie zostawić jako niespodziankę.

### 2. Rekonstrukcja — co ją psuje

Zweryfikowane w kodzie, w kolejności wagi:

1. **Dywidendy rzeczowe (spin-off) nie istnieją w eksporcie XTB.**
   `context/archive/2026-07-29-xtb-portfolio-import/change.md:45-51`: S2B (Syn2Bio, 4 szt.)
   *„nie ma go w pliku pod żadnym tickerem ani nazwą — bo nie jest ani ruchem gotówki, ani
   pozycją zamkniętą. […] To samo powtórzy się z Shoperem i Cyber_Folks, więc jest to stała
   właściwość, nie przypadek brzegowy."*
   **Stan dziś:** sprawdziłem prod — `S2B` nie ma w `user_portfolio_positions` w żadnym
   portfelu. Ślepa plamka jest realna jako właściwość formatu, ale w tej chwili nie ma
   ani jednego wiersza, który by ją manifestował. Formuła resztowa i tak ją obsłuży.

2. **Pozycje dodane ręcznie nie mają żadnych operacji.** `POST /api/portfolio/positions`
   (`src/api.py:1034-1060`) woła tylko `upsert_user_portfolio_position`;
   `merge_user_broker_operations` jest wołane z **jednego** miejsca — commitu importu
   (`src/api.py:1318`). Na prodzie: portfel `626e9da1…`, 1 pozycja, 0 operacji.

3. **Gotówki nie da się zrekonstruować sumowaniem.** `_CASH` (`db/bigquery.py:1080`) trafia
   wyłącznie do `user_portfolio_positions`, z wiersza `Total` eksportu
   (`extract_cash_balance`, `src/brokers/xtb.py:251-274`). Suma `amount_pln` po
   zaimportowanych operacjach daje **143,94 zamiast 84,03** — 59,91 zł rozjazdu z
   pominiętych instrumentów zagranicznych, przypięte testem
   (`tests/test_brokers_xtb.py:457-474`). Co gorsza błąd jest **zależny od ścieżki w
   czasie**: zagraniczne transakcje otwierano i zamykano w konkretnych dniach, więc
   odtworzony szereg gotówki dryfuje zmiennie, a nie o stałą kalibrowalną wartość.
   `_cash_position` (`src/api.py:435-462`) już raz w tym projekcie kosztował awarię
   (PUL-95 F1: `None` i `0.0` szły tą samą ścieżką, więc gotówka nigdy nie spadała do zera).

4. **Skasowanie pozycji ręcznie nie kasuje operacji.** `DELETE /api/portfolio/positions/{ticker}`
   (`src/api.py:1062-1081`) zostawia operacje; jedynym miejscem kasującym z
   `user_broker_operations` jest kaskada usunięcia portfela (`db/bigquery.py:1209-1240`).
   Rekonstrukcja wskrzesi celowo usunięty walor w historii.

5. **Oversell.** `_consume_oldest` (`src/brokers/xtb.py:298-309`) po cichu odrzuca nadmiar,
   więc `reconstruct_positions` **podłoguje na zerze**; goły `SUM` w SQL zejdzie **poniżej
   zera**. Odpala się, gdy okno eksportu zaczyna się po zakupie — a nigdzie nie
   przechowujemy „Date from" eksportu, więc z samej tabeli tego nie wykryjesz.
   Formuła resztowa to neutralizuje.

6. **Pył zmiennoprzecinkowy.** Python traktuje `shares <= 1e-9` jako zamknięte
   (`src/brokers/xtb.py:191`); goły `SUM` zostawi ~1e-13 i wyrenderuje pozycję-widmo.

7. **Strefa czasowa.** `occurred_at` zapisywane jest jako naiwny `isoformat()`
   (`src/api.py:482`) z naiwnych dat openpyxl, więc BQ czyta je jako **UTC**; nagłówek
   eksportu to `Date from (UTC)` (`tests/test_brokers_xtb.py:346`). Godziny GPW mapują się
   bezpiecznie, ale to wniosek, nie pomiar — używaj `DATE(occurred_at, 'Europe/Warsaw')`.

**Czego rekonstrukcja NIE psuje:** instrumenty zagraniczne są pomijane spójnie po obu
stronach (`src/brokers/xtb.py:132-139` + walidacja tickera w `src/api.py:1050-1052`), więc
dla *akcji* to nie jest rozjazd — tylko dla gotówki. Filtr `list_broker_trades`
(`op_type IN ('buy','sell') AND ticker IS NOT NULL`, `db/bigquery.py:3436-3437`) jest
semantycznie identyczny z filtrem `reconstruct_positions` (`src/brokers/xtb.py:213`), a
ticker jest normalizowany już przy zapisie (`src/api.py:486`) — join po `ticker` nie
wymaga strippowania `.PL`.

**Wolumen zawsze dodatni, kierunek wyłącznie z `op_type`.** Sprzedaż ma w komentarzu
`CLOSE BUY 5 @ 55.00` (`tests/test_brokers_xtb.py:324`) — słowo „BUY" w wierszu sprzedaży.
Znakowanie po komentarzu odwróciłoby każdą sprzedaż.

### 3. Dane produkcyjne (zweryfikowane `bq`, 2026-07-30)

```
portfel        pozycje  _CASH  operacje  1. operacja  min(created_at pozycji)
d49d0121…  13       1      458       2025-01-28   2026-07-30
6c6fdd5b…  12       0      458       2025-01-28   2026-07-29
10414536…   9       1      102       2025-07-09   2026-07-30
57ed5830…   8       0      102       2025-07-09   2026-07-29
626e9da1…   1       0        0       —            2026-07-22
```

1120 operacji, 2 użytkowników × 2 portfele + 1 portfel ręczny. Typy operacji na prodzie to
dokładnie `{buy, sell, cash, dividend, withholding_tax}` — `_TYPE_MAP`
(`src/brokers/xtb.py:92-104`) jest wyczerpujący dla realnych danych.

Trzy rzeczy z tej tabelki:

- **`positions.created_at` jest bezwartościowe jako inception** — to data importu.
- Pary 458/458 i 102/102 to **dwaj różni użytkownicy**, nie duplikaty. Poprawna
  implementacja musi dawać dla nich **identyczne szeregi** — darmowy test A/B na prodzie.
- Dwa portfele mają `_CASH`, dwa nie — jeden user zrobił powtórny import po PUL-95, drugi
  jeszcze nie. Kalendarz musi to znieść w obie strony.

**~18 miesięcy realnej historii** (od 2025-01-28) jest dostępne do walidacji, a ceny
sięgają **2011-01-03** (`context/archive/2026-07-24-backfill-historical-closes/plan.md:276`),
więc styczeń 2025 da się wycenić. Uwaga: korekta z archiwum GPW (PUL-98) obejmuje tylko
okno od 2025-01-01 i tylko rynek główny; NewConnect i miesiące wcześniejsze to surowy stooq.

### 4. Frontend — co pęknie, gdy dni zaczną znikać

**Kalendarz (`static/index.html`)** — dziś bezpieczny, ale na jednym założeniu:

- JS rozgałęzia się tylko na `'data'`, `'weekend'`, `'holiday'` (`:4892`, `:4909`).
  `'no_data'`, `'partial'`, `'future'` nie mają gałęzi — wpadają w biały `pp-cal-cell`.
  Brak tooltipów, brak ryzyka `NaN` na ekranie.
- **`const firstWeekday = data.days[0].weekday;` (`:4873`) nie jest strzeżone.** Gdyby
  poprawka kiedykolwiek zwróciła pusty `days`, leci `TypeError` — a ponieważ render jest
  wołany wewnątrz `try` w `fetchPortfolioCalendar` (`:4848`), user zobaczy
  *„Błąd ładowania kalendarza."* zamiast pustego miesiąca sprzed inwestowania.
  → **Kontrakt `compute_calendar_pnl`: zawsze pełny miesiąc.** Dni przed inception mają być
  *stanem*, nie brakiem wiersza.
- Renderer wypełnia puste komórki tylko **przed** `days[0]` (`:4874-4878`) i dalej leje
  tablicę sekwencyjnie w siatkę 7-kolumnową. Dziura w środku po cichu przesunęłaby
  wszystkie kolejne dni na złe kolumny — bez błędu.
- Przełącznik miesiąca (`:4707-4725`) **nie ma strażnika out-of-order**, w odróżnieniu od
  wykresu. Nie pogarszamy tego, ale warto wiedzieć.

**Wykres** — obsługa pustego `series` jest czysta (`:5170-5174`, komunikat
*„Brak danych dla tego zakresu"* przed jakąkolwiek matematyką min/max). Ale:

> **Oś X jest indeksowa, nie datowa:** `xFor = i => padL + i * plotW / (n - 1)` (`:5183`).

Szereg dwumiesięczny zamówiony jako `1y` rozciąga się na całą szerokość i jest wizualnie
nie do odróżnienia od pełnego roku. Jedyny uczciwy sygnał to dwie etykiety osi, brane z
danych (`:5223-5224`). **Po tej zmianie obcięcie do inception przestaje być przypadkiem
brzegowym i staje się normą** — przy pierwszej operacji 2025-01-28 zakres `1y` jest jeszcze
pełny, ale `626e9da1…` i każdy nowy user dostaną krótki szereg rozciągnięty na cały wykres.
Naturalnym nośnikiem sprostowania jest istniejąca koperta `notes` + przycisk `(i)`.

**`compute_calendar_pnl` (`src/portfolio_calendar.py`)** znosi zero wierszy bez zarzutu
(`:90-98` → `no_data`, pełna tablica dni; testy `tests/test_portfolio_calendar.py:94`,
`:219`). MTD kumuluje wyłącznie na `state == "data"` (`:111-115`), więc rzadsze wiersze
kurczą je poprawnie.

**Jedna luka semantyczna:** stan wybiera `prices_found > 0` (`:104-109`). Dzień z **zerowym
stanem posiadania** przyszedłby jako `prices_found = 0, total_positions = 0` → `partial`,
co dziś znaczy *„dzień sesyjny, ale nic nie udało się wycenić"* (problem z danymi), a nie
*„portfel jeszcze nie istniał"* (prawda). Oba malują biało, więc nic nie pęka — ale to nie
jest ten sam fakt, a **PUL-104 (eksport CSV) będzie musiał je rozróżnić**. Uwaga: każde
nowe pole wymaga rozszerzenia modelu Pydantic, bo wszystkie mają
`model_config = ConfigDict(extra="ignore")` (`src/api.py:260`, `:273`, `:281`, `:289`, `:302`) —
nowy klucz zostanie **po cichu wycięty**.

### 5. Warstwa API

| | kalendarz | wykres |
| -- | -- | -- |
| endpoint | `src/api.py:1201-1235` | `src/api.py:1237-1285` |
| walidacja | miesiąc 1-12; **rok od −5 do +1** (`:1212`) | `range ∈ {1w,1m,3m,1y}` (`:310`) |
| cache | `calendar:{user}:{pf}:{y}:{m}`, TTL 300 | `history:{user}:{pf}:{range}`, TTL 300 |
| „Wszystkie" | `portfolio_id == _ALL_PORTFOLIOS` → **pomija check własności**, `None` do DB | to samo |
| cudzy portfel | 403 | 403 |

Rok „−5" znaczy, że **pięć lat wstecz jest osiągalne z UI** — i prawie wszystko z tego jest
sprzed inception każdego realnego portfela. To jest powierzchnia buga.

Unieważnianie cache (`_perf_invalidate_portfolio`, `src/api.py:120-134`) już skanuje po
prefiksie i pokrywa oba klucze plus sentinel `all`, więc **po imporcie unieważnienie jest
poprawne bez zmian** — mimo że wynik zacznie zależeć od `user_broker_operations`.

### 6. Testy — co pęknie i gdzie jest jedyna prawdziwa siatka

**Pękną asercje na tekście SQL** (celowe, z lekcji PUL-29):

- `tests/test_bigquery.py:1446` — kalendarz: `LAST_VALUE(close_price IGNORE NULLS) OVER (`,
  `shares * close_ff`, `COUNTIF(close_ff IS NOT NULL)`, `WHERE snapshot_date >= @month_start`,
  plus **negatywne**: brak `shares * close_price`, brak `LAST_VALUE(daily_chg`
- `tests/test_bigquery.py:1573` — wykres: `LAST_VALUE`, `FIRST_VALUE`, `UNBOUNDED FOLLOWING`,
  `DATE_SUB(@start_date`, `covered > 0`, `LEFT JOIN daily`; negatywne: brak `missing = 0`
- `tests/test_api.py:1170,1174,1178,1182` — literalne `"AND portfolio_id = @portfolio_id"`.
  **Te pękną w momencie, gdy filtr portfela przeniesie się na `user_broker_operations`.**
  Harness: `_capture_calendar_query` (`:1129`), `_capture_history_query` (`:1151`).
- `tests/test_bigquery.py:1425` / `:1600` — dokładny zestaw parametrów i arytmetyka
  `lookback_start == month_start − 10 dni`.
- `tests/test_etf_bigquery.py:95` — `"etf_quotes"`, `"COALESCE"`.
- `tests/test_api.py:1105`, `:1117` — `mock_cal.assert_called_once_with(None, _CLIENT_ID, 2026, 6)`
  **pozycyjnie** → każda zmiana sygnatury je łamie.

**Przetrwają:** wszystkie 22 testy `tests/test_portfolio_calendar.py` (czysta funkcja) —
to naturalny dom dla nowego zachowania pre-inception; oraz testy kształtu wyniku
(`tests/test_bigquery.py:1404`, `:1471`, `:1481`, `:1508`, `:1535`, `:1549`, `:1559`).

**e2e nie jest siatką dla SQL-a.** `tests/e2e/conftest.py:686-693` podmienia **całe funkcje**
na poziomie `src.api`, więc zapytanie nigdy się nie wykonuje. Fake'i ignorują `year`/`month`
(`:356`) i `start_date` (`:387`). Gdyby zapytanie zaczęło zwracać mniej dni, e2e nadal
zobaczy `+300 PLN` i przejdzie.

**Krytyczna asymetria, którą trzeba świadomie ominąć:**

- Nowa logika **wewnątrz** `get_portfolio_calendar_data` / `get_portfolio_history`
  (dodatkowe CTE) → e2e nietknięte. ✓
- Nowa funkcja `db.bigquery` **importowana do `src/api.py`** i wołana z endpointu →
  **zielono lokalnie, czerwono w CI**. CI nie ma auth do GCP (`.github/workflows/tests.yml:12-13`),
  a `client = _get_client()` stoi **poza** `try/except BigQueryError`
  (`db/bigquery.py:387`, `:540` vs `try` w `:472`, `:645`), więc leci surowe 500 zamiast
  czystego błędu. Lokalnie ADC załatwia sprawę i test przechodzi po cichu, uderzając w realny
  `espi_ebi`.
  → **Trzymaj nową logikę wewnątrz tych dwóch funkcji.** Jeśli mimo to dojdzie import do
  `src/api.py`, dopisz `patch("src.api.<nazwa>")` do conftestu **w tym samym commicie**.

**Round-trip na realnym BQ (obowiązkowy wg `context/foundation/lessons.md:211-235`).**
Istnieje `scripts/test_bq_portfolio_calendar.py`, ale jest słaby: bierze losowy portfel
`LIMIT 1`, woła zahardkodowany czerwiec 2026 i sprawdza wyłącznie obecność kluczy i typy
(`:63-68`) — nigdy żadnej liczby. Wzorzec do skopiowania to podmiana stałej modułu na
tabelę-jednorazówkę, `scripts/test_bq_broker_operations.py:69-79`:

```python
rt_name = f"{real_name}_rt_{uuid.uuid4().hex[:8]}"
table.expires = datetime.now(timezone.utc) + timedelta(hours=24)  # PRZED create_table
client.create_table(table)
setattr(bq, const_name, rt_name)     # atrybut MODUŁU, nie lokalna nazwa z importu
```
z przywróceniem stałej i `delete_table` w `finally` (`:209-220`). Działa, bo obie funkcje
rozwiązują `_table_ref` **w momencie wywołania** (`db/bigquery.py:396-398`, `:543-545`).

**Nie istnieje jeszcze round-trip typu „zasiej wiersze → sprawdź dokładną liczbę".**
Ten change powinien być pierwszy: podmienić **cztery** tabele (`company_daily_stats`,
`etf_quotes`, `user_portfolio_positions`, `user_broker_operations`), zasiać kupno w środku
okna i sprawdzić, że **dzień przed kupnem ma wartość 0, a dzień kupna `shares × close`**.
To dokładnie ta klasa błędu, której mocki nie widzą.

**Komendy** (zweryfikowane): `uv run pytest` = 934 testy; `uv run pytest --ignore=tests/e2e`
= 788 (szybka pętla, **nie jest bramką merge** — CI zawsze puszcza wszystko);
`uv run pytest tests/e2e` = 146; `tach check` OK. CI woła `uv run pytest --tb=short` w
`tests.yml:44` i `deploy.yml:36`. **W CI nie ma kroku lintu** — `ruff`/`mypy` są dev-depami,
ale żaden workflow ich nie odpala.

## Code References

- `db/bigquery.py:362-486` — `get_portfolio_calendar_data`; feralny `CROSS JOIN positions p` w `:423`
- `db/bigquery.py:489-671` — `get_portfolio_history`; `CROSS JOIN` w `:605`; przyjęte przybliżenie opisane w docstringu `:519-524`
- `db/bigquery.py:3344-3373` — `user_broker_operations`: schemat, klastrowanie `["user_id","ticker"]`, **brak partycjonowania czasowego**
- `db/bigquery.py:3416-3449` — `list_broker_trades`, gotowy czytnik operacji (buy/sell, `ORDER BY occurred_at`)
- `db/bigquery.py:1209-1240` — kaskada usunięcia portfela obejmuje operacje
- `db/bigquery.py:765-774`, `:828-837` — `created_at` przeżywa MERGE (ustawiane tylko w `WHEN NOT MATCHED`)
- `src/portfolio_calendar.py:26-127` — `compute_calendar_pnl`, kontrakt „zawsze pełny miesiąc"
- `src/api.py:435-462` — `_cash_position` (poprawka PUL-95 F1: `None` ≠ `0.0`)
- `src/api.py:465-493` — `_operation_rows`; `ticker` normalizowany przy zapisie (`:486`)
- `src/api.py:1034-1060` — ręczne dodanie pozycji: **zero operacji**
- `src/brokers/xtb.py:92-104` — `_TYPE_MAP`; `:146-149` — `volume`/`unit_price` tylko dla buy/sell
- `src/brokers/xtb.py:251-274` — `extract_cash_balance` + zmierzony rozjazd 84,03 vs 143,94
- `src/brokers/xtb.py:298-309` — `_consume_oldest`, podłogowanie oversell
- `src/portfolio_realized.py:32-125` — FIFO nad zapisanymi operacjami (kandydat do ponownego użycia przy bazie kosztowej)
- `static/index.html:4873` — niestrzeżone `data.days[0].weekday`
- `static/index.html:5183` — indeksowa oś X wykresu
- `tests/e2e/conftest.py:356`, `:387` — fake'i ignorujące `year`/`month` i `start_date`
- `scripts/test_bq_broker_operations.py:69-79`, `:209-220` — wzorzec tabeli-jednorazówki

## Architecture Insights

### Formuła resztowa zamiast zamiany źródła

Sednem jest to, że `user_broker_operations` **nie jest kompletnym rejestrem stanu
posiadania** — jest kompletnym rejestrem *ruchów, które broker widział*. Snapshot
`user_portfolio_positions` jest kompletny co do stanu **dziś**. Żadne z nich osobno nie
wystarcza; razem wystarczają, jeśli traktować operacje jako **cofanie** snapshotu, a nie
jako budowanie od zera.

To odwrócenie ma jeszcze jedną zaletę: jest **monotonicznie samo-naprawiające**. Każda
nowa operacja, którą kiedyś nauczymy się parsować (split, transfer rzeczowy), po prostu
zmniejsza resztę. Nic nie trzeba przepisywać.

### BOCF nie znika — zawęża się

PUL-100 wprowadziło backward-fill, żeby S2B (0,66% portfela, debiut 2026-04-16) nie obcinał
dziewięciu miesięcy historii. `context/archive/2026-07-26-history-coverage-gate/plan.md:99-106`:

> *„Backward-filling contributes a constant `shares × (first_px − avg_buy_price)` to every
> pre-debut day […] This is the same class of approximation PUL-79 already accepted."*

Naiwna lektura: skoro pozycja przed pierwszym kupnem po prostu nie istnieje, BOCF traci
rację bytu. **To jest błąd.** S2B to dokładnie ticker *resztowy* — dywidenda rzeczowa bez
ani jednej operacji. Formuła resztowa utrzymuje go stałym przez całe okno, więc **wciąż
potrzebuje ceny sprzed debiutu**. BOCF zarabia na siebie dokładnie dla tej klasy walorów,
dla której rekonstrukcja jest bezsilna.

Dla tickera *z* operacjami, kupionego w dniu debiutu, `shares` przed debiutem = 0, więc
BOCF wnosi `0 × cena = 0`. Nieszkodliwe. **Dwie korekty się więc nie nakładają — dzielą
zbiór po rozłączności.** To trzeba jawnie zweryfikować w implementacji, nie założyć.

Niezmiennik, którego nie wolno naruszyć (`plan.md:108-117`, *„Do not re-litigate without
new evidence"*): **prawa krawędź wykresu musi równać się wartości z „Mój portfel"**.
Formuła resztowa spełnia go z definicji.

### Czego nie wolno cofnąć

1. **`zmiana_kwotowa` bierzemy wprost, nigdy jako różnicę kolejnych zamknięć.**
   `context/archive/2026-07-27-official-close-source/plan.md:641-648`: procent GPW mierzony
   jest względem kursu odniesienia korygowanego o zdarzenia korporacyjne; naiwne różnicowanie
   myli się na każdej dywidendzie i splicie — `ECHO` 2026-06-24: **+0,10% oficjalnie przeciw
   −7,40% naiwnie**. Czasowo-świadome ma być **wyłącznie `shares`**.
2. **`daily_chg` nigdy nie jest przenoszone do przodu, `close_price` zawsze jest**
   (`db/bigquery.py:429-437`). Dzień bez transakcji nie miał ruchu; ale wyzerowanie ceny
   wyrzuciłoby pozycję z wartości dnia i wyglądałoby jak realna strata.
3. **Zero-fill jest zakazane** (PUL-79 F1) — zmierzony fałszywy skok ~25%.
   Uwaga na nowy wariant tego błędu: *„nie posiadane"* i *„posiadane, ale bez ceny"* to dwa
   różne fakty. Pierwsze wypada z wartości **i z bazy kosztowej**; drugie idzie przez
   LOCF/BOCF i notatkę.
4. **10-dniowy lookback kalendarza istnieje wyłącznie po to, żeby LOCF miał poprzednika dla
   1. dnia miesiąca** (`db/bigquery.py:381-385`). To nie jest baseline P&L — był nim
   kiedyś, został usunięty w PUL-60 i przywrócony w innym celu w PUL-98. Okno skanu operacji
   to trzecia, osobna sprawa; nie mieszaj ich.
5. **`portfolio_id: str | None`, gdzie `None` = „Wszystkie"** (PUL-90) — obie funkcje muszą to
   zachować.
6. **Kalendarz nie jest odporny na duplikaty** — surowe `LEFT JOIN` bez dedupu
   (`context/archive/2026-07-24-backfill-historical-closes/research.md:35`), w odróżnieniu od
   wykresu z `QUALIFY ROW_NUMBER`. Dołożenie kolejnego joinu zwiększa tę powierzchnię.
   Duplikat `(portfolio_id, ticker, occurred_at)` w operacjach rozmnożyłby wiersze dnia.
7. **Klastrowania nie da się zmienić** (`db/bigquery.py:3369-3372`) — `ensure_schema_current`
   umie tylko doklejać kolumny. `["user_id","ticker"]` dobrze obsłuży okno
   `PARTITION BY user_id, ticker`, ale nie przytnie zakresu dat. Przy ~1120 wierszach to bez
   znaczenia.

### Kręgosłup już istnieje

Dywidendy (`get_dividend_summary`) i zrealizowany P/L (`list_broker_trades` +
`compute_realized_pnl`) **już czytają z operacji** i są poprawne z konstrukcji. Kalendarz i
wykres to dwa ostatnie miejsca żyjące na snapshotcie. Plan importu XTB zapowiedział to wprost
(`context/archive/2026-07-29-xtb-portfolio-import/plan.md:86-87`): *„Nie zmieniamy wykresu
wartości historycznej. Daty transakcji zostają zapisane, ale wykorzystanie ich do usunięcia
przybliżenia z PUL-79 to osobna zmiana."* **To jest ta zmiana.**

## Historical Context (from prior changes)

- `context/archive/2026-07-22-pul-79-portfolio-value-history/research.md:32-47` — `portfolio_snapshots`
  odrzucone: kluczowane po `wallet`, bez automatycznego writera, 7 dni danych, miesiąc nieświeże.
  **Odrzucenie dotyczyło tej konkretnej tabeli, nie materializacji w ogóle** — nowa tabela
  prekomputowana nie jest tym zakazana, ale `portfolio_snapshots` wskrzeszać nie wolno.
- `context/archive/2026-07-22-pul-79-portfolio-value-history/plan.md:79-82` — przybliżenie przyjęte
  świadomie i **warunkowo**: *„positions carry no purchase dates"*. Warunek przestał obowiązywać.
- `context/archive/2026-07-26-history-coverage-gate/plan.md:99-117` — BOCF, phantom
  `shares × (first_px − avg_buy_price)`, oraz jawnie odrzucone „pełne wykluczenie" z zakazem
  re-litygacji bez nowych dowodów.
- `context/archive/2026-06-29-pul-59-portfolio-calendar/plan.md:79-81` + `reviews/impl-review.md:87-94`
  — plan zakładał różnice kolejnych wartości; implementacja poszła w `zmiana_kwotowa` (F7,
  „better solution"). PUL-98 dostarczył twardy dowód, dlaczego.
- `context/archive/2026-07-27-official-close-source/plan.md:423` — self-heal koryguje
  `kurs_zamkniecia`, `zmiana_procentowa` **i `zmiana_kwotowa`**; `reviews/impl-review.md:117-128`
  (F4) — wiersz bez procentu w archiwum jest **odrzucany**, więc część wierszy zachowuje
  wartości sprzed PUL-98.
- `context/archive/2026-07-29-xtb-portfolio-import/research.md:454-460` — *„Import XTB to
  pierwsza zmiana, która te daty faktycznie ma. […] niezapisanie oznacza świadome
  dziedziczenie przybliżenia mimo posiadania danych."*
- `context/archive/2026-07-29-xtb-portfolio-import/reviews/impl-review.md:76-82` — commit importu
  to trzy niezależne zapisy BQ bez transakcji; każdy idempotentny, ale częściowa awaria zostawia
  operacje zapisane przy nieaktualnych pozycjach. Rekonstrukcja odziedziczy ten stan.

Brak lekcji w `context/foundation/lessons.md` na temat tych dwóch zapytań — jest za to lekcja
o mockowanych testach BQ (`:211-235`), która wprost narzuca round-trip.

## Related Research

- `context/archive/2026-07-22-pul-79-portfolio-value-history/research.md` — źródła danych dla szeregu wartości
- `context/archive/2026-07-26-history-coverage-gate/research.md` — bramka pokrycia, koperta `{series,notes,excluded}`
- `context/archive/2026-07-24-backfill-historical-closes/research.md` — zasięg cen historycznych, brak dedupu w kalendarzu
- `context/archive/2026-07-29-xtb-portfolio-import/research.md` — model operacji, ślepa plamka spin-offów

## Open Questions

1. **Jak nazwać dzień sprzed inception?** Brak wiersza (→ `no_data`, zero zmian we
   frontendzie) czy nowy stan `pre_inception` (wymaga pola w modelu Pydantic + gałęzi w JS,
   ale rozróżnia „nie istniałeś" od „nie dało się wycenić" — czego **PUL-104 będzie
   potrzebować**). Do rozstrzygnięcia w `/10x-plan`.
2. **Inception dla portfela ręcznego** — `user_portfolios.created_at` jest jedynym sensownym
   proxy, ale to data założenia portfela, nie zakupu. Czy szereg ma być wtedy oznaczony jako
   przybliżony w `notes` (jak proponuje Faza 4 ticketu), czy po prostu obcięty bez komentarza?
3. **Czy obcięty szereg wymaga zmiany osi X wykresu?** Indeksowa oś sprawia, że dwumiesięczna
   historia w zakresie `1y` wygląda jak pełny rok. To osobna zmiana we froncie — czy wchodzi
   tutaj, czy idzie jako follow-up?
4. **Rekonstrukcja wskrzesza pozycje skasowane ręcznie.** Dla *historii* to formalnie
   poprawne (naprawdę je posiadałeś), ale user mógł je skasować, bo dane były błędne. Zostawić
   czy dodać tombstone?
5. **Ile kosztuje dodatkowe CTE?** Wykres już mierzy ~1,6 s przy użytkowniku
   (`db/bigquery.py:536`). Round-trip powinien mieć asercję na górny limit czasu, wzorem
   `MERGE_BUDGET_SECONDS` w `scripts/test_bq_broker_operations.py:43`.

### Tło: baza kosztowa (zbadane, nieplanowane)

`avg_buy_price` (`db/bigquery.py:717`) to jedna liczba bez czasu. **Jednym przebiegiem
okienkowym FIFO się nie policzy** — okno da średnią ważoną kosztem, a to nie to samo:
testy przypinają SNT `avg = 200,00` (FIFO) przeciw `150,00` (średnia ważona)
(`tests/test_brokers_xtb.py:85-98`), a na realnych danych SNT to **284,28 (ważona) vs
297,90 (FIFO)** (`context/archive/2026-07-29-xtb-portfolio-import/change.md:35-37`).
To ~5% różnicy, nie zaokrąglenie, i zaprzeczyłoby liczbie pokazywanej dziś w UI.

Najtańsza uczciwa droga to **Python, nie SQL**: `compute_realized_pnl`
(`src/portfolio_realized.py:32-125`) już przechodzi całą historię we właściwej kolejności i
utrzymuje dokładnie tę strukturę lotów, której potrzeba — jest celowo czysty (*„serves a
freshly parsed export and rows read back out of user_broker_operations"*, `:3-4`) i już
karmiony z `list_broker_trades` (`src/api.py:1404`). Nie da się go użyć wprost (konsumuje
loty i zwraca tylko sumy zrealizowane), ale jego pętla wewnętrzna po refaktorze mogłaby
emitować **snapshot ocalałych lotów na każdej granicy daty** i obsłużyć obu konsumentów.
Przy 1120 wierszach koszt jest bez znaczenia. Uwaga: `compute_realized_pnl` ma tiebreak
buy-przed-sell dla identycznych znaczników czasu (`:20-29`), którego `reconstruct_positions`
**nie ma** (`src/brokers/xtb.py:216`) — dwie implementacje FIFO, dwie różne reguły.

Rekomendacja: osobny ticket, po tym change'u, z tym change'em jako prerekwizytem —
dokładnie jak przewiduje sekcja „Related" w PUL-103.
