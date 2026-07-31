# Holdings as of each day — kalendarz i wykres wartości

## Overview

Kalendarz P&L i wykres wartości portfela liczą historię z **dzisiejszych** liczb akcji
rzutowanych na każdy dzień sesyjny. Skutek: portfel raportuje dzienne zyski i straty za
czerwiec 2024 — siedem miesięcy przed pierwszą transakcją — a każdy dzień *wewnątrz* okresu
inwestowania jest błędnie ważony, bo pozycja kupiona w lipcu 2026 wnosi swój ruch do
stycznia 2025.

Naprawiamy to, nadając stanom posiadania wymiar czasu odtworzony z `user_broker_operations`,
i ograniczając szereg do momentu powstania portfela.

## Current State Analysis

Obie funkcje mają identyczny defekt w identycznym miejscu — `CROSS JOIN` bieżącego
snapshotu pozycji z osią dni:

| | kalendarz `db/bigquery.py:362` | wykres `db/bigquery.py:489` |
| -- | -- | -- |
| mierzy | **przepływ**: `SUM(shares × zmiana_kwotowa)` | **stan**: `SUM(shares × close)` |
| feralny join | `:423` | `:605` |
| wypełnianie cen | LOCF (`:440`) | LOCF + BOCF (`:611-620`) |
| dedup cen | **brak** | `QUALIFY ROW_NUMBER` (`:582`) |
| bramka | brak | `covered > 0` (`:635`) |

Przybliżenie było przyjęte **świadomie i warunkowo** — warunkiem był brak dat transakcji
(`db/bigquery.py:519-524`, `context/archive/2026-07-22-pul-79-portfolio-value-history/plan.md:79-82`).
Import XTB (PUL-95) ten warunek usunął: `user_broker_operations` przechowuje 1120 operacji
z `occurred_at`, sięgających 2025-01-28.

**Kluczowy pomiar (realny BQ, 2026-07-30):** rekonstrukcja `SUM(±volume)` z operacji zbiega
do `user_portfolio_positions.shares` **co do 4 miejsc po przecinku dla każdego tickera
akcyjnego we wszystkich 5 portfelach**. Rozjazd występuje w dokładnie trzech wierszach:

| portfel | ticker | z operacji | w pozycjach | reszta |
| -- | -- | -- | -- | -- |
| `10414536…` (IKZE) | `_CASH` | — | 2 160,11 | 2 160,11 |
| `d49d0121…` (Główny) | `_CASH` | — | 84,03 | 84,03 |
| `626e9da1…` (ręczny) | `XTB` | — | 1,0 | 1,0 |

Stan portfeli na prodzie:

```
portfel        pozycje  _CASH  operacje  1. operacja  min(created_at pozycji)
d49d0121…  13       1      458       2025-01-28   2026-07-30
6c6fdd5b…  12       0      458       2025-01-28   2026-07-29
10414536…   9       1      102       2025-07-09   2026-07-30
57ed5830…   8       0      102       2025-07-09   2026-07-29
626e9da1…   1       0        0       —            2026-07-22
```

Pary 458/458 i 102/102 to **dwaj różni użytkownicy**, nie duplikaty — darmowy test A/B.

## Desired End State

- Czerwiec 2024 dla portfela Głównego renderuje się **całkowicie biało** — zero wierszy z BQ.
- Styczeń 2025 zaczyna produkować wartości **2025-01-29** (pierwszy zakup: KRU), nie 2. stycznia.
- Kalendarz IKZE nic nie pokazuje przed **2025-07-10** (pierwszy zakup; wpłata z 2025-07-09
  nie tworzy dnia).
- Dzień wewnątrz zakresu liczy się z akcji posiadanych **tego dnia**, nie dzisiejszych.
- Prawa krawędź wykresu nadal równa się wartości z „Mój portfel" co do grosza.
- Portfel bez importu (`626e9da1…`) **nie regresuje do pustego widoku** — zachowuje dane
  od daty założenia portfela.
- Bliźniacze portfele dwóch użytkowników dają identyczne szeregi.

Weryfikacja: skrypt round-trip z zasianymi danymi (Faza 4) plus ręczna kontrola na
produkcji (Faza 5).

### Key Discoveries

- **Rekonstrukcja zbiega do snapshotu z dokładnością do 4 miejsc** — zmierzone, nie założone.
  Reszta jest wyłącznie tam, gdzie operacji z definicji nie ma.
- **`user_portfolio_positions.portfolio_id` jest NULLABLE** (`db/bigquery.py:720`) — osierocone
  pozycje sprzed PUL-64. Join po portfelu musi to znieść.
- **`positions.created_at` to data importu**, nie zakupu — na prodzie 2026-07-29/30 dla
  wszystkich zaimportowanych pozycji. Bezużyteczne jako inception; `user_portfolios.created_at`
  (`db/bigquery.py:1090`, REQUIRED) jest wiarygodne.
- **Wolumen jest zawsze dodatni, kierunek wyłącznie z `op_type`.** Sprzedaż ma w komentarzu
  `CLOSE BUY 5 @ 55.00` (`tests/test_brokers_xtb.py:324`) — znakowanie po komentarzu
  odwróciłoby każdą sprzedaż.
- **`occurred_at` zapisywane jest jako naiwny `isoformat()`** (`src/api.py:482`), więc BQ
  czyta je jako UTC. Grupowanie po dniu musi używać `DATE(occurred_at, 'Europe/Warsaw')`.
- **e2e podmienia całe funkcje na poziomie `src.api`** (`tests/e2e/conftest.py:686-693`) —
  zapytanie nigdy się nie wykonuje, więc e2e nie jest siatką dla SQL-a.
