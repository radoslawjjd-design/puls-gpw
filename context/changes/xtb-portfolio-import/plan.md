# Import pozycji i dywidend z eksportu XTB — plan implementacji

## Overview

Budujemy import eksportu XTB do Mojego portfela: upload pliku `.xlsx`, rekonstrukcja
otwartych pozycji metodą FIFO z historii transakcji, podgląd z pełnym ujawnieniem
konsekwencji, potwierdzenie, oraz nowy widok dywidend gotówkowych z wyborem roku.

Fundamentem jest decyzja, żeby przechowywać **surowe operacje jako źródło prawdy**.
Jedna nowa tabela trzyma każdy wiersz arkusza `Cash Operations`; pozycje i dywidendy
są nad nią projekcjami. Dzięki temu deduplikacja po identyfikatorze operacji brokera
działa jednolicie dla wszystkiego, a daty transakcji — których projekt do tej pory
nigdy nie miał — zostają zapisane i odblokowują późniejsze usprawnienia bez kolejnej
migracji schematu.

## Current State Analysis

Pełna mapa kodu jest w `context/changes/xtb-portfolio-import/research.md`. Stan istotny
dla tego planu:

- **Upload pliku nie istnieje nigdzie.** Ani `<input type="file">` we froncie, ani
  `UploadFile` w backendzie. `python-multipart` nie jest zainstalowany, a `fastapi`
  przypięty bez ekstras (`pyproject.toml:7-24`).
- **Brak czytnika xlsx** — nie ma `openpyxl`, `pandas`, `xlrd` ani `calamine`.
- **Brak magazynu dywidend** — `db/bigquery.py` nie zawiera ani jednego identyfikatora
  związanego z dywidendami.
- **Brak FIFO i dopasowywania lotów w całym projekcie.** Koszt nabycia to jeden skalar
  `avg_buy_price` w jednym wierszu na `(portfolio_id, ticker)` (`db/bigquery.py:700-711`).
- **Istnieje gotowy prymityw idempotentnego importu**: `_merge_insert_only` z klauzulą
  `QUALIFY` (`db/bigquery.py:2801-2862`), ale ma **zaszyty klucz** `(ticker, snapshot_date)`.
- **Istnieje niemal dokładny szablon okna importu**: `#pp-add-portfolio-overlay`
  (`static/index.html:3799-3819`) — dropdown, pole warunkowe, błąd inline, akcje.
- **Dwie pre-istniejące dziury w cache**: `_perf_invalidate_portfolio`
  (`src/api.py:98-103`) nie czyści klucza `history:` ani sentynela `all`.
- **Cloud Run: `--timeout=60`, 512 MiB, max 2 instancje** (`.github/workflows/deploy.yml:81-97`).

Stan danych wejściowych (z `change.md`, wyliczony na realnych eksportach):

- 12 otwartych pozycji w Głównym, 8 w IKZE; 24 tickery już zamknięte.
- Kolumna `ID` w `Cash Operations` jest wypełniona w 571 na 571 operacji, unikalna
  w obrębie pliku i bez kolizji między plikami.
- FIFO odtwarza dzisiejszy stan produkcji co do grosza na 19 z 20 pozycji.

## Desired End State

Użytkownik otwiera Mój portfel, klika **Importuj** obok Eksport CSV, wybiera z listy
dom maklerski (na razie tylko XTB), wskazuje plik eksportu i widzi podgląd: co zostanie
zapisane, co zostanie usunięte jako zamknięte według pliku, czego aplikacja nie rozpoznała,
co zostanie nietknięte bo nie ma tego w pliku, oraz ile nowych dywidend przybędzie.
Po potwierdzeniu pozycje i dywidendy trafiają do bazy, a widoki natychmiast pokazują
aktualny stan — łącznie z domyślną zakładką „Wszystkie".

Pod Tabelą/Treemapą/Kalendarzem pojawia się czwarta zakładka **Dywidendy** z wyborem roku
(2025, 2026, wszystkie — lista rozszerza się sama), kafelkami brutto/podatek/netto
oraz tabelą wypłat w rozbiciu na spółki.

Weryfikacja końcowa: import obu realnych plików daje 12 pozycji w Głównym i 8 w IKZE,
zgodnych z wyliczoną wyrocznią, przy czym CBF zostaje skorygowany ze 199,40 na 188,40,
a S2B pozostaje nietknięty.

### Key Discoveries

- `db/bigquery.py:2801-2862` — `_merge_insert_only` z `QUALIFY`; klucz jest zaszyty,
  więc wymaga sparametryzowania. `QUALIFY` jest niezbędne, bo `WHEN NOT MATCHED` odpala
  się per wiersz źródłowy i duplikat w samej partii wszedłby dwa razy.
- `db/bigquery.py:3174-3199` — `notification_sent_log`, najczystszy wzorzec nowej tabeli.
- `db/bigquery.py:149-181` — `ensure_schema_current` jest **wyłącznie addytywne**;
  partycjonowania i klastrowania nie da się zmigrować po utworzeniu tabeli.
- `src/api.py:267-274` — `PortfolioHistoryResponse`, precedens koperty z zastrzeżeniami.
- `src/api.py:355-392` — `_merge_positions_by_ticker` liczy średnią **ważoną kosztem**;
  FIFO musi zapisywać `pozostały_koszt / pozostałe_akcje`, żeby tryb „Wszystkie" pozostał poprawny.
- `static/index.html:3846-3849` — lista ukrywania paneli `data-mode`: cztery miejsca.
- `tests/e2e/conftest.py:542-675` — każda niezapatchowana funkcja DDL powoduje, że cała
  sesja e2e startuje przeciwko żywemu BigQuery.
- `context/foundation/lessons.md:211-235` — mockowane testy BQ nie parsują SQL;
  round-trip na realnym BQ jest obowiązkowy.

## What We're NOT Doing

- **Nie obsługujemy instrumentów zagranicznych.** Wiersze z tickerem niekończącym się
  na `.PL` są pomijane w parserze i zliczane w ostrzeżeniach. Uzasadnienie liczbowe:
  to 11 operacji łącznie, wszystkie na pozycjach już zamkniętych, i **zero dywidend** —
  pominięcie nie rusza żadnej prezentowanej sumy.
- **Nie dodajemy dywidend rzeczowych.** Spin-offy (S2B, a wkrótce Shoper i Cyber_Folks)
  strukturalnie nie występują w eksporcie. Widok nazywa się „dywidendy gotówkowe".
- **Nie zmieniamy wykresu wartości historycznej.** Daty transakcji zostają zapisane,
  ale wykorzystanie ich do usunięcia przybliżenia z PUL-79 to osobna zmiana.
- **Nie budujemy zrealizowanego P/L.** Arkusz `Closed Positions` służy wyłącznie jako
  kontrola krzyżowa parsera, nie jako źródło nowej funkcji.
- **Nie dodajemy drugiego brokera.** Rejestr jest przygotowany na rozszerzenie, ale
  implementujemy wyłącznie XTB.
- **Nie konsolidujemy pięciu istniejących wzorców modalnych.** Odnotowane jako dług.
- **Nie commitujemy realnych plików eksportu.** `export_xtb/` zawiera numery rachunków
  i jest w `.gitignore`; fixture'y są syntetyzowane.

## Implementation Approach

Warstwa po warstwie, od środka na zewnątrz, tak żeby każda faza kończyła się na zielono
i dała się zdeployować osobno.

Parser jest **czystą funkcją bez BQ i bez HTTP**, zgodnie z precedensem
`src/portfolio_treemap.py` — dzięki temu złoty zbiór da się sprawdzić testem jednostkowym,
zanim powstanie jakikolwiek endpoint czy UI.

