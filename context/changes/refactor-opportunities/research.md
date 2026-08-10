---
change_id: refactor-opportunities
title: Refactor opportunities — ranking kandydatów (element ④)
kind: research
created: 2026-08-08
last_updated: 2026-08-10
status: verified
tags: [architect, m4l4, refactor-opportunities, portfolio, verified]
---

# Refactor opportunities

> Wejście: `context/changes/portfolio-lots-flow/research.md` (problemy T1–T8, dług tani D1–D4).
> Metoda: trzy sub-agenty — obecny kształt / **historia i intencjonalność** / wykonalność
> migracji. Rygor **evidence / inference / unknown**.
>
> **Na tym etapie nie zapada żadna decyzja.** Ranking jest propozycją do sesji planowania.

---

## 1. Klasyfikacja problemów (do audytu)

Rozbicie względem wejścia: **T3 podzielone** na T3a (brak bramki — konfiguracja) i T3b (samo
naruszenie warstwy — struktura). **Dodane P1** z mapy, bo jest przyczyną D2 i połowy T5.

| id | Problem | Klasyfikacja |
| -- | -- | -- |
| T1 | dwie rekonstrukcje stanu posiadania (SQL wstecz + Python FIFO wprzód) | **KANDYDAT** |
| T2 | `ORDER BY occurred_at` bez klucza wtórnego | **KANDYDAT** |
| T3a | `tach` nie jest bramką | NIE-KANDYDAT (konfiguracja) |
| T3b | naruszenie warstwy `db → src.portfolio_lots` | **KANDYDAT** |
| T4 | kontrakt dataclass niewidoczny dla narzędzi | **KANDYDAT** (zawężony — patrz §3) |
| T5 | adaptery BigQuery bez testów | NIE-KANDYDAT strukturalny → **wejście do wykonalności** |
| T6 | akcje korporacyjne wypadają cicho | **ZATRZYMANY** → M4L5/DDD |
| T7 | `/realized` bez modelu odpowiedzi | **KANDYDAT** |
| T8 | preview wykonuje pracę commitu | NIE-KANDYDAT — werdykt „świadome ograniczenie" |
| F1 | walidacja domenowa w handlerze, nie w modelu | **KANDYDAT** |
| F2 | dwie rozłączne drogi zapisu do portfela | **ZATRZYMANY** → M4L5/DDD |
| P1 | 51 nazw z `db.bigquery` w przestrzeni `src.api` | **KANDYDAT** |
| D1–D4, K1–K3 | dyscyplina / mierniki / fałszywe alarmy / kosmetyka | NIE-KANDYDAT |

**Dwa zatrzymania na granicy domeny.** T6 i F2 wymagają pojęć, których model danych nie ma
(*zdarzenie korporacyjne*, *waluta pozycji*, *czym jest pozycja bez lotów*). Zgodnie z twardą
granicą kontraktu: nazwane i przekazane do analizy domenowej, nie projektowane tutaj.

---

## 2. Werdykty intencjonalności