- **Kalendarz nie jest odporny na duplikaty** (`context/archive/2026-07-24-backfill-historical-closes/research.md:35`).

## What We're NOT Doing

- **Nie ruszamy bazy kosztowej.** `avg_buy_price` zostaje jedną liczbą bez czasu, więc
  `pnl_pln` na wykresie będzie miał poprawne wagi i wciąż nieczasową bazę. FIFO-as-of-date
  wymaga Pythona, nie okna SQL (SNT: 284,28 ważona vs 297,90 FIFO — ~5%, nie zaokrąglenie).
  Osobny ticket, z tym change'em jako prerekwizytem.
- **Nie ruszamy osi X wykresu.** Zostaje indeksowa; skrócony zakres ujawniamy notatką.
- **Nie zmieniamy modeli Pydantic kalendarza** ani renderera kalendarza w `static/index.html`.
- **Nie usuwamy BOCF.**
- **Nie dotykamy `zmiana_kwotowa`** — bierzemy ją wprost, nigdy jako różnicę zamknięć.
- **Nie tykamy śledztwa Δ 123,11 PLN vs XTB** — osobny wątek, wymaga danych od usera.
- **Nie tworzymy tabeli prekomputowanej.** Zostajemy przy compute-on-the-fly.
- **Nie dodajemy tombstone'ów** dla ręcznie skasowanych pozycji.

## Implementation Approach

Sednem jest odwrócenie kierunku rekonstrukcji. `user_broker_operations` **nie jest
kompletnym rejestrem stanu posiadania** — jest kompletnym rejestrem *ruchów, które broker
widział*. Snapshot `user_portfolio_positions` jest kompletny co do stanu **dziś**. Dlatego
operacje **cofają** snapshot, zamiast budować od zera:

```
shares(dzień) = dzisiejsze_akcje − Σ(±volume operacji późniejszych niż ten dzień)
```

Jedna formuła obsługuje wszystkie przypadki, bo to, czego operacje nie tłumaczą (reszta),
zostaje stałe w czasie zamiast zniknąć:

| przypadek | zachowanie | dlaczego poprawne |
| -- | -- | -- |
| ticker z pełną historią operacji | czysta rekonstrukcja, zero przed 1. kupnem | reszta = 0 |
| `_CASH`, pozycja ręczna, dywidenda rzeczowa (S2B) | linia płaska | brak operacji → reszta = dzisiejsze akcje |
| ticker sprzedany do zera | **historia go odzyskuje** | dzisiejsze = 0, suma operacji po dniu ujemna |
| eksport zaczyna się po zakupie (oversell) | reszta pochłania lukę | zamiast **ujemnych akcji** z gołego `SUM` |
| dzień dzisiejszy | dokładnie dzisiejsze akcje | brak operacji „po" → niezmiennik PUL-100 |

Ten kierunek jest też **monotonicznie samo-naprawiający**: każda operacja, którą kiedyś
nauczymy się parsować (split, transfer rzeczowy), po prostu zmniejsza resztę. Nic nie trzeba
przepisywać.

Naiwne „licz stan wyłącznie z operacji" złamałoby produkcję — portfel `626e9da1…` dostałby
pusty kalendarz i pusty wykres, a gotówka zniknęłaby z obu portfeli (sumowanie `amount_pln`
jej nie odtworzy: zmierzony rozjazd 84,03 vs 143,94, `src/brokers/xtb.py:256-260`).

## Critical Implementation Details

**Kolejność korekt jest load-bearing.** Trzy mechanizmy działają na tym samym zapytaniu i
muszą pozostać rozłączne:

1. `shares_on_day` **odcina** pozycję przed pierwszym kupnem (0 akcji).
2. LOCF/BOCF **wypełnia cenę** dla pozycji, która akcje ma.
3. Bramka `covered > 0` firuje, gdy **nic** nie da się wycenić.

BOCF po tej zmianie dotyczy wyłącznie tickerów resztowych — dla tickera *z* operacjami
`shares` przed debiutem = 0, więc BOCF wnosi `0 × cena = 0`. To musi zostać **udowodnione
testem**, nie założone (Faza 3).

**Trzy okna czasowe w kalendarzu to trzy różne rzeczy — nie mieszać:**
- 10-dniowy lookback cen istnieje **wyłącznie** po to, żeby LOCF miał poprzednika dla 1. dnia
  miesiąca (`db/bigquery.py:381-385`). To nie jest baseline P&L.
- Skan **operacji nie ma horyzontu** — obejmuje całą historię portfela aż do dziś,
  niezależnie od oglądanego miesiąca. Suma „operacji po dniu" wymaga wszystkiego, co po nim
  nastąpiło, więc żadna oś ograniczona miesiącem nie może jej wyznaczyć.
- Okno **wyniku** to nadal `WHERE snapshot_date >= @month_start`.

**Nowy kod musi zostać wewnątrz tych dwóch funkcji.** Nowa funkcja `db.bigquery`
zaimportowana do `src/api.py` przechodzi lokalnie (ADC uderza w realny `espi_ebi`) i pada
w CI, gdzie nie ma auth — a `client = _get_client()` stoi **poza** `try/except BigQueryError`
(`db/bigquery.py:387`, `:540`), więc leci surowe 500 zamiast czystego błędu.

**W trybie „Wszystkie" ten sam ticker występuje w dwóch portfelach** (np. KRU w Głównym i
IKZE). Partycjonowanie okna musi być po `(portfolio_id, ticker)`, nie po samym `ticker` —
inaczej operacje jednego portfela skorygują pozycję drugiego.

---

## Phase 1: Holdings as of each day w kalendarzu

### Overview