Podgląd i commit są **bezstanowe**: commit ponownie przyjmuje ten sam plik i parsuje go
od nowa, zamiast trzymać wynik podglądu po stronie serwera. Powód jest konkretny —
cache jest per proces, a Cloud Run chodzi z dwiema instancjami, więc token podglądu
zapisany przez instancję A bywałby nieznany instancji B. Parser jest deterministyczny,
więc powtórne parsowanie daje ten sam wynik.

## Critical Implementation Details

**Kolejność przy dodaniu `python-multipart`.** FastAPI podnosi `RuntimeError` przy
**definicji trasy**, nie przy obsłudze żądania. Endpoint z `UploadFile` bez tej zależności
wywala `create_app()` i kładzie cały serwis. Zależność i endpoint muszą wejść tym samym
commitem, a zależność musi trafić do sekcji runtime — Docker robi `uv sync --frozen --no-dev`,
więc pomyłkowe umieszczenie w grupie dev przechodzi CI i wychodzi dopiero na produkcji.

**Partycjonowanie i klastrowanie są nieodwracalne.** `ensure_schema_current` potrafi tylko
doklejać kolumny. Decyzja o klastrowaniu nowej tabeli zapada teraz i na zawsze.

**Mikrosekundy w znacznikach czasu są znaczące.** Trzy wypłaty PAS w IKZE mają identyczny
znacznik czasu z dokładnością do sekundy i różnią się dopiero mikrosekundami. Żadna ścieżka
nie może obcinać precyzji timestampu — ani przy deduplikacji, ani przy grupowaniu.

**Budżet 60 s na żądanie.** Commit musi zmieścić się w trzech zapytaniach do BigQuery
(MERGE operacji, MERGE pozycji, DELETE zamkniętych), a nie w pętli po pozycjach. Dwadzieścia
sekwencyjnych wywołań `upsert_user_portfolio_position` to realnie 20–60 s.

---

## Phase 1: Czysty parser XTB i silnik FIFO

### Overview

Moduł parsujący bez żadnych zależności od BigQuery i FastAPI, z rejestrem brokerów
przygotowanym na rozszerzenie. Złoty zbiór jako asercje liczbowe.

### Changes Required

#### 1. Zależność

**File**: `pyproject.toml`

**Intent**: Dodać `openpyxl` jako zależność runtime — najmniejszy czytnik xlsx pasujący
do profilu projektu (czysty Python, jedna zależność tranzytywna).

**Contract**: Wpis w sekcji `[project].dependencies` (nie w grupie `[dependency-groups].dev`),
przez `uv add openpyxl`.

**Implementation Note**: Dockerfile robi `uv sync --frozen --no-dev`, więc regenerowany
`uv.lock` musi wejść **tym samym commitem** co `pyproject.toml` — `--frozen` wywala build
przy rozjeździe. `uv add` aktualizuje lock automatycznie; chodzi wyłącznie o to, żeby nie
wypadł z commita. `--no-dev` oznacza dodatkowo, że pomyłkowe umieszczenie zależności
w grupie dev przechodzi lokalnie i CI, a pada dopiero na produkcji.

#### 2. Rejestr brokerów

**File**: `src/brokers/__init__.py` (nowy)

**Intent**: Punkt rozszerzenia, o który prosi ticket — dropdown domów maklerskich musi
mieć źródło, a kolejny broker ma być wpięciem jednej pozycji.

**Contract**: Eksportuje `list_brokers() -> list[dict]` zwracające `[{"id": "xtb", "label": "XTB"}]`
oraz `get_parser(broker_id) -> Callable[[bytes], BrokerImport]`, podnoszące `UnknownBrokerError`
dla nieznanego identyfikatora. To pierwszy podpakiet w płaskim dziś `src/` — uzasadniony
tym, że rozszerzalność jest wprost wymaganiem ticketu.

#### 3. Adapter xlsx

**File**: `src/brokers/xlsx_reader.py` (nowy)

**Intent**: Odizolować jedyne miejsce dotykające bajtów, żeby cała reszta parsera dała się
testować na literałach — dokładnie tak, jak `scripts/backfill_historical_closes.py` oddziela
czystą logikę od I/O.

**Contract**: `read_sheets(data: bytes) -> dict[str, list[dict]]` — mapa nazwa arkusza →
lista wierszy jako słowniki kluczowane **nagłówkiem kolumny**, nigdy pozycją. Nagłówek jest
odnajdywany przez wyszukanie wiersza zawierającego oczekiwany zestaw etykiet; brak
oczekiwanej kolumny podnosi błąd z jej nazwą, nigdy nie zgaduje. Ładowanie przez
`load_workbook(read_only=True, data_only=True)` ze względu na limit 512 MiB.

Mapowanie po nagłówku to bezpośrednia lekcja z PUL-98, gdzie indeksowanie pozycyjne
pozwoliło defektowi żyć miesiąc.

#### 4. Parser XTB

**File**: `src/brokers/xtb.py` (nowy)

**Intent**: Zamienić arkusze na znormalizowane operacje, odtworzyć otwarte pozycje metodą
FIFO i wyodrębnić dywidendy gotówkowe.

**Contract**: Funkcja wejściowa `parse_xtb_export(data: bytes) -> BrokerImport`, gdzie
`BrokerImport` to dataclass z polami `operations`, `positions`, `dividends`, `closed_tickers`,
`warnings`. Warstwa czysta pod spodem, testowana osobno:

- `normalize_operations(rows) -> list[Operation]` — normalizuje etykietę typu XTB
  (`Stock purchase`, `Stock sell`, `Dividend`, `Withholding tax`, `Deposit`, …) na własny
  słownik typów, zachowując oryginał. Wiersz `Total` jest pomijany. Wiersze z tickerem
  niekończącym się na `.PL` są pomijane i zliczane w `warnings`; wiersze bez tickera
  (wpłaty, odsetki) są zachowywane. Pole `comment` jest zachowywane wyłącznie dla typów
  transakcyjnych i dywidendowych — dla ruchów gotówkowych niesie identyfikatory operatora
  płatności bez wartości analitycznej i jest odrzucane.
- `parse_trade_comment(comment) -> tuple[float, float]` — wolumen i cena z komentarza.
  Kontrakt wzorca: `^(OPEN|CLOSE) BUY ([\d.]+)(?:/[\d.]+)? @ ([\d.]+)$`, gdzie **pierwsza
  liczba to wolumen zrealizowany, a druga to całe zlecenie**. Jedno zlecenie rozbija się
  na kilka wierszy; użycie drugiej liczby zdublowałoby wolumen. Niedopasowanie podnosi błąd.
- `reconstruct_positions(operations) -> list[Position]` — FIFO. Kupna sortowane rosnąco
  po czasie, sprzedaże zdejmują najstarsze loty. **`avg_buy_price = pozostały_koszt /
  pozostałe_akcje`** — ten kontrakt jest wiążący, bo `_merge_positions_by_ticker`
  (`src/api.py:355-392`) uśrednia ponownie kosztem w trybie „Wszystkie", a trzy inne
  miejsca liczą P/L jako `(kurs − avg) × akcje`. Tickery z saldem zero trafiają do
  `closed_tickers`, nie do `positions`. Ticker jest normalizowany przez obcięcie `.PL`.
- `extract_dividends(operations) -> list[Dividend]` — **bez parowania dywidendy z podatkiem.**
  Zwracane są pojedyncze zdarzenia; sumy brutto i podatku powstają dopiero w agregacji SQL.
  Parowanie po `(ticker, timestamp)` zawodzi w 24 przypadkach, bo znaczniki rozjeżdżają się
  o sekundy, a jedna wypłata bywa rozbita na kilka wierszy.