Najważniejsza sekcja — decyduje o **guard vs przebudowa**. Źródło: `context/archive/`
(79 zamkniętych zmian, każda z tabelą „Key Decisions Made") + archeologia gita.

| Problem | Werdykt | Dowód |
| -- | -- | -- |
| **T1** | **ŚWIADOME OGRANICZENIE po obu stronach** + luka w egzekwowaniu | Rozdział był **zaplanowany**: `archive/2026-07-30-calendar-holdings-as-of-date/plan-brief.md` wyłącza bazę kosztową z zakresu z powodem „**FIFO wymaga Pythona**". Docstring SQL: „That direction is deliberate and load-bearing" (`db/bigquery.py:414-440`) |
| **T2** | ŚWIADOME OGRANICZENIE, **częściowo** zmierzone | `archive/2026-08-04-fifo-lot-ledger/plan.md:37` — „Measured: **0 tied buy/sell instants** across 508 production rows". `impl-review.md` F2 podnosi remis i dysponuje jako „unreachable in practice" |
| **T3a/T3b** | ŚWIADOME + **korekta faktograficzna** | `tach.toml` powstało w `ac295ad` (2026-06-09) jako **artefakt ćwiczenia z lekcji**, nigdy jako bramka. `git log -S"tach" -- pyproject.toml uv.lock` → **0 commitów**. Sama krawędź jest udokumentowaną decyzją (`archive/2026-08-05-…/research.md:154-161`) |
| **T4** | **WYMUSZONE** co do 3 z 4 kopii; **UNKNOWN** co do dostępu atrybutowego | `_BASIS_SEGMENT_TYPE` = wymóg API `google-cloud-bigquery`; literał `ARRAY<STRUCT<…>>[]` = obejście **udokumentowanego buga klienta**; czwarta kopia = **guard dodany na żądanie bramki `ai-code-review`** (`39855da`, PR #250) |
| **T5** | ŚWIADOME OGRANICZENIE **z kompensacją** — ale kompensacja nie jest w CI | Strategia: `context/foundation/test-plan.md:17-23` „unit tests with mocked boundaries". Znany mechanizm porażki: `lessons.md:211-235` (PUL-29) „mockowane testy BQ **nie weryfikują składni SQL** → round-trip **obowiązkowy**". Kompensacja: 14 skryptów `scripts/test_bq_*.py`, **żaden nie biegnie w CI** |
| **T6** | **ZŁOŻONOŚĆ PRZYPADKOWA nałożona na świadome ograniczenie** | Gwarancja „monotonicznie samo-naprawiające" (`archive/2026-07-30-…/research.md:347-349`) dotyczy **wyłącznie rekonstrukcji SQL**. Ledger FIFO **nie jest** samo-naprawiający — nieprzeparsowany split zostawia lot z przedrozłamową liczbą sztuk i to wchodzi wprost do `basis(D)`. **Archiwum tej asymetrii nigdzie nie odnotowuje** |
| **T7** | ZŁOŻONOŚĆ PRZYPADKOWA — ale **nie lokalna: to konwencja całego repo** | **0 wystąpień `response_model=` przy 32 trasach.** `/realized` jest strukturalnie identyczny z sąsiednim `/dividends`. Kształt wynikł z powielenia sąsiada |
| **T8** | ŚWIADOME OGRANICZENIE — potwierdzone **dwukrotnie, w tym infrastrukturalnie** | `archive/2026-07-29-xtb-portfolio-import/plan-brief.md:78-79` + `deploy.yml:130-132`: `--min-instances=0 --max-instances=4` czyni cache per-proces niewiarygodnym |

### Historia bez wpadek

Najmocniejszy argument za „guard zamiast przebudowy" — czy kształt kiedykolwiek kosztował:

| Problem | Wiek kształtu | Commit naprawiający błąd *z tego kształtu*? |
| -- | -- | -- |
| T1 (współistnienie) | **3 dni** | Nie — **i nie mogło być**, kształt młodszy niż jeden cykl zgłoszeń |
| T2 | 9 dni | Nie. `git log -S"_sort_key"` → 2 commity, oba tworzące |
| T3 | 60 dni | Nie. **`tach check` przechodzi dziś na zielono** — bramka nie złapałaby niczego |
| T4 | 3 dni | **Odwrotnie** — czwarta „kopia" *jest* naprawą, wymuszoną przez bramkę AI review |
| **T5** | **61 dni** | **TAK — dwukrotnie.** PUL-29 (2026-06-14): brak backticków → `400 Syntax error` na prodzie mimo zielonych testów. PUL-88 (2026-07-26): `REQUIRED client_id` → **prod padł na ~2 min**, `dry_run=True` przeszedł |
| T6 | 10 dni | Nie — ale brak też dowodu, że split w ogóle wystąpił w danych |
| T7 | 57 dni, 32 trasy | Nie. Zero commitów naprawiających rozjazd kontraktu drutu |
| T8 | 10 dni | Odwrotnie — kształt istnieje, żeby zapobiec awarii |

**Siedem z ośmiu kandydatów nie ma ani jednego commita naprawczego.** Dla T1 i T4 (3 dni) brak
wpadki **nie jest dowodem, tylko brakiem danych**. Jedyny kandydat, który realnie kosztował
produkcję, to **T5 — dwa razy, obie wpadki tej samej klasy**, obie złapane mechanizmem
(round-trip), który **nadal nie jest w CI**.

---

## 3. Refactor opportunities (ranking)

### 🥇 C1 — Domknięcie testowe adapterów BigQuery (z T5)

| | |
| -- | -- |
| **Obecny kształt** | `list_broker_trades` (`db/bigquery.py:4127`) — **jedyne wejście produkcyjne do całego przepływu FIFO** — ma **zero testów** i **zero round-tripu**. `_first_open_lot_dates` (`:1646`) testowane wyłącznie na pustym wejściu (`tests/test_bigquery.py:1770-1778`, `return_value=[]`) |
| **Docelowy kształt** | *Osłona testowa na szwie odczytu* — nie zmiana struktury |
| **Dlaczego #1** | **Jedyny kandydat z udokumentowaną historią kosztów produkcyjnych** (2×, w tym 2-minutowa awaria). Koszt długu jest zmierzony, nie hipotetyczny. Koszt zmiany: S. Idiom gotowy o dwa pliki dalej |
| **Blast radius** | **Żaden** — nowe funkcje testowe, zero zmian w kodzie produkcyjnym |
| **Ścieżka inkrementalna** | (1) test `list_broker_trades` na `_mock_client_for_select` + `_Row(dict)` (`tests/test_bigquery_broker_operations.py:235-250`) — pin `op_type IN ('buy','sell')`, `ticker IS NOT NULL`, obecności `portfolio_id` w filtrze i parametrach → (2) `_first_open_lot_dates` na **niepustym** wejściu |
| **Pierwszy prerekwizyt** | Żaden. Startuje od razu |
| **Co złapie regresję** | Blokująca bramka `Tests` (`.github/workflows/tests.yml:44`, wymagany status na master) |

⚠️ **Pułapka do uniknięcia:** `list_broker_trades` kończy `return [dict(row) for row in rows]`
(`db/bigquery.py:4164`), więc helper `_mock_bq_client` z `tests/test_bigquery.py` **nie zadziała**
(daje `MagicMock`, tylko dostęp atrybutowy). Zadziała `_Row(dict)` z
`test_bigquery_broker_operations.py` — „attribute and mapping access both work".

---

### 🥈 C2 — Przypięcie niezmiennika uzgadniającego obie rekonstrukcje (z T1)

| | |
| -- | -- |
| **Obecny kształt** | `get_portfolio_history` (`db/bigquery.py:829`) liczy „ile sztuk" SQL-em wstecz (`:944`), a „po jakim koszcie" Pythonem FIFO wprzód (`:924`). Obie strony **świadome i nośne** |
| **Docelowy kształt** | *Bramka równoważności dwóch rekonstrukcji* — **guard, nie unifikacja** |
| **Kluczowe ustalenie** | Uzgodnienie **było przeprowadzone jako pomiar**: `archive/2026-08-05-fifo-basis-on-read-paths/research.md:41-44` — „Measured over all 508 production operations: **`ledger_open(D) + residual` equals SQL's `shares_on_day(D)` exactly**, on every operation day, for every ticker". Cała architektura **stoi na tej tożsamości** — dzięki niej segmenty mogą nieść cenę za sztukę, a „*Python never has to tell SQL how many shares there were*" |
| **Na czym polega dług** | Tożsamość zmierzono **raz, 2026-08-05, i nigdy nie zakodowano jako asercji**. Zweryfikowane: w `tests/` i `scripts/` nie ma żadnego porównania obu rekonstrukcji — jedyne wystąpienia `shares_on_day` to piny stringa SQL. Istniejące guardy (`_log_unexplained_holdings`, `basis_gaps`) logują na INFO i mierzą **co innego** |
| **Dlaczego nie #1** | Kształt ma **3 dni**. Brak wpadki nie jest dowodem — ale i koszt nie jest jeszcze zmierzony |
| **Dlaczego mimo to #2** | **Ta klasa błędu wystąpiła już raz w tym repo.** `1c3f7c9` (2026-08-04) naprawia tę samą regułę domenową zaimplementowaną dwa razy — SQL dywidend i FIFO zrealizowanych — z komentarzem: „Fixed on both sides, because they had the same defect **independently**". Złapane prewencyjnie, nie przez użytkownika |
| **Blast radius** | **Żaden w kodzie produkcyjnym.** Zmiana dotyka jednego pliku poza obrazem deployu (`scripts/test_bq_portfolio_time_dimension.py` jest w `.dockerignore`) i nie importuje niczego nowego. Promień rażenia jest **operacyjny, nie kodowy**: skrypt tworzy i kasuje własne tabele `_rt_<uuid>` w produkcyjnym datasecie |
| **Koszt długu vs koszt zmiany** | Dług: tożsamość, na której stoi cała architektura historii wartości, zmierzona **raz** i nigdy nie zakodowana. Koszt zmiany: **S** — jedna funkcja `_check_*` dopisana do dziewięciu istniejących, cała maszyneria już jest |
| **Ścieżka inkrementalna** | Dopisać **kontrolę nr 12** do `scripts/test_bq_portfolio_time_dimension.py`: dla tego samego zasianego zestawu zdarzeń porównać `shares_on_day` z CTE z `ledger_open + residual` z `basis_segments`. **Nie tyka żadnej z dwóch implementacji** |
| **Pierwszy prerekwizyt** | Potwierdzenie z człowiekiem, że rozszerzenie skryptu round-trip mieści się w regule „destrukcyjne akcje BigQuery = human-only" (skrypt tworzy i kasuje własne tabele `_rt_<uuid>`) |
| **Co złapie regresję** | **Tylko ten skrypt, uruchamiany ręcznie.** Bramka `Tests` **nie** — strona SQL wykonuje się w BigQuery, a zamockowana porównywałaby mock ze sobą |

**Uwaga o wykonalności, którą trzeba zapisać:** czysty pytest jest tu **strukturalnie
niemożliwy**. Odpowiedź na „test czy żywe dane" brzmi: **ani jedno — zasiane dane, prawdziwy
silnik**. Zapytanie na produkcji odpowiada na inne pytanie („czy rozjazd jest już aktywny")
i jest diagnozą jednorazową, nie siecią na regresję.

---

### 🥉 C3 — Deterministyczny klucz porządkowy na granicy odczytu (z T2)

| | |
| -- | -- |
| **Obecny kształt** | `ORDER BY occurred_at` bez klucza wtórnego (`db/bigquery.py:4153`); `_sort_key` (`portfolio_lots.py:133-142`) rozstrzyga **wyłącznie** remis buy-vs-sell |
| **Docelowy kształt** | *Porządek totalny w kontrakcie odczytu* |
| **Luka w pomiarze** | Zmierzono **0 remisów buy-vs-sell** na 508 wierszach. Przypadku **buy-vs-buy** — jedynego, w którym kolejność SQL jest jedynym arbitrem — **nie zmierzył nikt** |
| **Ścieżka** | `external_id` jest `REQUIRED` (`db/bigquery.py:4064`) i deterministyczny (`{user}:{portfolio}:{broker}:{raw_id}`, `src/api.py:545`) → `ORDER BY occurred_at, external_id` daje porządek totalny **bez nowej kolumny i bez migracji**. Ale: `SELECT` w `list_broker_trades` (`:4147-4148`) **nie zwraca dziś `external_id`**, a `LotEvent` nie ma na nie pola |
| **Blast radius** | **Jedna linijka SQL-a — ale nie zero.** `ORDER BY` jest w `list_broker_trades`, z której czytają **trzy** ścieżki produkcyjne (`src/api.py:1555`, `db/bigquery.py:781`, `:1654`), więc zmiana kolejności dotyka jednocześnie daty nabycia, podstawy kosztowej i zysku zrealizowanego. Dorzucenie `external_id` do `SELECT` wymaga też pola w `LotEvent` — czyli dotyka `src/portfolio_lots.py` |
| **Koszt długu vs koszt zmiany** | Dług: **uśpiony** — remisy istnieją (2 grupy / 701), ale przy identycznych cenach, więc dziś nie zmieniają podstawy kosztowej. Koszt zmiany: **S** dla samego `ORDER BY`, **M** jeśli dokładamy `external_id` do kontraktu odczytu |
| **Pierwszy prerekwizyt** | **Pomiar**, nie zmiana: `GROUP BY portfolio_id, ticker, op_type, occurred_at HAVING COUNT(*) > 1` na produkcji. Jeśli zero — guard w postaci pinu SQL-a wystarczy |
| **Zależność** | Dzieli plik testowy i mock z C1 — robić **po** C1, nie przed |

---

### Rozważeni i odrzuceni

| Kandydat | Dlaczego odrzucony |
| -- | -- |
| **T3a — włączyć `tach` jako bramkę** | **`tach check` przechodzi dziś na zielono** (`EXIT=0`, zweryfikowane 2026-08-08). `tach.toml:42-45` deklaruje `db depends_on src` **hurtowo**, więc krawędź `db → src.portfolio_lots` jest dozwolona z definicji. Bramka weszłaby bezkosztowo i **nie ochroniłaby przed niczym** — a wyglądałaby jak domknięcie problemu. Realna wartość wymaga **granulacji modułów**, czyli decyzji architektonicznej, nie konfiguracji. Zostaje **uzupełnienie komentarza `KNOWN VIOLATION`** o czwartą krawędź — kosmetyka, nie refaktor |
| **T3b — przenieść liście do wspólnego pakietu** | Bezcelowe, dopóki nic nie pilnuje granic. Sama krawędź jest udokumentowaną decyzją: budowa ledgera wewnątrz `db.bigquery` zostawia `tests/e2e/conftest.py:791` nietknięte; przeniesienie do `src/api.py` przepisałoby każdy test historii |
| **T4 — ujednolicić 4 kopie `BasisSegment`** | **Raport wejściowy policzył guard jako dług.** Trzy kopie są wymuszone (system typów BigQuery + udokumentowany bug klienta przy pustej tablicy STRUCT), czwarta **jest osłoną** dodaną na żądanie bramki AI review. Zostaje wąski, realny wiór: dostęp atrybutowy do 5 dataclass — **tani guard przez `dataclasses.fields()`**, stdlib, bez nowej zależności. Nie awansuję go do rankingu, bo bez incydentu i bez śladu decyzji jest to `unknown`, nie dług |
| **T7 — model odpowiedzi dla `/realized`** | **`response_model` nie występuje w `src/api.py` ani razu, przy 32 trasach.** To nie odchył od konwencji — to *jest* konwencja. Punktowa naprawa **zwiększyłaby** niespójność. Dodatkowo FastAPI z `response_model` **cicho odfiltrowuje** klucze spoza modelu, więc niekompletny model wywaliłby front (`static/index.html:5537`, `:5643`) w sposób, którego e2e może nie złapać. Bezpieczny pierwszy krok to **asercja kształtu**, nie model. Koszt: M, nie S |
| **T8 — cache dla preview** | Werdykt „świadome ograniczenie" **potwierdzony mocniej, niż go zapisano**: `deploy.yml:130-132` (`--min-instances=0 --max-instances=4`) czyni cache per-proces niewiarygodnym, a zimne starty gubiłyby token także między requestami tej samej sesji |
| **F1 — walidacja w modelu** | Realne, ale `Field` **nie jest w ogóle importowane** w `src/api.py` — to znów zmiana konwencji, nie naprawa odchyłu. Bez incydentu w historii |
| **P1 — wstrzyknięcie repozytorium** | Największa wartość strukturalna (62 łatki → 1) i przyczyna połowy T5. **Ale to nie jest „bezpieczna zmiana", której wymaga ta lekcja**: dotyka 32 endpointów i 30 plików e2e naraz, a mapa nadal ma to jako otwarte pytanie. Kandydat na osobną kampanię (`/10x-test-plan`), nie na jeden plan |
| **T6, F2** | Zatrzymane na granicy domeny → M4L5. **T6 niesie jednak realne znalezisko**: gwarancja „monotonicznie samo-naprawiające" zaprojektowana dla rekonstrukcji SQL została w domyśle przeniesiona na ledger FIFO, gdzie **nie obowiązuje**. To złożoność przypadkowa nałożona na świadome ograniczenie — i archiwum jej nie odnotowuje |

---

## 3b. Weryfikacja twierdzeń (ast-grep + grep)

Krok wymagany przez M4L4 po zbudowaniu rankingu. Ten dokument stoi na **pięciu twierdzeniach
o zerze**, a zero jest najbardziej zawodnym wynikiem, jaki można dostać — wzorzec, który nie pasuje,
i wzorzec, którego nie ma, wyglądają identycznie. Reguła: **licz `ast-grep` dla precyzji, każde
zero potwierdzaj grepem**. Wykonane 2026-08-10, `ast-grep` 0.42.3.

| # | Twierdzenie (gdzie) | `ast-grep` | `grep` / `git` | Werdykt |
| -- | -- | -- | -- | -- |
| 1 | `response_model=` nie występuje w `src/` **ani razu** (T7, odrzucenie) | `response_model=$A` → **0** | `grep -rc response_model src/` → **0 plików** | ✅ POTWIERDZONE dwoma narzędziami |
| 2 | `tach` nigdy nie był zależnością projektu (T3a) | — | `git log -S"tach" -- pyproject.toml uv.lock` → **0 commitów** | ✅ POTWIERDZONE |
| 3 | `Field` z `pydantic` nie jest w ogóle importowane (F1) | `Field($$$A)` → **0** | `from pydantic import BaseModel, ConfigDict, ValidationError` (`src/api.py:27`) — `Field` **nie ma na liście** | ✅ POTWIERDZONE — import mówi więcej niż licznik |
| 4 | W `tests/` i `scripts/` **nie ma żadnego porównania obu rekonstrukcji** (C2, „na czym polega dług") | — | `shares_on_day` → **7 trafień**, `basis_segments` → 2 pliki; **jeden plik ma oba** (`tests/test_bigquery.py`) | ⚠️ **DOPRECYZOWANE — patrz niżej** |
| 5 | Zero śladów w archiwum dla dostępu atrybutowego do 5 dataclass (T4, werdykt `unknown`) | — | przeszukanie `context/archive/` → **0** | ✅ POTWIERDZONE (zero negatywne — brak dowodu intencji, nie dowód braku intencji) |

**Twierdzenie #4 wymagało poprawki i to jest jedyny realny zysk z tego kroku.** Naiwny odczyt
(„`shares_on_day` w testach = 0") jest **nieprawdziwy** — trafień jest siedem, a `tests/test_bigquery.py`
zawiera **oba** pojęcia naraz, co na pierwszy rzut oka obala tezę. Sprawdzenie każdego trafienia
z osobna pokazuje, że wszystkie siedem to **asercje na tekście wygenerowanego SQL-a**
(`assert "WHERE shares_on_day > 1e-9" in sql`, `tests/test_bigquery.py:1664-1665`, `:1961-1962`,
`:1987-1988`, `:1997`), a nie porównania wartości. Żaden test nie uruchamia obu rekonstrukcji
i nie zestawia wyników. Teza się broni, ale **nie w brzmieniu, w jakim ją zapisałem** — poprawne
jest: „jedyne wystąpienia `shares_on_day` w testach to piny stringa SQL", i tak jest teraz
zapisane w tabeli C2.

**Czego ten krok nie objął.** Twierdzenia o intencjonalności (§2) opierają się na archeologii gita
i archiwum, nie na strukturze kodu — `ast-grep` ich nie weryfikuje i nie udaję, że weryfikuje.
Pozostają na poziomie *evidence z dokumentów*, nie *evidence ze składni*.

## 4. Co ranking zmienił względem intuicji

Warto zapisać, bo to jest cała wartość perspektywy intencjonalności:

- **Mój typ przed tym etapem był błędny.** Obstawiałem T1 na szczycie (najefektowniejsze
  znalezisko) i widziałem T3 jako oczywistą naprawę. Historia odwróciła oba: T1 zeszło na #2,
  bo uzgodnienie **było zrobione** i architektura z niego wynika — brakuje tylko asercji;
  a T3 wypadło z rankingu, bo bramka **nic by nie złapała**.
- **Na szczyt wszedł kandydat, który wyglądał na „tylko brakujące testy".** T5 jest jedynym
  z udokumentowaną historią kosztów — dwie awarie produkcyjne tej samej klasy, obie opisane
  w `lessons.md`, obie złapane mechanizmem, którego wciąż nie ma w CI.
- **Jeden problem okazał się już naprawiony.** Czwarta kopia `BasisSegment`, którą policzyłem
  jako dług, jest osłoną wymuszoną przez bramkę AI review na PR #250.

---

## Unknowns

- ~~**Czy istnieją pary *zakupów* o identycznym `occurred_at`**~~ — dokładny warunek aktywacji C3.
  **ZMIERZONE 2026-08-10 na produkcji:** 2 grupy / 4 wiersze z 701 transakcji mają identyczny
  znacznik czasu, ale **w żadnej z nich ceny się nie różnią** (`COUNT(DISTINCT unit_price) = 1`),
  więc kolejność FIFO nie wpływa dziś na podstawę kosztową. Warunek aktywacji jest spełniony
  częściowo: kolizja jest, szkody nie ma. To **obniża pilność C3, nie unieważnia go** — różnica
  cen w kolidującej parze jest kwestią danych, nie kodu. Szczegóły → „Pomiary produkcyjne"
  w `portfolio-lots-flow/research.md`.
- **Czy tożsamość `ledger_open(D) + residual = shares_on_day(D)` nadal trzyma.** Zmierzona raz,
  2026-08-05, na 508 operacjach; nigdy nie zakodowana.
- **Czy `scripts/test_bq_portfolio_time_dimension.py` przechodzi dziś** — nie uruchamiano,
  wymaga poświadczeń GCP i tworzy tabele (to zapis, poza granicą eksploracji).
- ~~**Czy `ai-code-review/verdict` przepuszcza PR czysto testowy.**~~ **ZMIERZONE 2026-08-10
  na przypadku ostrzejszym niż pytanie.** PR #260 (`docs/test-plan-drift`) to diff **wyłącznie
  markdownowy** — ani jednej linii kodu, ani jednego nowego testu. Wynik: `pass`, **min score 10**,
  czyli **10/10 we wszystkich sześciu kryteriach**, w tym `testCoverageVsRisk`. Scorer nie ukarał
  braku testów w PR, który z natury ich nie ma, i nie zdegenerował się na diffie bez kodu.
  Skoro przechodzi diff bez kodu, to PR czysto testowy tym bardziej — **zmienna kosztu znika
  z rankingu dla wszystkich trzech kandydatów**, a faza 4 planu (wpis do `lessons.md`) jest
  potwierdzenie tania. Zastrzeżenie: to jeden pomiar, nie rozkład — mówi, że bramka *potrafi*
  przepuścić taki PR, nie że przepuści każdy.
- **Koszt P1** — nie policzono, ilu z 32 endpointów i 30 plików e2e dotknęłoby wstrzyknięcie
  repozytorium.
- **Dlaczego 5 dataclass czytanych jest atrybutowo** — zero śladów w archiwum.
- **`context/foundation/test-plan.md` jest nieaktualny** (status `draft`, 2026-06-08, deklaruje
  „No integration or end-to-end tests exist yet", co dziś nieprawda). Nie zaliczam do długu
  kodu, ale wpływa na wiarygodność strategii testowej jako źródła.