Zastąpienie `CROSS JOIN positions` w `get_portfolio_calendar_data` siatką, w której liczba
akcji zależy od dnia. Bez granicy inception (Faza 2) — tu chodzi wyłącznie o to, żeby dzień
liczył się z właściwych akcji.

### Changes Required

#### 1. Zapytanie kalendarza

**File**: `db/bigquery.py` (`get_portfolio_calendar_data`, :362-486)

**Intent**: Wprowadzić wymiar czasu do liczby akcji metodą korekty wstecznej nad snapshotem,
z uniwersum tickerów obejmującym również walory sprzedane do zera, i przy okazji zamknąć
znaną dziurę duplikatów cen.

**Contract**: Sygnatura i kształt zwracanych dictów **bez zmian** —
`(portfolio_id, user_id, year, month) -> list[dict]` z kluczami `snapshot_date`,
`portfolio_value`, `daily_change_pln`, `prices_found`, `total_positions`. Zmienia się
wyłącznie wnętrze zapytania.

Nowe/zmienione CTE, w kolejności:

- `holders` — uniwersum `(portfolio_id, ticker, today_shares)` jako **FULL OUTER JOIN**
  pozycji i zagregowanych operacji użytkownika, `today_shares = COALESCE(shares, 0)`.
  Uniwersum z samych pozycji zgubiłoby walor sprzedany do zera (wiersz kasowany przy
  imporcie, `src/api.py:1322`), a z samych operacji — gotówkę i pozycje ręczne.
  `portfolio_id` z pozycji jest NULLABLE, więc join musi to znieść.
- `ops_daily` — `SUM(CASE WHEN op_type='buy' THEN volume WHEN op_type='sell' THEN -volume
  ELSE 0 END)` grupowane po `(portfolio_id, ticker, DATE(occurred_at,'Europe/Warsaw'))`.
  Agregacja **przed** joinem — chroni przed rozmnożeniem wierszy dnia.
- `ops_totals` — `SUM(signed_volume)` po **całej** historii operacji, grupowane po
  `(portfolio_id, ticker)`. Bez horyzontu: to jest odniesienie, do którego zbiega
  rekonstrukcja.
- `holdings` — rdzeń zmiany. Liczba akcji na dzień to dzisiejszy stan pomniejszony o
  wszystko, co wydarzyło się **po** tym dniu, wyrażone jako różnica sum kumulacyjnych:

  ```sql
  h.today_shares - (t.total_signed - COALESCE(c.cum_signed_through_day, 0)) AS shares_on_day
  ```

  gdzie `cum_signed_through_day` = `SUM(signed_volume)` dla `op_date <= td.snapshot_date`,
  liczone **warunkiem zakresu** — nie równością dat i nie oknem nad osią sesyjną.

  Ta forma jest wymuszona przez dwie awarie, które okno
  `ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING` nad osią sesyjną by wprowadziło
  (F1 plan-review):

  1. **Horyzont.** Oś kalendarza to jeden miesiąc, więc `UNBOUNDED FOLLOWING` kończy się na
     jego ostatnim dniu — czerwiec 2025 obejrzany dziś nadal liczyłby akcje kupione w
     grudniu 2025. Skan operacji **nie ma horyzontu**; oś cenowa ma.
  2. **Operacje spoza osi.** Join po równości dat gubi operacje z dni nieobecnych w
     `company_daily_stats`. Zmierzone: **16 z 426 dni operacyjnych**. Dziś wszystkie 16 to
     operacje niehandlowe (`trade_days_off_spine = 0`), więc liczby akcji są bezpieczne
     przez przypadek, nie przez konstrukcję. W różnicy sum kumulacyjnych taka operacja
     wchodzi do obu składników i znosi się poprawnie.

  `op_date <= snapshot_date` (nie `<`) jest istotne: zakup wykonany danego dnia ma się
  liczyć **od tego dnia**.
- `daily_prices` — jak dziś, ale `p.shares` zastąpione przez `shares_on_day`, a wiersze
  z `ABS(shares_on_day) <= 1e-9` **odfiltrowane**. Bez progu goły `SUM` zostawia ~1e-13
  i renderuje pozycję-widmo, przekłamując `total_positions` i `prices_found`.
- Dedup cen — `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src)`
  na źródle cen, równając kalendarz do wykresu (`db/bigquery.py:579-583`).

Zaktualizować docstring: usunąć „against the user's current positions", opisać korektę
wsteczną i wprost nazwać resztę jako świadome zachowanie dla tickerów bez operacji.

#### 2. Testy jednostkowe zapytania

**File**: `tests/test_bigquery.py`

**Intent**: Przypiąć nowy kształt SQL-a tam, gdzie stary był przypięty, i dołożyć asercje
negatywne chroniące przed powrotem do dzisiejszych akcji.

**Contract**: `test_calendar_carries_the_last_close_forward_over_a_no_trade_session` (:1446)
zachowuje wszystkie dotychczasowe asercje LOCF; dochodzą: obecność
`DATE(occurred_at, 'Europe/Warsaw')`, `QUALIFY ROW_NUMBER`, oraz trzy **negatywne**
strażniki regresji: brak `CROSS JOIN positions`, brak `ROWS BETWEEN 1 FOLLOWING` (okno nad
osią sesyjną — F1) i brak `o.op_date = ` (join operacji po równości dat — F1). `test_get_portfolio_calendar_data_uses_correct_date_params` (:1425)
dostaje zaktualizowany zestaw parametrów.

#### 3. Test filtra portfela

**File**: `tests/test_api.py` (`_capture_calendar_query`, :1129; asercje :1170, :1174)