#### 5. Złoty zbiór i testy

**File**: `tests/test_brokers_xtb.py` (nowy), `tests/fixtures/xtb_sample.xlsx` (nowy, generowany)

**Intent**: Przypiąć liczbowo zachowanie parsera, zanim powstanie cokolwiek nad nim.

**Contract**: Fixture syntetyzowany skryptem pomocniczym (realnych plików nie wolno
commitować), odwzorowujący wszystkie kształty z prawdziwego eksportu: częściowe fill-e
z ukośnikiem, akcje ułamkowe, ticker zamknięty do zera, wiersz zagraniczny, wiersz `Total`,
dywidendę z podatkiem i dywidendę bez podatku, oraz trzy wypłaty różniące się wyłącznie
mikrosekundami. Asercje obejmują: liczbę pozycji, `shares` i `avg_buy_price` co do grosza,
zawartość `closed_tickers`, liczbę ostrzeżeń o wierszach zagranicznych oraz to, że trzy
wypłaty mikrosekundowe dają **trzy** osobne dywidendy, nie jedną.

Dodatkowo test kontroli krzyżowej: dla każdego tickera suma sprzedaży z `Cash Operations`
równa się wolumenowi z `Closed Positions` — ta niezmienniczość zachodzi na realnych danych
i jest tanim wykrywaczem błędu w parsowaniu fill-ów.

### Success Criteria

#### Automated Verification

- Zależność `openpyxl` jest w sekcji runtime, nie w dev: `uv run python -c "import openpyxl"`
- Testy parsera przechodzą: `uv run pytest tests/test_brokers_xtb.py -v`
- Pełny pakiet jednostkowy pozostaje zielony: `uv run pytest --ignore=tests/e2e`
- Parser nie importuje warstwy danych — asercja w `tests/test_brokers_xtb.py`, że moduł
  `src.brokers.xtb` nie ma w swoich importach niczego z `db` ani `fastapi` (AST na pliku
  albo inspekcja `sys.modules` po świeżym imporcie). **`tach check` tego nie wykryje**:
  `tach.toml:29-33` deklaruje `src` z `depends_on = [{ path = "db" }]`, więc import
  warstwy danych z `src/` jest jawnie dozwolony, a `src.brokers` należy do modułu `src`.
  `tach check` zostaje w pakiecie jako ogólny strażnik architektury, ale nie jest dowodem
  czystości parsera

#### Manual Verification

- Uruchomienie parsera na obu realnych plikach z `export_xtb/` daje 12 pozycji w Głównym
  i 8 w IKZE, zgodnych z wyrocznią zapisaną w `change.md`
- Sumy dywidend zgadzają się: 2 290,71 zł brutto w Głównym i 429,75 zł w IKZE

**Implementation Note**: Po zaliczeniu weryfikacji automatycznej zatrzymaj się i poczekaj
na potwierdzenie ręcznej weryfikacji przed przejściem do fazy 2.

---

## Phase 2: Warstwa BigQuery

### Overview

Nowa tabela operacji, idempotentny zapis, agregacja dywidend i masowy zapis pozycji
mieszczący się w budżecie 60 s.

### Changes Required

#### 1. Tabela operacji

**File**: `db/bigquery.py`

**Intent**: Magazyn surowych operacji brokerskich jako źródło prawdy dla pozycji i dywidend.

**Contract**: Stała `_USER_BROKER_OPERATIONS_TABLE_NAME = "user_broker_operations"`, schemat
`_USER_BROKER_OPERATIONS_SCHEMA`, oraz para
`create_user_broker_operations_table_if_not_exists()` / `ensure_user_broker_operations_schema_current()`
na wzór `notification_sent_log` (`db/bigquery.py:3174-3199`).

Tryby kolumn — `REQUIRED` wyłącznie dla pól, które parser wyprodukuje dla każdego brokera:

| Kolumna | Typ | Tryb |
|---|---|---|
| `user_id` | STRING | REQUIRED |
| `portfolio_id` | STRING | REQUIRED |
| `broker` | STRING | REQUIRED |
| `external_id` | STRING | REQUIRED |
| `op_type` | STRING | REQUIRED |
| `occurred_at` | TIMESTAMP | REQUIRED |
| `amount_pln` | FLOAT | REQUIRED |
| `imported_at` | TIMESTAMP | REQUIRED |
| `raw_type` | STRING | NULLABLE |
| `ticker` | STRING | NULLABLE |
| `instrument_name` | STRING | NULLABLE |
| `volume` | FLOAT | NULLABLE |
| `unit_price` | FLOAT | NULLABLE |
| `comment` | STRING | NULLABLE |
| `source_file` | STRING | NULLABLE |

`external_id` ma postać `"{broker}:{ID}"` — identyfikator operacji pochodzi wprost z kolumny
`ID` eksportu, która jest wypełniona w 571 na 571 wierszy i nie koliduje między plikami.
Hasz treści nie jest potrzebny.

Klastrowanie `["user_id", "ticker"]`, bez partycjonowania — zgodnie z precedensem tabel
per-użytkownik (`user_portfolios`, `user_portfolio_positions`, `notification_sent_log`).
Decyzja jest nieodwracalna, bo `ensure_schema_current` potrafi tylko doklejać kolumny.

#### 2. Rejestracja w hooku startowym

**File**: `src/api.py`

**Intent**: Tabela czytana i zapisywana przez API musi powstawać przy starcie rewizji —
projekt nie ma innego mechanizmu migracji.

**Contract**: W `_init_dimension_tables` (`src/api.py:409-422`) dodać wywołanie `create_*`
**a po nim** `ensure_*`. Kolejność jest wiążąca: `ensure_schema_current` na nieistniejącej
tabeli po cichu wychodzi (`db/bigquery.py:165-167`).

#### 3. Sparametryzowanie MERGE insert-only

**File**: `db/bigquery.py`

**Intent**: Ponownie użyć istniejącego prymitywu idempotentnego importu zamiast kopiować go
po raz trzeci; dziś ma zaszyty klucz `(ticker, snapshot_date)`.

**Contract**: `_merge_insert_only` (`db/bigquery.py:2801-2862`) dostaje dwa nowe parametry
z domyślnymi wartościami zachowującymi zachowanie obecnych wywołań:
`key_columns: tuple[str, ...] = ("ticker", "snapshot_date")` i `order_column: str = "fetched_at"`.
Klauzule `ON` i `QUALIFY ... PARTITION BY` są budowane z `key_columns`. Obaj dzisiejsi
wołający (`merge_company_daily_stats_insert_only`, `merge_etf_quotes_insert_only`) pozostają
bez zmian — wołają pozycyjnie pięcioma argumentami, więc nowe parametry z domyślnymi
wartościami ich nie dotykają.

**Implementation Note**: `tests/test_bigquery_insert_only_merge.py:65` asercjonuje
**dosłowny napis** `"PARTITION BY ticker, snapshot_date"` w wygenerowanym SQL. Builder musi
odtworzyć dokładnie to formatowanie (przecinek + spacja), inaczej istniejący test padnie
mimo w pełni poprawnego zachowania. Ten sam plik liczy wystąpienia podciągów `source`
i `kurs_odn` w całym SQL — nowy interpolowany tekst zawierający te podciągi przesunąłby
licznik.

#### 4. Zapis operacji

**File**: `db/bigquery.py`

