<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Import pozycji i dywidend z eksportu XTB

- **Plan**: `context/changes/xtb-portfolio-import/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-29
- **Verdict**: REVISE → SOUND (po triage'u — wszystkie 6 findingów naprawione)
- **Findings**: 2 critical, 3 warnings, 1 observation

## Verdicts

| Dimension | Verdict (przed) | Po fixach |
|-----------|-----------------|-----------|
| End-State Alignment | WARNING | PASS |
| Lean Execution | PASS | PASS |
| Architectural Fitness | PASS | PASS |
| Blind Spots | WARNING | PASS |
| Plan Completeness | FAIL | PASS |

## Grounding

7/7 ścieżek ✓, 8/8 symboli ✓, brief↔plan ✓, Progress↔Phase 6/6 faz ✓, 44/44 kryteriów ✓

## Co obroniło się pod weryfikacją

Najdroższe założenia planu zostały sprawdzone w kodzie i **potwierdzone**:

- **Sparametryzowanie `_merge_insert_only` jest tanie.** Klucz `(ticker, snapshot_date)`
  i `ORDER BY fetched_at` to jedyne zaszyte identyfikatory (`db/bigquery.py:2836-2846`).
  Źródło ładowane jest przez tabelę tymczasową, a schemat przychodzi argumentem — helper
  nie zawiera ani jednej nazwy specyficznej dla `company_daily_stats`. Obaj wołający
  wołają pozycyjnie pięcioma argumentami, więc parametry z domyślnymi wartościami ich
  nie ruszają.
- **Lista napraw cache jest kompletna.** Przejrzano wszystkie 8 prefiksów zapisywanych
  do `_PERF_CACHE`. Poza domeną watchlisty (obsługiwaną przez `_invalidate_wl_sentiment`)
  i `admin:treemap` (inne źródło danych — `get_latest_snapshot_for_wallet`, nie pozycje
  użytkownika) nic nie zostaje nieunieważnione.
- **Wzorzec patchowania e2e opisany poprawnie.** Patche idą na `src.api.*` (miejsce
  importu). Potwierdzone: `ExitStack` wchodzi przed `create_app()`, więc każda
  niezapatchowana funkcja DDL w `_init_dimension_tables` uderza w żywe BigQuery.

## Findings

### F1 — Zakładka Dywidendy nie pobierze danych

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 5 → „Zakładka i panel"
- **Detail**: Plan opisywał handler trybów jako „lista ukrywania paneli (`:3846-3849`) to
  cztery jawne miejsca" i wymieniał do zmiany tylko ten blok oraz `_selectPortfolioTab()`.
  W rzeczywistości to jeden blok ukrywający cztery elementy, a bezpośrednio pod nim leży
  drugi łańcuch `if/else` (`static/index.html:3850-3859`) sterujący pobieraniem danych,
  którego plan nie wymieniał. Bez gałęzi `dividends` kliknięcie zakładki pokazuje **pusty
  panel** — nikt nie woła fetcha. Dodatkowo `stopPortfolioTreemapResize()` nie zostaje
  zawołane, więc przejście treemapa → dywidendy zostawia działający observer resize.
- **Fix**: Rozpisano oba bloki jako dwa osobne miejsca zmiany; dodano wymóg gałęzi
  `else if (mode === 'dividends')` wołającej `stopPortfolioTreemapResize()` oraz
  `if (!_ppDivData) fetchPortfolioDividends()`. Dopisano też, że przywrócenie trybu z URL
  (`:3578-3580`) działa bez zmian, i że przyciski roku nie mogą trafić do `#pp-view-tabs`,
  bo handler jest do tego kontenera zawężony przed kolizją z przełącznikami zakresu
  i metryki współdzielącymi klasę `.pp-view-tab`.
- **Decision**: FIXED