**Intent**: Filtr portfela przenosi się z CTE `positions` na `holders`/`ops_daily`, więc
literalne `"AND portfolio_id = @portfolio_id"` przestaje opisywać prawdę.

**Contract**: Asercje sprawdzają, że parametr `portfolio_id` jest **obecny w zapytaniu przy
portfelu konkretnym i nieobecny w trybie „Wszystkie"** — zamiast pinować dokładny fragment
tekstu w jednym CTE.

### Success Criteria

#### Automated Verification

- Testy jednostkowe BQ przechodzą: `uv run pytest tests/test_bigquery.py`
- Testy API przechodzą: `uv run pytest tests/test_api.py`
- Testy ETF przechodzą (`tests/test_etf_bigquery.py:95` pinuje `etf_quotes` + `COALESCE`)
- Pełna szybka pętla zielona: `uv run pytest --ignore=tests/e2e`
- Warstwy OK: `tach check`

#### Manual Verification

- Zapytanie **wykonuje się na realnym BQ bez błędu składni** — mocki nie parsują SQL-a
  (`context/foundation/lessons.md:211-235`); wystarczy jedno wywołanie z konsoli
- Kalendarz bieżącego miesiąca w UI nadal pokazuje te same wartości co przed zmianą
  (bieżący miesiąc nie ma operacji „po", więc liczby nie mogą się ruszyć)

---

## Phase 2: Granica inception w kalendarzu

### Overview

Po Fazie 1 tickery z operacjami mają zero akcji przed pierwszym kupnem, ale tickery resztowe
(gotówka, pozycje ręczne) ciągną się w nieskończoność wstecz. Ta faza ogranicza cały szereg
do momentu powstania portfela.

### Changes Required

#### 1. CTE granicy

**File**: `db/bigquery.py` (`get_portfolio_calendar_data`)

**Intent**: Wyznaczyć najwcześniejszą datę, którą portfel może raportować, i wyciąć dni
wcześniejsze **bez emitowania wiersza** — żeby `compute_calendar_pnl` renderował je jako
`no_data` (białe), zgodnie z istniejącym stanem (`src/portfolio_calendar.py:39`).

**Contract**: `inception` = `MIN(DATE(occurred_at,'Europe/Warsaw'))` po operacjach
**zmieniających stan posiadania** (`op_type IN ('buy','sell')`); gdy portfel takich operacji
nie ma — `MIN(DATE(created_at))` z `user_portfolios`. W trybie „Wszystkie" — minimum po
wszystkich portfelach użytkownika. Dodatkowy filtr w `daily_portfolio`:
`snapshot_date >= inception`.

**Dlaczego pierwszy zakup, a nie pierwsza operacja (F2 plan-review).** Na prodzie pierwszą
operacją każdego portfela jest wpłata, nie zakup: Główny `2025-01-28` przy pierwszym zakupie
`2025-01-29`, IKZE `2025-07-09` przy `2025-07-10`. Przy granicy z `MIN(occurred_at)` dzień
wpłaty przechodzi, a jedynym posiadaniem jest wtedy reszta gotówkowa — wyceniona po 1,00
z `daily_chg = 0`. Daje to `prices_found = 1 > 0`, czyli stan `data`
(`src/portfolio_calendar.py:104-109`) i komórkę **„0 PLN" renderowaną jako realny płaski
dzień** — dokładnie to kłamstwo, które ta zmiana ma usunąć. Łamie też kryterium 5.6:
bliźniaczy portfel bez zaimportowanej gotówki nie ma reszty, więc ten sam dzień
wyrenderowałby się w obu portfelach różnie.

**Skutek uboczny do udokumentowania:** portfel z samymi wpłatami, bez ani jednego zakupu,
dostaje pusty kalendarz. To poprawne — nie było czego wyceniać — ale ma być świadome.

**Portfel mieszany — przyjęte ograniczenie (F4 plan-review).** Fallback odpala się tylko przy
całkowitym braku operacji zakupu/sprzedaży, więc portfel z importem plus jedną pozycją dodaną
ręcznie dostanie granicę z operacji i będzie trzymał tę ręczną pozycję stałą aż do niej —
potencjalnie o półtora roku za wcześnie. Granica per-ticker rozwiązałaby to kosztem drugiego
wymiaru w zapytaniu; przy zerowej liczbie takich portfeli na prodzie nie jest tego warta.
Zostaje jako świadome ograniczenie, nie przeoczenie.

**Zero nie jest dopuszczalne jako alternatywa** — wyrenderuje się jako realny płaski dzień,
co jest innym kłamstwem (`context/archive/2026-07-22-pul-79-portfolio-value-history/plan.md:71-77`).

#### 2. Testy granicy

**File**: `tests/test_bigquery.py`

**Intent**: Przypiąć, że granica istnieje i że ma fallback dla portfela bez operacji.

**Contract**: Asercje na tekst SQL: obecność `user_portfolios` w zapytaniu (fallback), `MIN(`
po `occurred_at`, oraz **zawężenie granicy do `op_type IN ('buy','sell')`** — bez tego
warunku dzień wpłaty przechodzi i renderuje fałszywe „0 PLN" (F2). Plus test parametrów,
jeśli granica wprowadza nowy parametr.

#### 3. Test czystej funkcji

**File**: `tests/test_portfolio_calendar.py`

**Intent**: Udowodnić, że miesiąc bez wierszy renderuje pełną, białą siatkę — to kontrakt,
na którym stoi cała ta faza.

**Contract**: `compute_calendar_pnl([], year, month)` zwraca `days` długości równej liczbie
dni miesiąca, wszystkie w stanie `weekend`/`holiday`/`no_data`/`future`, `mtd_diff` wszędzie
`None`. Częściowo pokryte przez `:94` i `:219` — rozszerzyć o jawny przypadek „miesiąc
sprzed inception" z komentarzem wiążącym go z PUL-103.

### Success Criteria

#### Automated Verification

- Testy jednostkowe przechodzą: `uv run pytest tests/test_bigquery.py tests/test_portfolio_calendar.py`
- Pełna szybka pętla zielona: `uv run pytest --ignore=tests/e2e`
- Warstwy OK: `tach check`

#### Manual Verification

- **Czerwiec 2024 dla portfela Głównego zwraca zero wierszy** — zweryfikowane wywołaniem
  funkcji przeciw realnemu BQ
- **Styczeń 2025 zwraca pierwszy wiersz 2025-01-29**, nie 2025-01-02
- **IKZE nic nie zwraca przed 2025-07-10** (wpłata z 2025-07-09 nie tworzy dnia)
- Portfel `626e9da1…` (bez operacji) **nadal zwraca wiersze** od daty założenia

---

## Phase 3: Ten sam wymiar czasu w wykresie wartości

### Overview

Przeniesienie tych samych CTE na `get_portfolio_history`, z dwoma dodatkami specyficznymi
dla wykresu: notatką o skróconym zakresie i dowodem rozłączności z BOCF.

### Changes Required

#### 1. Zapytanie wykresu

**File**: `db/bigquery.py` (`get_portfolio_history`, :489-671)

**Intent**: Te same `holders` / `ops_daily` / `holdings`, wpięte w istniejącą siatkę `grid`,
z zachowaniem LOCF+BOCF, bramki `covered > 0` i join'a meta-first.

**Contract**: Sygnatura i koperta `{series, notes, excluded}` **bez zmian**. `grid` używa
`shares_on_day` zamiast `p.shares`; `avg_buy_price` zostaje bez zmian (poza zakresem).

**Próg pyłu obowiązuje tu tak samo jak w Fazie 1 (F3 plan-review).** `grid` odfiltrowuje
`ABS(shares_on_day) <= 1e-9`, a `covered` liczy wyłącznie pozycje z niezerowymi akcjami
danego dnia. Bez tego ticker o zerowym stanie zostaje w `filled` z niezerowym `px_ff` i
wchodzi do `COUNTIF(px_ff IS NOT NULL)` — bramka `covered > 0` przestaje wtedy znaczyć
„nic nie da się wycenić", a zaczyna znaczyć „nic nie ma w uniwersum". To dwa różne fakty
i mylenie ich utrudni diagnozę następnej awarii. Skutek poboczny tego samego przeoczenia:
`notes` zgłosiłoby debiut tickera, którego w tym oknie nie posiadano.

Dzień sprzed inception ma wypadać **przez granicę, nie przez bramkę** — granica jest
świadectwem „portfel nie istniał", bramka świadectwem „dane zawiodły".

Granica inception jak w Fazie 2, ale odniesiona do `@start_date`: szereg zaczyna się od
`GREATEST(@start_date, inception)`.

Docstring: przepisać akapit „Accepted approximations" (`:519-524`) — przybliżenie „dzisiejsze
akcje na każdym dniu" **przestaje obowiązywać**, zostaje wyłącznie baza kosztowa i
zawężony BOCF.

#### 2. Notatka o skróconym zakresie

**File**: `db/bigquery.py` + `src/api.py` (model `PortfolioHistoryResponse`, :298-305)

**Intent**: Gdy szereg zaczyna się później niż żądany zakres, powiedzieć to wprost — bo oś X
jest indeksowa (`static/index.html:5183`) i dwumiesięczna historia w zakresie `1y` wygląda
jak pełny rok.

**Contract**: Nowy wpis w istniejącej tablicy `notes`, o kształcie odróżnialnym od
istniejących not o debiucie (te mają `ticker`/`listed_from`/`price`). Front renderuje noty
w `_ppHistNoteLines` (`static/index.html:5155-5166`) — dodać gałąź dla nowego kształtu.
Treść mówi o **danych**, nie o listingu: *„dane od DD.MM.RRRR — portfel nie istniał
wcześniej"*, zgodnie z lekcją PUL-100 F2 (nigdy „notowany od").