**Intent**: Idempotentny zapis partii operacji — ponowny import tego samego pliku ma być
strukturalnie bezpieczny.

**Contract**: `merge_user_broker_operations(rows) -> int`, wołająca `_merge_insert_only`
z `key_columns=("external_id",)` i `order_column="imported_at"`. Zwraca
`num_dml_affected_rows`, czyli liczbę faktycznie nowych operacji — to zasila komunikat
„N nowych, M już było". Brak gałęzi `WHEN MATCHED` oznacza, że istniejący wiersz jest
nietykalny, a `QUALIFY` chroni przed duplikatem w samej partii źródłowej.

#### 5. Masowy zapis pozycji i usunięcie zamkniętych

**File**: `db/bigquery.py`

**Intent**: Zmieścić commit w budżecie 60 s — dwadzieścia sekwencyjnych MERGE-ów to realnie
20–60 s samego oczekiwania na BigQuery.

**Contract**: Dwie funkcje:

- `merge_user_portfolio_positions_bulk(user_id, portfolio_id, positions) -> int` — **jeden**
  MERGE, którego źródłem jest `UNNEST(@positions)` z tablicy STRUCT-ów. Klucz
  `(user_id, portfolio_id, ticker)`, gałąź `WHEN MATCHED THEN UPDATE` i `WHEN NOT MATCHED
  THEN INSERT`, semantyka identyczna z `upsert_user_portfolio_position`
  (`db/bigquery.py:732-783`). **Nigdy `WHEN NOT MATCHED BY SOURCE`** — to skasowałoby
  pozycje nieobecne w pliku, czyli dokładnie S2B.
- `delete_user_portfolio_positions(user_id, portfolio_id, tickers) -> int` — jedno
  `DELETE ... WHERE ticker IN UNNEST(@tickers)`.

Nazwa parametru tablicowego **nie może brzmieć `rows`** — to słowo zarezerwowane w BigQuery.

#### 6. Agregacja dywidend

**File**: `db/bigquery.py`

**Intent**: Zasilić widok dywidend jednym zapytaniem: sumy, rozbicie na spółki i lista
dostępnych lat do wyboru.

**Contract**: `get_dividend_summary(user_id, portfolio_id, year) -> dict` z kluczami
`years`, `totals` (`gross`, `tax`, `net`, `count`) i `by_ticker`. `portfolio_id=None`
oznacza wszystkie portfele użytkownika, `year=None` wszystkie lata. Brutto to suma operacji
typu dywidenda, podatek to suma operacji typu podatek u źródła — **nigdy przez parowanie**.

Lista lat wraca **tym samym zapytaniem**, a join musi być pisany **meta-first**
(`FROM meta LEFT JOIN dane ON ...`), inaczej lista znika, gdy strona agregatu jest pusta —
to konkretna lekcja z PUL-100 (`db/bigquery.py:623-626`). Wiersz metadanowy bez danych
jest odfiltrowywany w Pythonie.

Precyzja timestampów nie może być obcinana przy grupowaniu — rok wyciągamy przez
`EXTRACT(YEAR FROM occurred_at)`, ale identyczność wierszy pozostaje na pełnym znaczniku.

#### 7. Skrypt round-trip

**File**: `scripts/test_bq_broker_operations.py` (nowy)

**Intent**: Mockowane testy nie parsują SQL — to obowiązkowy krok weryfikacji każdej zmiany
w ręcznie sklejanym SQL (`lessons.md:211-235`).

**Contract**: Wzorzec z `scripts/test_bq_insert_only_merge.py:31-71` — tabela jednorazowa
`<nazwa>_rt_<uuid8>` z `expires` ustawionym **przed** `create_table`, podmiana stałej
modułowej, sprzątanie w `finally`. Trzy asercje: nowy klucz się wstawia; powtórny MERGE
tego samego klucza z innymi wartościami nie zmienia wiersza i zwraca zero; zduplikowana
partia źródłowa daje jeden wiersz. Czwarta asercja pokrywa agregację dywidend i obecność
listy lat przy pustym wyniku. `load_dotenv()` przed jakimkolwiek importem `db.*`, wyjście ASCII.

**Piąta asercja: pomiar czasu ściany** dla `merge_user_broker_operations` na partii
571 wierszy (tyle liczy realny eksport). Skrypt drukuje zmierzony czas i **alarmuje powyżej
15 s** — to jedna trzecia budżetu `--timeout=60`, z zapasem na dwa pozostałe kroki commitu
i na narzut zimnego startu. Powód w Performance Considerations: prymityw robi create + load
job + MERGE i nigdy nie był wykonywany w żądaniu HTTP, więc jego koszt w tym kontekście
jest nieznany. Ten pomiar jest tani (skrypt i tak powstaje) i daje odpowiedź w fazie 2,
zanim endpoint commitu w ogóle powstanie.

### Success Criteria

#### Automated Verification

- Testy warstwy danych przechodzą: `uv run pytest tests/test_bigquery.py -v`
- Test regresyjny na treść SQL potwierdza brak nazw zarezerwowanych w kluczowych zapytaniach
- Istniejący pakiet pozostaje zielony: `uv run pytest --ignore=tests/e2e`

#### Manual Verification

- Round-trip na realnym BigQuery przechodzi: `uv run python scripts/test_bq_broker_operations.py`
- Pomiar czasu ściany dla 571 wierszy mieści się poniżej progu 15 s
- Ponowne uruchomienie round-tripu nie tworzy duplikatów i raportuje zero nowych wierszy
- Tabela na produkcji powstaje przy starcie rewizji z oczekiwanym klastrowaniem

**Implementation Note**: Zatrzymaj się na potwierdzenie ręcznej weryfikacji przed fazą 3.

---

## Phase 3: API — upload, podgląd, commit, dywidendy

### Overview

Trzy nowe endpointy, pierwsza w projekcie obsługa uploadu, oraz naprawa unieważniania cache.

### Changes Required

#### 1. Zależność transportowa

**File**: `pyproject.toml`

**Intent**: `UploadFile` bez `python-multipart` podnosi `RuntimeError` przy **definicji trasy**,
czyli wywala `create_app()` i kładzie cały serwis.

**Contract**: `uv add python-multipart` — sekcja runtime. **Musi wejść tym samym commitem
co endpoint.**

#### 2. Modele

**File**: `src/api.py`

**Intent**: Koperta podglądu, która niesie zastrzeżenia razem z danymi zamiast cicho pomijać.

**Contract**: Nowe modele w bloku `src/api.py:181-341` (projekt nie ma `schemas.py`):
`ImportPreviewResponse` z polami `positions`, `closed` (do usunięcia), `unknown_tickers`,
`untouched` (są w portfelu, nie ma w pliku), `dividends_new`, `warnings`.
Wzorzec wprost z `PortfolioHistoryResponse` (`src/api.py:267-274`).

#### 3. Endpoint podglądu

**File**: `src/api.py`

**Intent**: Sparsować plik i pokazać pełne konsekwencje, nie zapisując niczego.

**Contract**: `POST /api/portfolio/import/preview`, multipart: `file`, `broker`, `portfolio_id`.
Deklaruje oba dependency (`_get_role`, `_get_user_id`) zgodnie z konwencją domu.
Własność portfela sprawdzana inline przez `list_user_portfolios` z **404 „Wallet not found"**
— to precedens ścieżek pozycyjnych (`src/api.py:790`), nie 403 z historii.
**Sentynel `all` jest jawnie odrzucany** — żadna ścieżka zapisu go dziś nie obsługuje,
a istniejący guard zwróciłby 404 tylko przypadkiem.

