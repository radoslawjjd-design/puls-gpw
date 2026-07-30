<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Import pozycji i dywidend z eksportu XTB

- **Plan**: `context/changes/xtb-portfolio-import/plan.md`
- **Scope**: Phases 1–8 (całość)
- **Date**: 2026-07-30
- **Verdict**: NEEDS ATTENTION
- **Findings**: 1 critical, 3 warnings, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Grounding

Diff zakresu: `75a8240^..HEAD` (PR #205, #206, #207), 30 plików kodu poza `context/`.
Pakiet testów: **934 zielone** (przed poprawką F1: 930). Wszystkie kryteria automatyczne
faz 1–8 przechodzą. Cztery pozycje manualne pozostają otwarte i są opisane w `## Progress`
z powodem.

Zmiany w `post_main.py` i `src/post_generator.py` w tym samym zakresie diffu pochodzą
z PR #208 (naprawa X-posta, PUL-101) i nie należą do tego planu — zweryfikowane, nie
liczone jako dryf.

## Findings

### F1 — Gotówka nigdy nie spada do zera

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — poprawka oczywista i wąska
- **Dimension**: Safety & Quality (data safety)
- **Location**: `src/api.py:447`
- **Detail**: `_cash_position` zwracało `None` zarówno dla salda **nieznanego**, jak
  i **zerowego**. Merge pozycji świadomie nie ma gałęzi „usuń czego nie ma w źródle" —
  to właśnie ta właściwość chroni pozycję S2B, której eksport nie widzi. Ta sama
  właściwość oznacza, że pominięty wiersz `_CASH` nie jest czyszczony, tylko
  **zostawiany**. Skutek: po wydaniu całej gotówki i ponownym imporcie stary wiersz
  zostaje, a wartość portfela, treemapa, kalendarz i wykres pokazują pieniądze, których
  nie ma. Plan formułuje regułę, którą kod łamał: „saldo nieujawnione w pliku (`None`)
  i saldo zerowe to dwa różne fakty i nie mogą renderować się tak samo" — zasada została
  zastosowana do renderowania, a przeoczona na ścieżce zapisu.
- **Fix**: pomijać wiersz wyłącznie przy `None`; saldo podane emitować zawsze, ujemne
  przycinając do zera (jako pozycja czytałoby się jak krótka sprzedaż).
- **Decision**: FIXED — `src/api.py:_cash_position`, 4 testy jednostkowe
  w `tests/test_api.py`; break-verified (przywrócenie starego warunku wywala 2 z nich).

### F2 — Brak sufitu na rozmiar po dekompresji

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — realny kompromis, wymaga zastanowienia
- **Dimension**: Safety & Quality (dostępność)
- **Location**: `src/brokers/xlsx_reader.py:39,52`
- **Detail**: `read_sheets` materializuje **każdy** arkusz w całości
  (`list(sheet.iter_rows(values_only=True))`), a jedyny limit — `_IMPORT_MAX_BYTES`
  = 5 MB — mierzy bajty **skompresowane**, i to dopiero po `await file.read()`.
  `.xlsx` to archiwum zip, więc kilka megabajtów powtarzalnych komórek rozwija się
  do wielokrotności tego w pamięci i może położyć instancję Cloud Run dla wszystkich
  użytkowników, nie tylko dla wgrywającego. Endpoint wymaga uwierzytelnienia, więc nie
  jest to wektor anonimowy — stąd WARNING, nie CRITICAL.
- **Fix**: twardy sufit na liczbę wierszy/komórek egzekwowany **w trakcie** iteracji
  (przerwanie po przekroczeniu), zamiast kontroli rozmiaru wejścia po fakcie.
- **Decision**: DEFERRED — osobne issue.

### F3 — Import bez transakcji zostawia stan częściowy

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality (niezawodność)
- **Location**: `src/api.py:1310–1314`
- **Detail**: commit wykonuje trzy niezależne zapisy do BigQuery
  (`merge_user_broker_operations`, `merge_user_portfolio_positions_bulk`,
  `delete_user_portfolio_positions`) bez transakcji. Awaria drugiego lub trzeciego
  zwraca 500, ale wcześniejsze zapisy już weszły: operacje i dywidendy są widoczne,
  podczas gdy pozycje pozostają nieaktualne. Każdy krok jest idempotentny, więc
  ponowienie zbiega się poprawnie — problem w tym, że **nikt użytkownikowi nie mówi,
  że ma ponowić**.
- **Fix**: odróżnić „import częściowy" od pełnej awarii w komunikacie i logu, albo
  objąć sekwencję `BEGIN TRANSACTION … COMMIT`.
- **Decision**: DEFERRED — osobne issue.

### F4 — Test kontroli krzyżowej z fazy 1 nie istnieje

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: `tests/test_brokers_xtb.py` (brak)
- **Detail**: kryterium fazy 1 wymagało testu porównującego sumę sprzedaży
  z `Cash Operations` z wolumenem z arkusza `Closed Positions` per ticker. Fraza
  `Closed Positions` nie występuje nigdzie w `src/` ani `tests/`. Walidacja została
  wykonana ręcznie na realnych plikach i opisana w `change.md`, ale nigdy nie
  zautomatyzowana — mimo to wiersz Progress był odhaczony.
- **Fix**: dopisać test na syntetycznym zestawie z oboma arkuszami (realnych plików
  nie wolno commitować — zawierają dane rachunku).
- **Decision**: DEFERRED — osobne issue.

### F5 — `external_id` ma inny format niż dokumentuje plan

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: `src/api.py:251`
- **Detail**: plan dokumentuje `"{broker}:{ID}"`, kod buduje
  `"{user_id}:{portfolio_id}:{broker}:{raw_id}"`. Kod jest **lepszy** — samo id brokera
  jest unikalne tylko w obrębie rachunku, więc bez przestrzeni nazw import jednego
  użytkownika mógłby po cichu połknąć operacje innego. Rozjeżdża się dokumentacja,
  nie zachowanie.
- **Fix**: poprawić tabelę kontraktu w planie (dokument archiwalny — do odnotowania
  przy ewentualnym kolejnym brokerze).
- **Decision**: ACCEPTED — kod zostaje, rozbieżność odnotowana tutaj.

### F6 — Sekcja „What We're NOT Doing" nie została zaktualizowana

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Scope Discipline
- **Location**: `plan.md` § What We're NOT Doing
- **Detail**: sekcja nadal deklaruje, że zrealizowany P/L nie powstaje, podczas gdy
  faza 8 zbudowała pełną funkcję. Faza 8 opisuje ten zwrot we własnym Overview
  (rozszerzenie na prośbę użytkownika), ale lista wykluczeń nigdy nie została poprawiona,
  więc plan przeczy sam sobie.
- **Decision**: ACCEPTED — faza 8 nosi uzasadnienie; sprzeczność odnotowana.

### F7 — Import z okna „Dodaj portfel" nie jest opisany żadną fazą

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Scope Discipline
- **Location**: `static/index.html` (`#pp-new-file`, `_importIntoNewWallet`),
  `tests/e2e/test_portfolio_import.py:219-293`
- **Detail**: drugie wejście do importu — plik dołączany przy tworzeniu portfela, jeden
  round trip — stanowi całość PR #207 i ma własne testy e2e, ale nie odpowiada mu żaden
  blok „Changes Required".
- **Decision**: ACCEPTED — funkcja przetestowana i wdrożona; brak opisu odnotowany.

### F8 — Nowe endpointy zwracają 404 tam, gdzie bliźniacze zwracają 403

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: `src/api.py:1352` wobec `src/api.py:1218,1251`
- **Detail**: `/api/portfolio/dividends` i `/api/portfolio/realized` odpowiadają 404
  „Wallet not found" na cudzy `portfolio_id`, zgodnie z rodziną positions/wallets.
  Ich najbliższe strukturalnie rodzeństwo — `/calendar` i `/history`, dzielące ten sam
  kształt `all_mode` + kontrola własności — odpowiada 403. Zachowanie poprawne, kontrakt
  niespójny między dwiema niemal identycznymi rodzinami.
- **Decision**: ACCEPTED — do ujednolicenia przy najbliższej pracy nad tymi endpointami.

Dodatkowo odnotowane bez osobnego findingu: ticker `_CASH.PL` w pliku znormalizowałby się
do sentinela `_CASH` i wmieszał w wiersz gotówki (wymaga ręcznie spreparowanego eksportu,
szkodzi wyłącznie własnym danym); zmiana `GOOGLE_CLOUD_REGION` na `global` w dwóch
workflow CI wjechała w tym samym zakresie diffu, niezwiązana z planem (naprawa 429
na regionalnym endpoincie Vertex).