**Uwaga:** wszystkie modele mają `model_config = ConfigDict(extra="ignore")` — pole
niezadeklarowane w modelu zostanie **po cichu wycięte**.

#### 3. Test rozłączności z BOCF

**File**: `tests/test_bigquery.py`

**Intent**: Udowodnić, że dwie korekty nie nakładają się — to jedyne miejsce, gdzie ta
zmiana mogłaby po cichu podwoić wkład.

**Contract**: Test SQL-owy dowodzący, że mnożenie przez cenę używa `shares_on_day`, a nie
`p.shares` (asercja negatywna na `p.shares *`). Plus test jednostkowy na wierszach: ticker
z operacjami kupiony w dniu debiutu ma przed debiutem `shares = 0`, więc BOCF wnosi zero;
ticker bez operacji zachowuje stałe akcje i **korzysta** z BOCF. Trzecia asercja (F3): ticker
o zerowym stanie danego dnia **nie wlicza się do `covered`**.

#### 4. Fake'i e2e

**File**: `tests/e2e/conftest.py`

**Intent**: Fake'i podmieniają całe funkcje (`:686-693`), więc SQL ich nie dotyczy — ale
**każda zmiana sygnatury Pythona rzuca `TypeError` w każdym teście e2e**, a nowy klucz w
kopercie musi się w nich pojawić, inaczej front nie ma czego renderować.

**Contract**: Sygnatury pozostają niezmienione, więc zmiana ogranicza się do dopisania
nowego kształtu noty do `_FAKE_HISTORY_NOTES` (`:381`) — o ile Faza 3 punkt 2 taki wprowadza.