### F2 — Podgląd ogłosi usunięcie ~24 tickerów, których nie ma w portfelu

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: End-State Alignment
- **Location**: Phase 1 (`closed_tickers`) ↔ Phase 3 (sekcja `closed`) ↔ Phase 6 krok 2
- **Detail**: Parser zwraca w `closed_tickers` każdy ticker, który kiedykolwiek zszedł
  do zera — w realnych plikach jest ich 24 (Current State Analysis). Plan nigdzie nie
  przecinał tego zbioru ze stanem portfela, a podgląd renderował go jako „zostaną
  usunięte". To wprost przeczyło kryterium 6.3 („Podgląd IKZE… zero do usunięcia").
  Sam `DELETE` byłby nieszkodliwym no-opem, ale fałszywe ostrzeżenie o operacji
  destrukcyjnej jest racjonalnym powodem, żeby przerwać import.
- **Fix**: Doprecyzowano w Phase 3, że sekcja `closed` i argument
  `delete_user_portfolio_positions` to `closed_tickers ∩ tickery obecne dziś w portfelu`,
  liczone z tej samej listy pozycji, która zasila `untouched`. Parser pozostaje czysty
  i zwraca komplet; przecięcie robi warstwa API, bo tylko ona zna stan portfela. Dodano
  wymóg, żeby podgląd i commit liczyły je identycznie.
- **Decision**: FIXED

### F3 — Nierozpoznany ticker: zachowanie commitu niezdefiniowane

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 3 → „Endpoint podglądu" / „Endpoint commitu"
- **Detail**: Plan określał, co podgląd robi z nierozpoznanym tickerem (`unknown_tickers`,
  nie 422), ale milczał o commicie. To nie detal implementacyjny: `src/api.py:833-834`
  odrzuca nieznany ticker przez 422 przy ręcznym dodawaniu pozycji, więc import bez
  rozstrzygnięcia albo po cichu łamie tę politykę, albo po cichu gubi pozycję. Zapisana
  pozycja z tickerem spoza `companies`/`etf_instruments` nie ma kursu i wchodzi w wartość
  portfela jako dziura.
- **Fix A ⭐ Recommended** (wybrany): Zapisuj, oznacz w podglądzie jako „bez wyceny"
  - Strength: Nie gubi realnej pozycji — plik brokera jest źródłem prawdy o stanie
    rachunku, a `companies` bywa niekompletne. Spójne z filozofią „nie kasujemy S2B".
  - Tradeoff: Pozycja bez kursu wchodzi do sum; wymaga jawnej etykiety w podglądzie.
  - Confidence: MEDIUM — nie zweryfikowano zachowania treemapy i kalendarza dla pozycji
    bez notowań.
  - Blind spot: Zachowanie wykresu wartości dla takiej pozycji.
- **Fix B**: Pomiń przy zapisie, pokaż jako pominięte
  - Strength: Zachowuje niezmiennik dzisiejszego 422 — każda pozycja w bazie jest wyceniana.
  - Tradeoff: Import cicho zawęża portfel o pozycję, której nie da się dodać ręcznie.
  - Confidence: HIGH — zgodne z istniejącą polityką w kodzie.
  - Blind spot: Brak.
- **Decision**: FIXED via Fix A. Uzasadnienie odstępstwa od 422 zapisane wprost w planie:
  przy ręcznym wpisie nieznany ticker to najpewniej literówka, przy imporcie to stan
  faktyczny rachunku. Kryterium 3.4 rozszerzone o zapis przez commit.

### F4 — Budżet 60 s policzony na „trzech zapytaniach", a MERGE to load job

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Performance Considerations / Phase 2 pkt 4
- **Detail**: `_merge_insert_only` (`db/bigquery.py:2819-2830`) nie jest jednym zapytaniem
  — tworzy tabelę tymczasową, ładuje wiersze osobnym jobem `load_table_from_json`, dopiero
  potem wykonuje MERGE. Realny commit to ~5 operacji BigQuery, nie 3. Istotniejsze: ten
  prymityw **nigdy nie działał wewnątrz żądania HTTP** — obaj dzisiejsi wołający
  (`scripts/backfill_historical_closes.py`, job company-stats) to zadania wsadowe, gdzie
  limit 60 s nie obowiązuje. Plan przedstawiał to jako policzone, a nie jako założenie.