Guard rozmiaru pliku przed parsowaniem (odrzuć powyżej 5 MB) — w aplikacji nie ma
żadnego limitu ciała żądania.

`list_distinct_portfolio_tickers()` wołane **raz**, a cały zbiór tickerów z pliku porównany
w jednym przebiegu. Funkcja nie jest cache'owana, więc wywołanie per wiersz byłoby
dwudziestoma zapytaniami.

**Sekcja `closed` to przecięcie, nie surowy zbiór z parsera.** Parser zwraca w
`closed_tickers` **każdy** ticker, który kiedykolwiek zszedł do zera — w realnych plikach
jest ich 24. Do odpowiedzi i do `DELETE` trafia wyłącznie
`closed_tickers ∩ tickery obecne dziś w portfelu`, wyliczone z tej samej listy pozycji,
która zasila sekcję `untouched`. Bez tego podgląd ogłasza usunięcie kilkunastu pozycji,
których użytkownik nie ma — `DELETE` byłby nieszkodliwym no-opem, ale fałszywe ostrzeżenie
o operacji destrukcyjnej jest racjonalnym powodem, żeby przerwać import (i wprost przeczy
kryterium 6.3). Przecięcie robi warstwa API, bo tylko ona zna stan portfela; parser
pozostaje czysty i zwraca komplet.

Nierozpoznany ticker trafia do `unknown_tickers` w odpowiedzi, **nie** powoduje 422.
Odrzucenie całego uploadu z powodu jednego tickera jest złym zachowaniem dla pliku,
którego użytkownik nie może edytować. 422 zostaje zarezerwowane dla awarii strukturalnych:
nieparsowalny plik, nieznany broker, brak oczekiwanych arkuszy.

**Commit zapisuje taką pozycję mimo wszystko, z jawnym oznaczeniem w podglądzie.**
To świadome odstępstwo od polityki ręcznego dodawania pozycji, gdzie
`src/api.py:833-834` odrzuca nieznany ticker przez 422. Uzasadnienie: przy ręcznym
wpisie nieznany ticker to najpewniej literówka, a przy imporcie to stan faktyczny
rachunku — plik brokera jest źródłem prawdy o tym, co użytkownik posiada, a
`companies`/`etf_instruments` bywają niekompletne. Pominięcie po cichu zawężałoby
portfel o pozycję, której nie da się dodać także ręcznie. Ta sama logika stoi za
nieusuwaniem S2B.

Konsekwencja do obsłużenia w UI: pozycja bez wyceny nie ma kursu, więc wchodzi
do sum jako dziura. Sekcja `unknown_tickers` w podglądzie musi nazwać to wprost —
„zostanie zapisana, ale bez wyceny" — a nie tylko wylistować ticker.

#### 4. Endpoint commitu

**File**: `src/api.py`

**Intent**: Zapisać wszystko albo nic, po potwierdzeniu przez użytkownika.

**Contract**: `POST /api/portfolio/import/commit`, ten sam kontrakt multipart co podgląd —
**plik jest wysyłany ponownie i parsowany od nowa**. Uzasadnienie: cache jest per proces,
a Cloud Run chodzi z dwiema instancjami, więc token podglądu zapisany przez jedną instancję
bywałby nieznany drugiej. Parser jest deterministyczny.

Sekwencja: `merge_user_broker_operations` → `merge_user_portfolio_positions_bulk` →
`delete_user_portfolio_positions` dla zamkniętych → unieważnienie cache. Trzy kroki logiczne,
nie pętla — budżet 60 s.

`delete_user_portfolio_positions` dostaje **to samo przecięcie**, które podgląd pokazał
w sekcji `closed` — nigdy surowego `closed_tickers`. Podgląd i commit muszą liczyć je
identycznie, bo commit parsuje plik od nowa i to jedyna gwarancja, że użytkownik
potwierdził dokładnie to, co się wykona.

Odpowiedź zwraca liczby faktycznie zapisane, w tym liczbę nowych operacji z
`num_dml_affected_rows`.

#### 5. Endpoint dywidend

**File**: `src/api.py`

**Intent**: Zasilić czwartą zakładkę.

**Contract**: `GET /api/portfolio/dividends?portfolio_id=&year=`, klonowany z najbliższego
rodzeństwa zgodnie z przyjętym w projekcie wzorcem. Obsługuje sentynel `all` po stronie
odczytu (pomija sprawdzenie własności, przekazuje `None` w dół). Walidacja `year` **przed**
zbudowaniem klucza cache, inaczej dowolny string zatruwa cache. Cache `dividends:{user}:{portfolio}:{year}`.

#### 6. Naprawa unieważniania cache

**File**: `src/api.py`

**Intent**: Zamknąć dwie pre-istniejące dziury, które import ujawniłby na domyślnej zakładce
zaraz po potwierdzeniu.

**Contract**: `_perf_invalidate_portfolio` (`src/api.py:98-103`) dodatkowo zdejmuje klucze
o prefiksie `history:{user_id}:{portfolio_id}:` (rozgałęzia się na cztery wartości `range`,
więc konieczny skan prefiksu, analogicznie do dzisiejszej pętli kalendarza), warianty
sentynela `all` dla pozycji, kalendarza i historii, oraz nowy prefiks `dividends:{user_id}:`.
Poprawka naprawia przy okazji dzisiejszą ścieżkę zapisu pojedynczej pozycji, która cierpi
na to samo.

#### 7. Conftest e2e

**File**: `tests/e2e/conftest.py`

**Intent**: Każda niezapatchowana funkcja DDL powoduje, że cała sesja e2e startuje przeciwko
żywemu BigQuery.

**Contract**: Patche na `src.api.*` (miejsce importu, nie `db.bigquery.*`) dla:
`create_user_broker_operations_table_if_not_exists`, `ensure_user_broker_operations_schema_current`,
`merge_user_broker_operations`, `merge_user_portfolio_positions_bulk`,
`delete_user_portfolio_positions`, `get_dividend_summary`.

Fake'i muszą być **stateful** (`side_effect=`, nie `return_value=`) — przepływ
podgląd → commit → odczyt jest wielożądaniowy. Fake podglądu musi zwracać dane renderujące
**wszystkie** sekcje ujawnienia, w tym `closed`, `unknown_tickers` i `untouched`.
To lekcja z PUL-100: gałąź `excluded` nigdy nie została wykonana, bo każdy fake zwracał
pustą listę. Najgorszy przypadek to ten, którego żaden test nie renderuje.

`list_distinct_portfolio_tickers` już zwraca `["CDR", "ETFBW20TR", "PKO", "XTB"]` — fixture
importu musi używać podzbioru albo lista musi zostać rozszerzona.

### Success Criteria

#### Automated Verification

- Testy API przechodzą: `uv run pytest tests/test_api.py -v`
- Test potwierdza, że podgląd niczego nie zapisuje
- Test potwierdza, że commit z sentynelem `all` jest odrzucany
- Test potwierdza, że nierozpoznany ticker trafia do odpowiedzi (nie 422) i że commit go zapisuje
- Test unieważniania cache pokrywa `history:`, sentynel `all` i `dividends:`
- Pełny pakiet zielony: `uv run pytest`

#### Manual Verification

- Podgląd na realnym pliku przez `TestClient` zwraca 12 pozycji dla Głównego
- Aplikacja wstaje po dodaniu `python-multipart` — `/health` odpowiada

**Implementation Note**: Zatrzymaj się na potwierdzenie przed fazą 4.

---

## Phase 4: Front — okno importu

### Overview

Przycisk obok Eksport CSV, modal z wyborem brokera i pliku, podgląd z pełnym ujawnieniem.

### Changes Required