### Success Criteria

#### Automated Verification

- Testy jednostkowe przechodzą: `uv run pytest tests/test_bigquery.py tests/test_api.py`
- Testy e2e przechodzą: `uv run pytest tests/e2e`
- Pełna suita zielona: `uv run pytest`
- Warstwy OK: `tach check`

#### Manual Verification

- Wykres w zakresie `1y` dla portfela Głównego **zaczyna się 2025-01-29**, nie rok wstecz
- **Prawa krawędź wykresu równa się wartości z „Mój portfel" co do grosza** — niezmiennik
  PUL-100, sprawdzony wzrokowo w UI
- Przycisk `(i)` pokazuje notatkę o skróconym zakresie, gdy zakres jest obcięty
- Zakładka „Wszystkie" pokazuje szereg od najwcześniejszej operacji ze wszystkich portfeli

---

## Phase 4: Round-trip na realnym BQ z zasianymi danymi

### Overview

Mocki nie parsują SQL-a, a e2e podmienia całe funkcje — więc **żaden istniejący test nie
złapie błędu arytmetycznego w tych zapytaniach**. Ta faza buduje jedyną realną siatkę.

### Changes Required

#### 1. Skrypt round-trip

**File**: `scripts/test_bq_portfolio_time_dimension.py` (nowy)

**Intent**: Zasiać deterministyczne dane w tabelach-jednorazówkach i sprawdzić **konkretne
liczby**, których regresja do dzisiejszych akcji by nie przeżyła.

**Contract**: Wzorzec podmiany stałej modułu z `scripts/test_bq_broker_operations.py:69-79`,
rozszerzony na **cztery** tabele: `company_daily_stats`, `etf_quotes`,
`user_portfolio_positions`, `user_broker_operations`. Bez podmiany wszystkich czterech
realne wiersze wciekną w arytmetykę i asercje przestaną być deterministyczne.

Konwencja szkieletu (obowiązkowa): docstring z listą sprawdzeń + `Run with:` + `Requires
ADC:`, następnie `sys.path.insert`, `load_dotenv()`, **dopiero potem** import `db.bigquery`
(`_DATASET` czytany przy imporcie). `table.expires` ustawiane **przed** `create_table`, a
przywrócenie stałych i `delete_table` w `finally`.

Asercje, każda dobrana tak, żeby padła przy powrocie do dzisiejszych akcji:

- **Dzień przed kupnem: wartość 0 i `total_positions = 0`.** Zasiane kupno w środku okna.
- **Miesiąc z przeszłości nie widzi zakupu dokonanego później** — zasiać zakup po końcu
  odpytywanego miesiąca i sprawdzić, że nie wpływa na żaden jego dzień. To jedyna asercja
  łapiąca F1; regresja do okna nad osią sesyjną przechodzi wszystkie pozostałe.
- **Operacja z dnia spoza osi sesyjnej nie ginie** — zasiać sprzedaż w dniu nieobecnym
  w `company_daily_stats` i sprawdzić, że stan po niej spada.
- **Dzień kupna: `shares × close` co do grosza.**
- **Dzień po częściowej sprzedaży: `(shares − sprzedane) × close`.**
- **Ticker sprzedany do zera pojawia się w historii** i znika po dacie sprzedaży.
- **Ticker bez operacji (gotówka) trzyma stałą wartość** przez całe okno.
- **Oversell nie produkuje ujemnych akcji** — zasiana sprzedaż bez poprzedzającego zakupu.
- **Dzień sprzed inception nie zwraca wiersza.**
- **Dzień wpłaty poprzedzającej pierwszy zakup nie zwraca wiersza** — inaczej reszta
  gotówkowa wyrenderuje się jako realny płaski dzień „0 PLN" (F2).
- **Ostatni dzień równa się dzisiejszym akcjom** — niezmiennik PUL-100.
- **Raport reszt**: dla każdego `(portfel, ticker)` wypisać `dzisiejsze − rekonstrukcja`,
  z jawnym rozróżnieniem oczekiwanych (gotówka, brak operacji) od nieoczekiwanych. To jest
  realizacja wymogu ticketu „rozjazd musi zostać ujawniony".
- **Budżet czasu**: górny limit wall-clock na oba zapytania, wzorem `MERGE_BUDGET_SECONDS`
  (`scripts/test_bq_broker_operations.py:43`). Wykres mierzy dziś ~1,6 s
  (`db/bigquery.py:536`), a dokładamy CTE.

#### 2. Log liczby reszt

**File**: `db/bigquery.py`

**Intent**: Żeby nieoczekiwana reszta nie została zauważona dopiero przez usera.

**Contract**: Zapytanie zlicza `(portfolio_id, ticker)` z niezerową resztą **poza** gotówką
i loguje liczbę na poziomie DEBUG, obok istniejących pomiarów czasu (`:476`, `:649`). Nie
zmienia zwracanego kształtu.

### Success Criteria

#### Automated Verification

- Skrypt przechodzi w całości: `uv run python scripts/test_bq_portfolio_time_dimension.py`
- Skrypt sprząta po sobie — tabele-jednorazówki nie istnieją po przebiegu
- Pełna suita nadal zielona: `uv run pytest`

#### Manual Verification

- Raport reszt na realnych danych pokazuje **dokładnie trzy** znane przypadki
  (2× `_CASH`, 1× pozycja ręczna) i zero nieoczekiwanych
- Oba zapytania mieszczą się w budżecie czasu

---

## Phase 5: Weryfikacja na produkcji

### Overview