- **Fix**: Skorygowano opis na „trzy kroki logiczne / ~5 operacji BQ" z wyjaśnieniem.
  Dodano piątą asercję do skryptu round-trip fazy 2: pomiar czasu ściany dla partii
  571 wierszy z **progiem alarmowym 15 s** (jedna trzecia budżetu, z zapasem na dwa
  pozostałe kroki commitu i zimny start). Dodano kryterium 2.5 w Progress.
  - Strength: Skrypt round-trip i tak powstaje w Phase 2 — pomiar to jedna linia,
    a odpowiedź przychodzi zanim endpoint commitu w ogóle powstanie.
  - Tradeoff: Brak istotnego.
  - Confidence: HIGH — kod prymitywu przeczytany bezpośrednio.
  - Blind spot: Nieznany typowy czas load joba w tym projekcie.
- **Decision**: FIXED

### F5 — Kryterium 1.4 (`tach check`) nie może wykryć tego, co deklaruje

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 → Success Criteria / Progress 1.4
- **Detail**: Kryterium brzmiało „Parser nie importuje warstwy danych: `uv run tach check`".
  `tach.toml:29-33` deklaruje moduł `src` z `depends_on = [{ path = "db" }]`, czyli import
  `db.bigquery` z `src/` jest **jawnie dozwolony**. `src.brokers` należy do modułu `src`
  (najbliższy zadeklarowany przodek), więc `tach check` przechodzi na zielono niezależnie
  od tego, czy parser dotyka BigQuery. Kryterium nie mogło zawieść.
- **Fix**: Zastąpione asercją w `tests/test_brokers_xtb.py` — moduł `src.brokers.xtb`
  nie ma w importach niczego z `db` ani `fastapi` (AST na pliku albo inspekcja
  `sys.modules` po świeżym imporcie). `tach check` zostaje w pakiecie jako ogólny
  strażnik architektury, ale nie jako dowód czystości parsera. Progress 1.4 przepisane.
- **Decision**: FIXED

### F6 — Dwa ograniczenia implementacyjne nienazwane w planie

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 pkt 3 / Phase 1 pkt 1
- **Detail**:
  (a) `tests/test_bigquery_insert_only_merge.py:65` asercjonuje dosłowny napis
  `"PARTITION BY ticker, snapshot_date"` — sparametryzowany builder musi odtworzyć
  dokładnie to formatowanie (przecinek + spacja), inaczej istniejący test padnie mimo
  poprawnego zachowania. Ten sam plik liczy wystąpienia podciągów `source` i `kurs_odn`
  w całym SQL.
  (b) Dockerfile robi `uv sync --frozen --no-dev`, więc `uv.lock` musi wejść tym samym
  commitem co `pyproject.toml` — `--frozen` wywala build przy rozjeździe.
- **Fix**: Oba dopisane jako Implementation Note w odpowiednich fazach.
- **Decision**: FIXED

## Wzorzec do zapamiętania

F1 i F2 to **ta sama klasa błędu**: plan opisywał sąsiedztwo kodu z drugiej ręki (przez
`research.md`) zamiast z pierwszej, i w obu przypadkach ominął to, co leży bezpośrednio
pod cytowanym zakresem linii. Przy fazach frontowych warto czytać handler w całości,
nie po zakresach.

Rzecz **nie** zgłoszona jako finding: komentarz w kodzie (`static/index.html:3838`) mówi
„Tabela | Treemapa | Kalendarz | **Wartość**", sugerując czwarty tryb. Nie ma go —
„Wartość" to przycisk metryki wewnątrz kalendarza (`data-metric="value"`, `:3782`).
Plan słusznie tego nie skopiował.