#### 1. Przycisk

**File**: `static/index.html`

**Intent**: Wejście do funkcji tam, gdzie prosił użytkownik.

**Contract**: Przycisk `#pp-import-btn` tuż za `#pp-export-csv-btn` (`static/index.html:3671`),
stylowany jak sąsiad, z wpisem w liście selektorów motywu ciemnego (`:988`).
**Musi respektować tryb read-only sentynela** — wchodzi do `_ppSyncAddBtnVisibility()`
(`:3520-3523`) obok `#pp-add-toggle-btn`, inaczej pojawia się na zakładce „Wszystkie",
gdzie zapis jest zabroniony po obu stronach.

#### 2. Modal

**File**: `static/index.html`

**Intent**: Okno importu na wzór istniejącego modalu dodawania portfela.

**Contract**: `#pp-import-overlay` / `#pp-import-modal` wewnątrz szablonu
`_buildPortfolioPositionsViewContent` (`:3653-3820`), wzorowany na
`#pp-add-portfolio-overlay` (`:3799-3819`): `<select>` domu maklerskiego,
`<input type="file" accept=".xlsx">`, `<div>` błędu inline, `.pp-modal-actions`.

Jedna poprawka wobec szablonu: **dodać handler Escape strzeżony widocznością**, jak
`:3947`. Wzorzec dodawania portfela go nie ma, a niestrzeżony globalny listener
(`:3908`) to istniejący błąd, którego nie powielamy.

Wpisy w selektorach motywu ciemnego dla `select`, `input` i tła panelu (`:941-957`).

#### 3. Podgląd i commit

**File**: `static/index.html`

**Intent**: Pokazać wszystkie konsekwencje przed zapisem i zapisać po potwierdzeniu.

**Contract**: `_ppSubmitImportPreview()` wysyła `FormData` na endpoint podglądu i renderuje
pięć sekcji: do zapisania, zamknięte według pliku (z jawną informacją, że zostaną usunięte),
nierozpoznane tickery, pominięte bo nie ma ich w pliku, oraz liczba nowych dywidend.
`_ppSubmitImportCommit()` wysyła **ten sam obiekt pliku** ponownie.

Konwencje domu: `X-API-Key` w nagłówku, `if (r.status === 401) { doLogout(); return; }`,
błędy do `<div>` inline zamiast `alert()`, `esc()` na każdej ścieżce `innerHTML`,
przycisk wyłączany synchronicznie w handlerze kliknięcia (lekcja #9 — wyłączenie wewnątrz
funkcji async następuje po zrzuceniu stanu i nie zamyka okna wyścigu).

Po udanym commicie: `showToast()` z podsumowaniem, zamknięcie modalu, oraz **wyczyszczenie
czterech cache'y frontowych** — `_ppTreemapData`, `_ppCalData`, `_ppHistDataActive`,
`_ppHistDataAll` (`:3246-3269`). Dzisiejsza ścieżka zapisu pojedynczej pozycji tego nie robi,
więc bez tego treemapa, kalendarz i wykres pokażą stan sprzed importu.

#### 4. Testy e2e

**File**: `tests/e2e/test_portfolio_import.py` (nowy)

**Intent**: Przypiąć przepływ i sekcje ujawnienia.

**Contract**: Lokatory przez `getByRole` / `getByLabel`; **test najpierw klika zakładkę
„Główny"**, bo domyślna „Wszystkie" jest read-only. Asercje na widoczność wszystkich pięciu
sekcji podglądu, na to, że przycisk importu **nie** jest widoczny na zakładce „Wszystkie",
oraz że po commicie tabela pokazuje zaimportowane pozycje. Bez `page.waitForTimeout()`.
Nie dodajemy nowych bytów do współdzielonego conftestu bez audytu istniejących lokatorów —
PUL-90 odrzuciło taki dodatek, bo destabilizował cały pakiet przez strict-mode.

### Success Criteria

#### Automated Verification

- Składnia inline JS: `node --check` na wyekstrahowanym skrypcie
- Testy e2e importu przechodzą: `uv run pytest tests/e2e/test_portfolio_import.py`
- Pełny pakiet zielony: `uv run pytest`
- Testy potwierdzają ukrycie przycisku importu w trybie „Wszystkie"

#### Manual Verification

- Import realnego pliku Głównego w przeglądarce pokazuje 12 pozycji, CBF z ceną 188,40,
  a S2B w sekcji pominiętych
- Po potwierdzeniu treemapa, kalendarz i wykres pokazują dane po imporcie, bez odświeżania strony
- Escape i klik poza modalem zamykają okno; brak błędów w konsoli
- Motyw jasny i ciemny wyglądają poprawnie

**Implementation Note**: Zatrzymaj się na potwierdzenie przed fazą 5.

---

## Phase 5: Front — zakładka Dywidendy

### Overview

Czwarty tryb `data-mode` z wyborem roku, kafelkami i rozbiciem na spółki.

### Changes Required

#### 1. Zakładka i panel

**File**: `static/index.html`

**Intent**: Nowy tryb obok Tabela / Treemapa / Kalendarz.

**Contract**: Przycisk `.pp-view-tab[data-mode="dividends"]` w `#pp-view-tabs` (`:3664-3668`)
oraz panel `#pp-dividends-wrap`.

**Krytyczne**: handler trybów to **dwa sąsiadujące bloki**, nie jeden. Plan musi ruszyć oba:

1. **Blok ukrywania paneli** (`:3846-3849`) — cztery przypisania `style.display`.
   Nowy panel dostaje własną linię, a widoczność `#pp-portfolio-tabs-wrap` (`:3846`) musi
   uwzględnić nowy tryb, bo dziś przełącznik portfeli pokazuje się tylko dla `table`
   i `calendar`.
2. **Łańcuch efektów ubocznych** (`:3850-3859`) — `if (mode === 'table') … else if
   ('treemap') … else if ('calendar')`. Bez czwartej gałęzi kliknięcie zakładki pokaże
   **pusty panel**, bo nikt nie zawoła fetcha. Gałąź `else if (mode === 'dividends')`
   musi wołać `stopPortfolioTreemapResize()` (inaczej przejście treemapa → dywidendy
   zostawia działający observer resize) oraz `if (!_ppDivData) fetchPortfolioDividends()`.

Dodatkowo gałąź w `_selectPortfolioTab()` (`:3536-3538`), żeby zmiana portfela odświeżała dane.

Przywrócenie trybu z URL (`:3578-3580`) działa bez zmian — selektor filtruje po
`data-mode`, więc nowa zakładka wpina się sama. Nowe przyciski roku **nie mogą** trafić
do `#pp-view-tabs`: handler trybów jest do tego kontenera zawężony właśnie po to, żeby
przełączniki zakresu i metryki (`:3776-3783`), które współdzielą klasę `.pp-view-tab`,
nie przełączały widoku.

Wybór roku realizowany pigułkami `.pp-view-tabs-inline` na wzór `#pp-history-ranges`
(`:3775-3784`) — to konwencja tego widoku; lista lat pochodzi z odpowiedzi endpointu,
więc rozszerza się sama.

#### 2. Renderowanie

**File**: `static/index.html`

**Intent**: Kafelki podsumowania i tabela per spółka.

**Contract**: `_renderPortfolioDividends(data)` — własna funkcja renderująca, bo
`_renderPortfolioTable` (`:3344-3405`) jest przywiązany do dziewięciu stałych kolumn.
Ponownie używamy `_showSkeleton` (`:2274`), `_sortRows` (`:2263`) i `esc()` (`:4704`).