Zmiana jest widoczna wyłącznie jako liczby na ekranie, więc ostatnim krokiem jest sprawdzenie
ich przeciw realnym danym — po deployu, przed zamknięciem ticketu.

### Changes Required

#### 1. Aktualizacja notatek change'a

**File**: `context/changes/calendar-holdings-as-of-date/change.md`

**Intent**: Zapisać wyniki weryfikacji produkcyjnej jako trwały ślad.

**Contract**: Sekcja `## Notes` dostaje wynik każdego punktu z listy poniżej, z datami
i liczbami.

### Success Criteria

#### Automated Verification

- CI zielone na PR: `uv run pytest --tb=short`
- Deploy przeszedł: `/health` na produkcji odpowiada

#### Manual Verification

- **Czerwiec 2024, portfel Główny — kalendarz całkowicie biały**
- **Styczeń 2025 — pierwsza wartość 2025-01-29 (KRU), nie 2. stycznia**
- **IKZE — nic przed 2025-07-10** (wpłata z 2025-07-09 nie tworzy dnia)
- **Bliźniacze portfele dwóch użytkowników dają identyczne szeregi** (`d49d0121…` vs
  `6c6fdd5b…` po uwzględnieniu różnicy w gotówce; `10414536…` vs `57ed5830…`)
- **Prawa krawędź wykresu = wartość z „Mój portfel" co do grosza**
- **Portfel `626e9da1…` (bez importu) nie jest pusty**
- Próbkowany dzień wewnątrz zakresu uzgadnia się z wyciągiem XTB przy użyciu akcji
  posiadanych **tego** dnia

---

## Testing Strategy

### Unit Tests

- **Kształt SQL-a** (`tests/test_bigquery.py`) — obecność okna korekty wstecznej,
  partycjonowania po `(portfolio_id, ticker)`, strefy `Europe/Warsaw`, dedupu `QUALIFY`;
  asercje **negatywne** na `CROSS JOIN positions` i `p.shares *` jako strażnicy regresji.
- **Parametry** — zestaw i typy, w tym nowe okno skanu operacji.
- **Czysta funkcja** (`tests/test_portfolio_calendar.py`) — miesiąc bez wierszy renderuje
  pełną białą siatkę; to kontrakt, na którym stoi decyzja „brak wiersza zamiast nowego stanu".
- **Rozłączność korekt** — ticker z operacjami nie korzysta z BOCF przed pierwszym kupnem;
  ticker bez operacji korzysta.

### Integration Tests

- **e2e** (`tests/e2e/`) — kontrakt UI, nie danych. Chronią przed `TypeError` z niezgodnych
  sygnatur i przed zniknięciem koperty; nie zobaczą błędu arytmetycznego.

### Manual Testing Steps

1. Round-trip z zasianymi danymi (Faza 4) — jedyny test łapiący złą arytmetykę.
2. Wywołanie obu funkcji przeciw realnemu BQ dla czerwca 2024, stycznia 2025 i IKZE.
3. Porównanie prawej krawędzi wykresu z „Mój portfel" w UI.
4. Porównanie szeregów bliźniaczych portfeli dwóch użytkowników.

## Performance Considerations

Klastrowanie `user_broker_operations` to `["user_id","ticker"]` (`db/bigquery.py:3373`) —
zgodne z `PARTITION BY user_id, ticker`, ale **nie przycina zakresu dat**. Przy 1120
wierszach w całej tabeli to bez znaczenia; klastrowania i tak nie da się zmienić, bo
`ensure_schema_current` umie tylko doklejać kolumny.

Wykres mierzy dziś ~1,6 s po stronie użytkownika (`db/bigquery.py:536`) i jest cache'owany
300 s. Dokładamy dwa CTE nad małą tabelą — koszt powinien być pomijalny, ale budżet czasu
w skrypcie round-trip jest asercją, nie życzeniem.

## Migration Notes

**Brak migracji schematu.** Zmiana jest wyłącznie w treści dwóch zapytań plus jedno
opcjonalne pole w kopercie wykresu. Nie ma zapisu do żadnej tabeli, więc rollback to
`git revert` — bez kroków po stronie danych.

Cache unieważnia się sam: `_perf_invalidate_portfolio` (`src/api.py:120-134`) już skanuje po
prefiksie i pokrywa oba klucze plus sentinel `all`, więc po imporcie wynik odświeży się
poprawnie mimo nowej zależności od `user_broker_operations`. Istniejące wpisy cache wygasną
w 300 s po deployu.

## References

- Research: `context/changes/calendar-holdings-as-of-date/research.md`
- Ticket: PUL-103 / GH #211; blokuje PUL-104 / GH #212
- Wzorzec tabeli-jednorazówki: `scripts/test_bq_broker_operations.py:69-79`, `:209-220`
- Bramka pokrycia i koperta: `context/archive/2026-07-26-history-coverage-gate/plan.md`
- Dlaczego `zmiana_kwotowa` wprost: `context/archive/2026-07-27-official-close-source/plan.md:641-648`
- Zakaz zero-fill: `context/archive/2026-07-22-pul-79-portfolio-value-history/plan.md:71-77`
- Lekcja o mockowanych testach BQ: `context/foundation/lessons.md:211-235`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Holdings as of each day w kalendarzu

#### Automated

- [x] 1.1 Testy jednostkowe BQ przechodzą — 26ec34b
- [x] 1.2 Testy API przechodzą — 26ec34b
- [x] 1.3 Testy ETF przechodzą — 26ec34b
- [x] 1.4 Pełna szybka pętla zielona (--ignore=tests/e2e) — 26ec34b
- [x] 1.5 tach check — 26ec34b

#### Manual