**Kafelki muszą pokazywać brutto, podatek i netto osobno.** IKZE ma zerowy podatek u źródła,
więc brutto równa się netto — pokazanie tylko jednej liczby sprawia, że zestawienie kont
wygląda na zepsute. Nagłówek nazywa rzecz po imieniu: **dywidendy gotówkowe**, bo rzeczowych
w eksporcie nie ma i sumy nie mogą sugerować kompletności.

Guard sekwencji żądań (`_ppDivReqSeq`) na wzór `_ppHistReqSeq` — przełączanie roku
i portfela wywołuje wiele fetchy, a odpowiedź spóźniona nie może nadpisać nowszej (lekcja #9).

#### 3. Testy e2e

**File**: `tests/e2e/test_portfolio_dividends.py` (nowy)

**Contract**: Otwarcie zakładki, przełączenie roku, asercje na kafelki i tabelę, oraz
regresja: po przejściu na inną zakładkę panel dywidend jest ukryty.

### Success Criteria

#### Automated Verification

- Testy e2e dywidend przechodzą: `uv run pytest tests/e2e/test_portfolio_dividends.py`
- Test regresyjny potwierdza ukrycie panelu przy przełączeniu trybu
- Pełny pakiet zielony: `uv run pytest`
- `node --check` na inline JS

#### Manual Verification

- Zakładka pokazuje lata 2025 i 2026 oraz opcję „wszystkie"
- Główny za 2025: 1 253,40 brutto, −238,17 podatek, 1 015,23 netto
- IKZE pokazuje brutto równe netto i zerowy podatek, bez sprawiania wrażenia błędu
- Rozbicie na spółki zgadza się z wyrocznią (KRU 722 zł, XTB 444, VOT 421 w Głównym)

**Implementation Note**: Zatrzymaj się na potwierdzenie przed fazą 6.

---

## Phase 6: Weryfikacja produkcyjna

### Overview

Faza bez nowego kodu. Podgląd z natury niczego nie zapisuje, więc pełni rolę dry-runu.
Krok bramkowany przez człowieka, wzorowany na rytuale z backfillu historycznych kursów.

### Changes Required

Brak zmian w kodzie. Wykonanie:

1. Deploy przez merge do `master` (CI robi to automatycznie — nigdy ręcznie).
2. Podgląd pliku IKZE na produkcji. Oczekiwane: 8 pozycji, zero do usunięcia, zero
   nierozpoznanych, dywidendy 429,75 zł brutto. IKZE jest bezpieczniejszym pierwszym
   celem, bo wyrocznia zgadza się tam do 0,47 zł łącznie.
3. Potwierdzenie importu IKZE. Weryfikacja tabeli pozycji i zakładki dywidend.
4. Podgląd pliku Głównego. Oczekiwane: 12 pozycji, **CBF ze zmianą 199,40 → 188,40**,
   **S2B w sekcji pominiętych**, zero do usunięcia.
5. Potwierdzenie importu Głównego.
6. Ponowny import obu plików — oczekiwane zero nowych operacji, brak duplikatów, brak
   zmian w pozycjach. To dowód idempotencji na realnych danych.

### Success Criteria

#### Automated Verification

- `/health` odpowiada po deployu
- Zapytanie kontrolne na BigQuery potwierdza brak duplikatów `external_id`

#### Manual Verification

- Podgląd IKZE zgadza się z wyrocznią przed jakimkolwiek zapisem
- Po imporcie Głównego CBF ma cenę 188,40, a S2B pozostaje z 4 sztukami po 0,01 zł
- Zakładka „Wszystkie" natychmiast po commicie pokazuje dane po imporcie, nie sprzed
- Powtórny import obu plików raportuje zero nowych operacji
- Sumy dywidend na produkcji zgadzają się z wyliczonymi: 2 290,71 zł i 429,75 zł

---

## Testing Strategy

### Unit Tests

- Parsowanie komentarza transakcji: częściowe fill-e z ukośnikiem, brak ukośnika,
  wartości ułamkowe, komentarz niepasujący do wzorca (musi podnieść błąd, nie zgadywać)
- Rekonstrukcja FIFO: pojedynczy lot, wiele lotów, sprzedaż częściowa przecinająca lot,
  sprzedaż całości, akcje ułamkowe
- Kontrakt `avg_buy_price = pozostały_koszt / pozostałe_akcje` sprawdzony liczbowo
- Wyodrębnianie dywidend: dywidenda z podatkiem, dywidenda bez podatku (przypadek IKZE),
  trzy wypłaty różniące się wyłącznie mikrosekundami dają trzy osobne rekordy
- Pominięcie wierszy zagranicznych i wiersza `Total`, z liczbą ostrzeżeń
- Mapowanie kolumn po nagłówku: brak oczekiwanej kolumny podnosi błąd z jej nazwą
- Agregacja dywidend: pusty wynik nadal zwraca listę lat (regresja na join meta-first)

### Integration Tests

- Podgląd nie wykonuje żadnego zapisu
- Commit z sentynelem `all` zwraca błąd
- Nierozpoznany ticker trafia do odpowiedzi podglądu, pozostałe pozycje przechodzą
- Unieważnianie cache obejmuje `history:`, sentynel `all` i `dividends:`
- Round-trip na realnym BigQuery: wstawienie, powtórka bez zmian, zduplikowana partia

### Manual Testing Steps

1. Otwórz Mój portfel, zakładka „Główny" — przycisk Importuj jest widoczny
2. Przełącz na „Wszystkie" — przycisk znika
3. Wróć na „Główny", otwórz modal, wybierz XTB i plik, sprawdź podgląd
4. Zweryfikuj wszystkie pięć sekcji ujawnienia, w szczególności obecność S2B wśród pominiętych
5. Potwierdź, sprawdź tabelę, treemapę, kalendarz i wykres bez odświeżania strony
6. Otwórz zakładkę Dywidendy, przełącz rok, sprawdź kafelki i rozbicie na spółki
7. Powtórz import tego samego pliku — zero nowych operacji

## Performance Considerations

Commit wykonuje **trzy kroki logiczne** niezależnie od liczby pozycji: MERGE operacji,
MERGE pozycji przez `UNNEST`, oraz `DELETE` zamkniętych. To projektowana odpowiedź na limit
`--timeout=60` — pętla po pozycjach z `upsert_user_portfolio_position` (1–3 s każde) dałaby
realnie 20–60 s dla dwudziestu tickerów.

**To nie są trzy zapytania.** `_merge_insert_only` (`db/bigquery.py:2819-2830`) tworzy tabelę
tymczasową, ładuje wiersze osobnym jobem `load_table_from_json`, i dopiero potem wykonuje
MERGE — sam pierwszy krok to trzy operacje BigQuery. Realny commit to około pięciu operacji.

Istotniejsze od arytmetyki: **ten prymityw nigdy nie działał wewnątrz żądania HTTP.** Obaj
dzisiejsi wołający (`scripts/backfill_historical_closes.py`, job company-stats) to zadania
wsadowe, gdzie limit 60 s nie obowiązuje. 571 wierszy to mało i najprawdopodobniej zmieści
się bez problemu, ale to założenie do zmierzenia, nie fakt policzony — stąd pomiar czasu
ściany w skrypcie round-trip fazy 2, zanim cokolwiek zacznie od tego budżetu zależeć.

Parsowanie plików o rozmiarze 15–41 KB jest nieistotne czasowo, ale `openpyxl` w trybie
domyślnym materializuje cały skoroszyt — przy 512 MiB używamy `read_only=True`.

`list_distinct_portfolio_tickers()` nie jest cache'owana; wywołujemy ją raz na import.

Cache jest per proces i bez eksmisji, a Cloud Run chodzi z dwiema instancjami. Zapis
obsłużony przez jedną instancję nie unieważni kopii drugiej — to własność istniejąca,
niezmieniana tym planem, ale warta odnotowania przy weryfikacji ręcznej.

## Migration Notes

Nowa tabela powstaje przez hook startowy przy pierwszym starcie nowej rewizji — projekt
nie ma innego mechanizmu migracji. Kolumny są dobrane pod ten model raz: klastrowanie
`["user_id", "ticker"]` i brak partycjonowania są **nieodwracalne**, bo
`ensure_schema_current` potrafi wyłącznie doklejać kolumny.

Nie zmieniamy schematu żadnej istniejącej tabeli, więc lekcja o kolejności przy kolumnach
`REQUIRED` (`lessons.md:294-325`) nie ma tu zastosowania — ale wszystkie kolumny
niebędące tożsamością są `NULLABLE` na wypadek, gdyby przyszły broker ich nie dostarczał.

**Wycofanie**: import jest addytywny dla operacji (MERGE insert-only), więc cofnięcie
sprowadza się do `DELETE FROM user_broker_operations WHERE user_id = @u AND broker = 'xtb'`.
Zmiany w pozycjach są nadpisaniem — przed pierwszym importem produkcyjnym warto zrzucić
`user_portfolio_positions` dla obu portfeli, żeby mieć punkt powrotu. Usunięcie tickerów
zamkniętych jest jedyną operacją nieodwracalną bez takiego zrzutu.

## References

- Analiza plików źródłowych i wyrocznia liczbowa: `context/changes/xtb-portfolio-import/change.md`
- Rozpoznanie kodu: `context/changes/xtb-portfolio-import/research.md`
- MERGE insert-only: `db/bigquery.py:2801-2862`
- Wzorzec nowej tabeli: `db/bigquery.py:3174-3199`
- Szablon modalu: `static/index.html:3799-3819`
- Wzorzec round-tripu: `scripts/test_bq_insert_only_merge.py:31-71`
- Precedens importu zewnętrznych plików: `context/archive/2026-07-24-backfill-historical-closes/`
- Precedens koperty z zastrzeżeniami: `context/archive/2026-07-26-history-coverage-gate/`

## Progress

> Konwencja: `- [ ]` oczekuje, `- [x]` zrobione. Dopisz ` — <commit sha>` gdy krok wyląduje.
> Nie zmieniaj tytułów kroków.

### Phase 1: Czysty parser XTB i silnik FIFO

#### Automated

- [x] 1.1 Zależność `openpyxl` jest w sekcji runtime, nie w dev — 9957b33
- [x] 1.2 Testy parsera przechodzą — 9957b33
- [x] 1.3 Pełny pakiet jednostkowy pozostaje zielony — 9957b33
- [x] 1.4 Asercja w teście potwierdza, że parser nie importuje `db` ani `fastapi` — 9957b33

#### Manual

- [x] 1.5 Parser na realnych plikach daje 12 pozycji w Głównym i 8 w IKZE — 9957b33
- [x] 1.6 Sumy dywidend zgadzają się: 2 290,71 zł i 429,75 zł — 9957b33

### Phase 2: Warstwa BigQuery

#### Automated

- [x] 2.1 Testy warstwy danych przechodzą — fcd82be
- [x] 2.2 Test regresyjny na treść SQL potwierdza brak nazw zarezerwowanych — fcd82be
- [x] 2.3 Istniejący pakiet pozostaje zielony — fcd82be

#### Manual

- [x] 2.4 Round-trip na realnym BigQuery przechodzi — fcd82be
- [x] 2.5 Pomiar czasu ściany dla 571 wierszy mieści się poniżej progu 15 s (zmierzone 3,7 s) — fcd82be
- [x] 2.6 Ponowny round-trip nie tworzy duplikatów i raportuje zero nowych wierszy — fcd82be
- [ ] 2.7 Tabela na produkcji powstaje z oczekiwanym klastrowaniem — funkcja DDL
      zweryfikowana na realnym BQ (klastrowanie, brak partycjonowania, idempotencja);
      część „przy starcie rewizji" domyka się deployem w fazie 6

### Phase 3: API — upload, podgląd, commit, dywidendy

#### Automated

- [x] 3.1 Testy API przechodzą — 0453ba8
- [x] 3.2 Test potwierdza, że podgląd niczego nie zapisuje — 0453ba8
- [x] 3.3 Test potwierdza, że commit z sentynelem `all` jest odrzucany — 0453ba8
- [x] 3.4 Test potwierdza, że nierozpoznany ticker trafia do odpowiedzi (nie 422) i że commit go zapisuje — 0453ba8
- [x] 3.5 Test unieważniania cache pokrywa `history:`, sentynel `all` i `dividends:` — 0453ba8
- [x] 3.6 Pełny pakiet zielony — 0453ba8

#### Manual

- [x] 3.7 Podgląd na realnym pliku przez TestClient zwraca 12 pozycji dla Głównego — 0453ba8
- [x] 3.8 Aplikacja wstaje po dodaniu `python-multipart`, `/health` odpowiada — 0453ba8

### Phase 4: Front — okno importu

#### Automated

- [x] 4.1 `node --check` na inline JS przechodzi
- [x] 4.2 Testy e2e importu przechodzą
- [x] 4.3 Pełny pakiet zielony
- [x] 4.4 Testy potwierdzają ukrycie przycisku importu w trybie „Wszystkie"

#### Manual

- [x] 4.5 Import Głównego pokazuje 12 pozycji, CBF 188,40, S2B wśród pominiętych
- [x] 4.6 Treemapa, kalendarz i wykres pokazują dane po imporcie bez odświeżania
      — pokryte testem e2e `test_commit_refreshes_the_other_views_without_a_reload`,
      break-verified (usunięcie czyszczenia cache wywala test)
- [x] 4.7 Escape i klik poza modalem zamykają okno, brak błędów w konsoli
- [x] 4.8 Motyw jasny i ciemny wyglądają poprawnie

### Phase 5: Front — zakładka Dywidendy

#### Automated

- [ ] 5.1 Testy e2e dywidend przechodzą
- [ ] 5.2 Test regresyjny potwierdza ukrycie panelu przy przełączeniu trybu
- [ ] 5.3 Pełny pakiet zielony
- [ ] 5.4 `node --check` na inline JS przechodzi

#### Manual

- [ ] 5.5 Zakładka pokazuje lata 2025, 2026 i opcję „wszystkie"
- [ ] 5.6 Główny za 2025: 1 253,40 brutto, −238,17 podatek, 1 015,23 netto
- [ ] 5.7 IKZE pokazuje brutto równe netto bez sprawiania wrażenia błędu
- [ ] 5.8 Rozbicie na spółki zgadza się z wyrocznią

### Phase 6: Weryfikacja produkcyjna

#### Automated

- [ ] 6.1 `/health` odpowiada po deployu
- [ ] 6.2 Zapytanie kontrolne potwierdza brak duplikatów `external_id`

#### Manual

- [ ] 6.3 Podgląd IKZE zgadza się z wyrocznią przed jakimkolwiek zapisem
- [ ] 6.4 Po imporcie Głównego CBF ma 188,40, a S2B pozostaje nietknięty
- [ ] 6.5 Zakładka „Wszystkie" natychmiast po commicie pokazuje dane po imporcie
- [ ] 6.6 Powtórny import obu plików raportuje zero nowych operacji
- [ ] 6.7 Sumy dywidend na produkcji zgadzają się z wyliczonymi