- [x] 1.6 Zapytanie wykonuje się na realnym BQ bez błędu składni — 26ec34b
- [x] 1.7 Kalendarz bieżącego miesiąca pokazuje te same wartości co przed zmianą — prawa — 26ec34b
      krawędź bez zmian (44 476,98 na 2026-07-30, zgodne co do grosza z pomiarem z researchu);
      wcześniejsze dni lipca **celowo** się zmieniły (11 pozycji zamiast 13 przed zakupem ASB
      2026-07-17 i VOT 07-21) — kryterium było w planie sformułowane zbyt szeroko

### Phase 2: Granica inception w kalendarzu

#### Automated

- [x] 2.1 Testy jednostkowe przechodzą — 26ec34b
- [x] 2.2 Pełna szybka pętla zielona — 26ec34b
- [x] 2.3 tach check — 26ec34b

#### Manual

- [x] 2.4 Czerwiec 2024 dla Głównego zwraca zero wierszy — 26ec34b
- [x] 2.5 Styczeń 2025 zwraca pierwszy wiersz 2025-01-29 — oba portfele Główne — 26ec34b
- [x] 2.6 IKZE nic nie zwraca przed 2025-07-10 (wpłata z 07-09 nie tworzy dnia) — oba IKZE — 26ec34b
- [x] 2.7 Portfel bez operacji nadal zwraca wiersze od daty założenia — `626e9da1…`, — 26ec34b
      8 wierszy od 2026-07-22; cztery portfele bez ani jednej pozycji słusznie zwracają zero

### Phase 3: Ten sam wymiar czasu w wykresie wartości

#### Automated

- [x] 3.1 Testy jednostkowe BQ i API przechodzą — f4b5f00
- [x] 3.2 Testy e2e przechodzą — f4b5f00
- [x] 3.3 Pełna suita zielona — f4b5f00
- [x] 3.4 tach check — f4b5f00

#### Manual

- [x] 3.5 Wykres 1y dla Głównego zaczyna się 2025-01-29 — f4b5f00
      **Kryterium okazało się nieaktualne**: zakres 1r liczony od 2026-07-31 startuje
      2025-07-31, czyli *po* inception, więc granica przy tym zakresie nic nie ucina.
      Zweryfikowane przy zakresie sięgającym dalej (start 2024-06-01): oba portfele
      Główne dają pierwszy punkt **2025-01-29** i 377 punktów, oba IKZE **2025-07-10**
      i 265 punktów.
- [x] 3.6 Prawa krawędź wykresu = wartość z „Mój portfel" co do grosza — f4b5f00
      Pięć portfeli z danymi, `delta = +0,00` w każdym (44 379,43 / 26 972,77 /
      44 295,40 / 24 812,66 / 148,26); niezmiennik PUL-100 utrzymany.
- [x] 3.7 Przycisk (i) pokazuje notatkę o skróconym zakresie — f4b5f00
      Na danych: portfel `626e9da1…` przy 1r zwraca `data_from=2026-07-23`.
      W realnej przeglądarce: e2e `test_backfill_note_is_reachable_without_a_mouse`
      czyta linię „dane od … — portfel nie istniał wcześniej" po otwarciu (i) klawiaturą.
- [x] 3.8 Zakładka „Wszystkie" agreguje od najwcześniejszej operacji — f4b5f00
      Tryb `all` startuje 2025-01-29 (minimum po wszystkich portfelach), prawa krawędź
      równa sumie portfeli co do grosza (44 379,43 + 26 972,77 = 71 352,20).

### Phase 4: Round-trip na realnym BQ z zasianymi danymi

#### Automated

- [x] 4.1 Skrypt round-trip przechodzi w całości — 15 sprawdzeń, 6 portfeli — f088f63
- [x] 4.2 Skrypt sprząta tabele-jednorazówki — `bq ls espi_ebi | grep _rt_` puste — f088f63
- [x] 4.3 Pełna suita nadal zielona — f088f63

#### Manual

- [x] 4.4 Raport reszt pokazuje dokładnie trzy znane przypadki i zero nieoczekiwanych — f088f63
      Na produkcji: `10414536… _CASH 2160,11`, `d49d0121… _CASH 84,03`,
      `626e9da1… XTB 1,0` — co do wiersza to, co zmierzył research; nic poza tym.
      Log DEBUG w obu zapytaniach liczy to samo z pominięciem gotówki (resztowej
      z definicji), więc na prodzie zgłasza 1 dla portfela ręcznego i 0 dla reszty.
- [x] 4.5 Oba zapytania mieszczą się w budżecie czasu — f088f63
      Na realnych danych: kalendarz 1,16 s, wykres 1,50 s (1r) / 1,90 s (od 2024-06-01)
      / 1,60 s („Wszystkie"). Baseline wykresu sprzed zmiany to ~1,6 s, więc dwa nowe
      CTE nic nie kosztowały. Budżet w skrypcie to 20 s na zapytanie.

### Phase 5: Weryfikacja na produkcji

#### Automated

- [ ] 5.1 CI zielone na PR
- [ ] 5.2 Deploy przeszedł, /health odpowiada

#### Manual

- [ ] 5.3 Czerwiec 2024, Główny — kalendarz całkowicie biały
- [ ] 5.4 Styczeń 2025 — pierwsza wartość 2025-01-29
- [ ] 5.5 IKZE — nic przed 2025-07-10
- [ ] 5.6 Bliźniacze portfele dwóch użytkowników dają identyczne szeregi
- [ ] 5.7 Prawa krawędź wykresu = „Mój portfel" co do grosza
- [ ] 5.8 Portfel bez importu nie jest pusty
- [ ] 5.9 Próbkowany dzień uzgadnia się z wyciągiem XTB
