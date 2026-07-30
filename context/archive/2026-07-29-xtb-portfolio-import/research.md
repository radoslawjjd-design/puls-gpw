---
date: 2026-07-29T11:43:26+02:00
researcher: Radek
git_commit: 519956de2388365489d52fc3445ad1cbe0190e5a
branch: pul-95-xtb-portfolio-import
repository: radoslawjjd-design/puls-gpw
topic: "Import pozycji i dywidend z eksportu XTB — rozpoznanie kodu przed planowaniem"
tags: [research, codebase, portfolio, import, xlsx, dividends, bigquery, spa]
status: complete
last_updated: 2026-07-29
last_updated_by: Radek
---

# Research: Import pozycji i dywidend z eksportu XTB

**Date**: 2026-07-29T11:43:26+02:00
**Researcher**: Radek
**Git Commit**: `519956de2388365489d52fc3445ad1cbe0190e5a`
**Branch**: `pul-95-xtb-portfolio-import`
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

Co w istniejącym kodzie trzeba znać, żeby zaplanować: (a) upload pliku eksportu XTB,
(b) rekonstrukcję pozycji metodą FIFO z historii transakcji, (c) import z podglądem
i potwierdzeniem, (d) nowy widok dywidend z magazynem danych i wyborem roku.

Analiza samych plików eksportu została wykonana wcześniej i jest zapisana w `change.md` —
ten dokument dotyczy strony kodu.

## Summary

Cztery równoległe rozpoznania (SPA, warstwa BigQuery, warstwa API, testy + historia zmian)
dały spójny obraz: **funkcja jest w dużej mierze greenfield na brzegach, a bardzo dobrze
oprzyrządowana w środku.**

Greenfield i wymagające decyzji:

1. **Upload pliku nie istnieje nigdzie** — ani we froncie (`<input type="file">`, `FormData`,
   `FileReader` — zero wystąpień), ani w backendzie (`UploadFile`, `File(...)`, `multipart` —
   zero wystąpień). `python-multipart` **nie jest zainstalowany**, a `fastapi` jest przypięty
   bez ekstras. To nie jest błąd 500 na żądaniu: FastAPI podnosi `RuntimeError` przy
   **definicji trasy**, czyli wywala `create_app()` przy starcie. Zależność musi wejść
   tym samym commitem co endpoint.
2. **Brak czytnika xlsx** — nie ma `openpyxl`, `pandas`, `xlrd` ani `calamine`.
3. **Brak jakiegokolwiek magazynu dywidend** — `db/bigquery.py` nie zawiera ani jednego
   identyfikatora związanego z dywidendami.
4. **Nie ma nigdzie FIFO ani dopasowywania lotów.** Koszt nabycia to jeden skalar
   `avg_buy_price` w jednym wierszu na `(portfolio_id, ticker)`.

Dobrze oprzyrządowane i do skopiowania:

- Wzorzec cyklu życia tabeli BQ jest mechaniczny i powtarzalny (`notification_sent_log`
  to najczystszy, najświeższy przykład).
- Istnieje **MERGE insert-only z `QUALIFY`** zbudowany dokładnie pod idempotentny import
  danych zewnętrznych (PUL-92) — to gotowy prymityw pod dywidendy.
- Istnieje modal z dropdownem, polem warunkowym i błędem inline (`#pp-add-portfolio-modal`),
  czyli niemal dokładnie ten kształt, którego potrzebuje okno importu.
- Istnieje wzorzec koperty odpowiedzi `{series, notes, excluded}` (PUL-100), uzasadniony
  dokładnie tym samym argumentem, którym uzasadniamy sekcję „są w portfelu, nie ma w pliku".
- Istnieje wzorzec czystego modułu parsującego w `src/` z testami jednostkowymi bez BQ i HTTP.

Do rozstrzygnięcia w planie są trzy rzeczy o charakterze architektonicznym, opisane
w sekcji **Open Questions** — z czego jedna (czy zapisujemy daty transakcji) sięga
znacznie dalej niż ten ticket.

## Detailed Findings

### 1. Upload pliku — decyzja transportowa

Stan zastany:

- `src/api.py:12` importuje `Depends, FastAPI, HTTPException, Query, Request, Response, Security`
  — bez `File`, `UploadFile`, `Form`.
- `python-multipart` nie występuje w `pyproject.toml`, w `uv.lock` ani w `.venv`.
- `fastapi>=0.136.1` jest przypięty jako goły pakiet, **nie** `fastapi[standard]`, więc
  multipart nie wchodzi tranzytywnie.
- Front: `_downloadCsv` (`static/index.html:4488-4496`) to jedyny ruch plikowy w aplikacji —
  wyłącznie wychodzący.

Trzy możliwe drogi, w kolejności rekomendacji:

| Droga | Koszt | Ryzyko |
|---|---|---|
| **A. `UploadFile` + `python-multipart`** | 1 nowa zależność runtime | Musi wejść jednym commitem z endpointem, inaczej deploy kładzie cały serwis |
| **B. base64 w JSON** | 0 nowych zależności transportowych | Narzut +33% na rozmiarze; niestandardowe, ale zgodne z każdym istniejącym call-site (`Content-Type: application/json` + `X-API-Key`) |
| **C. parsowanie po stronie klienta** | — | **Odpada.** xlsx to ZIP z XML; SPA nie ma bundlera ani zewnętrznych bibliotek, a rozpakowanie i parsowanie XML ręcznie w JS to realny dług |

Rekomendacja: **A**, z jawnym zapisem w planie, że `uv add python-multipart` i endpoint
lądują w jednym commicie. Droga B jest sensownym awaryjnym wyjściem, jeśli dodanie
zależności okaże się problemem — pliki mają 15 i 41 KB, więc narzut base64 jest bez znaczenia.

Niezależnie od drogi: **nie ma żadnego limitu rozmiaru ciała żądania.** Jedyny middleware
to `_add_process_time_header` (`src/api.py:401-407`). Twardy limit Cloud Run to 32 MiB,
więc własny guard (np. odrzucaj > 5 MB przed parsowaniem) trzeba dodać samodzielnie.

### 2. Czytnik xlsx i filozofia zależności

`pyproject.toml` ma 16 bezpośrednich zależności runtime, wszystkie wąskie. Projekt
konsekwentnie pisze małe parsery ręcznie — `src/polish_numbers.py` to 34-liniowy moduł
z dwoma parserami liczb, wydzielony po to, żeby „parser nie musiał sięgać do wnętrzności
źródła zapasowego".

`openpyxl` (czysty Python, jedna zależność tranzytywna `et-xmlfile`, ~250 KB) to najmniejszy
realny czytnik i jedyny pasujący do tego profilu. Uwaga wykonawcza: przy 512 MiB pamięci
na Cloud Run ładuj przez `load_workbook(..., read_only=True, data_only=True)` — tryb domyślny
materializuje cały skoroszyt.

Wymóg twardy: **zależność runtime nie może trafić do grupy dev.** Docker robi
`uv sync --frozen --no-dev` (`Dockerfile:9`), więc parser przeszedłby CI lokalnie
i wywalił się `ImportError` na produkcji.

### 3. Kształt warstwy serwisowej — gdzie mieszka parser

Logika biznesowa jest konsekwentnie oddzielona od `src/api.py`. Wzorzec do skopiowania:

- `src/portfolio_treemap.py:4-65` — `compute_user_portfolio_treemap_positions(rows)`,
  z docstringiem *„Pure function — no BQ/network access"*, wołana z `src/api.py:958`
- `src/portfolio_calendar.py` — `compute_calendar_pnl(rows, year, month)`, wołana z `src/api.py:1010`

Każdy ma test jednostkowy 1:1 (`tests/test_portfolio_treemap.py`, `tests/test_portfolio_calendar.py`) —
czyste funkcje, bez BQ, bez HTTP. Tak samo należy testować silnik FIFO.

`src/` jest **płaskie** — 25 plików `.py`, zero podpakietów. `src/brokers/xtb.py` byłoby
pierwszym zagnieżdżeniem. Zgodne z dzisiejszym układem byłoby płaskie `src/xtb_import.py`;
podpakiet `src/brokers/` jest uzasadniony, jeśli dropdown faktycznie zapowiada kolejnych brokerów.

Import zawsze absolutny z prefiksem pakietu (`from src.portfolio_treemap import ...`,
`from db.bigquery import ...`) — **zero importów względnych w `src/`**.

`tach.toml` jest realny i egzekwowany przez review (nie przez CI): `src` zależy tylko od `db`.
**Czysty parser nie może importować `db.*`** — powinien zostać na stdlib + `openpyxl`.
Podział zgodny z precedensem treemapy: `parse_xtb_export(bytes) -> {positions, dividends, warnings}`
bez FastAPI i bez BigQuery, żeby dał się testować na próbkach i wołać z CLI.

### 4. Warstwa BigQuery — nowa tabela dywidend

**Cykl życia tabeli** jest mechaniczny. Najczystszy świeży wzorzec to `notification_sent_log`
(`db/bigquery.py:3174-3199`): stała `_X_TABLE_NAME`, lista `_X_SCHEMA`, funkcja
`create_x_table_if_not_exists()` łapiąca `NotFound`, oraz `ensure_x_schema_current()`
delegująca do generycznego `ensure_schema_current` (`db/bigquery.py:149-181`).

**Tabele tworzone są przez entry pointy, nie centralnie.** Tabela czytana i zapisywana
przez API musi trafić do hooka startowego `_init_dimension_tables` w `src/api.py:409-422`,
zawsze parą `create_*` **potem** `ensure_*` — `ensure_schema_current` na nieistniejącej
tabeli po cichu wychodzi (`db/bigquery.py:165-167`).

**Idempotencja — istnieje gotowy prymityw.** `_merge_insert_only` (`db/bigquery.py:2801-2862`)
powstał w PUL-92 dokładnie pod import danych zewnętrznych:

```sql
MERGE `{target}` T
USING (
  SELECT * FROM `{tmp}`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY fetched_at DESC) = 1
) S
ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
WHEN NOT MATCHED THEN INSERT (...) VALUES (...)
```

Dwie własności są kluczowe: brak gałęzi `WHEN MATCHED` czyni ponowny import strukturalnie
bezpiecznym, a `QUALIFY` deduplikuje **samą partię źródłową** (bez tego `WHEN NOT MATCHED`
odpala się per wiersz źródłowy i duplikat w pliku wchodzi dwa razy). Funkcja zwraca
`num_dml_affected_rows`, czyli gotowe „N nowych / M już było" do podglądu.

Zastrzeżenie: `_merge_insert_only` ma **zaszyty klucz** `(ticker, snapshot_date)`
(`:2840`, `:2842`) mimo generyczności po kolumnach. Pod dywidendy trzeba go sparametryzować
albo skopiować.

**Klucz deduplikacji — korekta wobec rekomendacji z rozpoznania.** Agent badający BQ zalecił
hasz treści, zakładając, że „eksport nie niesie ID operacji brokera po stronie brokera".
To założenie jest nieprawdziwe i sprawdziłem to na danych: kolumna `ID` w `Cash Operations`
istnieje, jest wypełniona w **571 na 571** operacji, unikalna w obrębie pliku i **bez kolizji
między plikami**. To naturalny klucz zewnętrzny — `external_id = f"xtb:{ID}"`.

Hasz treści działa jako wariant zapasowy dla brokerów bez ID, ale ma pułapkę: trzy wypłaty
PAS w IKZE mają identyczny znacznik czasu **z dokładnością do sekundy** i różnią się dopiero
mikrosekundami. Hasz liczony po timestampie obciętym do sekund skleiłby je w jeden wiersz.

**Precedens agregacji per rok nie istnieje** — w całym `db/bigquery.py` nie ma ani jednego
`EXTRACT(YEAR ...)`, `DATE_TRUNC` ani `FORMAT_DATE`. Najbliższe wzorce:
- `summarize_watchlist_sentiment` (`db/bigquery.py:1970-2029`) — jeden wiersz z wieloma
  agregatami plus bezpieczne wartości domyślne przy pustym wyniku; to kształt kafelka „razem"
- `get_portfolio_history` CTE `daily` (`db/bigquery.py:613-622`) — `SUM(IF(...))` i `COUNTIF(...)`
  jako wzorce sum warunkowych

**Pułapka z PUL-100 wprost dotyczy dropdownu lat**: jeśli lista dostępnych lat wraca tym samym
zapytaniem co agregat, join musi być pisany **meta-first** (`FROM meta LEFT JOIN dane ON ...`),
inaczej metadane znikają, gdy strona agregatu jest pusta (`db/bigquery.py:623-626`, `:640-650`).

**Migracja schematu jest wyłącznie addytywna.** `ensure_schema_current` porównuje po nazwach
i dokleja brakujące kolumny na końcu; nigdy nie usuwa, nie zmienia trybu ani typu. Kolumny
dodane po utworzeniu tabeli **muszą być `NULLABLE`** (`db/bigquery.py:2394-2395`).
Partycjonowanie i klastrowanie da się ustawić **wyłącznie** przy tworzeniu.

Wniosek dla schematu dywidend: `REQUIRED` tylko dla pól, które parser zawsze wyprodukuje
dla każdego brokera (`user_id`, `portfolio_id`, `ticker`, `paid_at`, `external_id`, `imported_at`).
`gross_amount`, `withholding_tax`, `source` — `NULLABLE`. Uzasadnienie merytoryczne jest
w danych: **IKZE nie ma ani jednego wiersza podatku**, a przyszły broker może w ogóle nie
rozdzielać brutto od netto.

### 5. Warstwa API — konwencje i dwie istniejące dziury w cache

**Konwencje endpointów** (`src/api.py`): fabryka `create_app()` (`:395-1067`), wszystko jako
domknięcia w środku; `app.mount("/static", ...)` **musi zostać ostatni** (`:1065`).
Endpointy per-user deklarują **oba** dependency — `role: Role = Depends(_get_role)`
i `user_id: str = Depends(_get_user_id)` — nawet gdy `role` jest nieużywane.

Własność portfela sprawdzana jest inline w każdym miejscu, bez helpera. **Kod statusu jest
niespójny w istniejącym kodzie** — `404 "Wallet not found"` na pozycjach (`:790`, `:829`, `:856`),
ale `403` na kalendarzu i historii (`:1003`, `:1036`). Dla importu działającego na pozycjach
precedensem jest **404**.

Sentynel `_ALL_PORTFOLIOS = "all"` (`src/api.py:345`) pomija sprawdzenie własności i przekazuje
`None` w dół. **Ścieżka zapisu musi go jawnie odrzucić** — dziś żaden zapis go nie obsługuje,
a istniejący guard zwróciłby 404 tylko przypadkiem.

Modele Pydantic są na poziomie modułu w `src/api.py:181-341` — nie ma `schemas.py`.
Handlery zwracają `Model(...).model_dump()`, bez `response_model=`.

**Dwie pre-istniejące dziury w unieważnianiu cache, które import uwidoczni:**

`_perf_invalidate_portfolio` (`src/api.py:98-103`) czyści dokładnie trzy rzeczy:
`positions:{user}:{portfolio}`, `treemap:{user}` i prefiks `calendar:{user}:{portfolio}:`.

1. **`history:` nie jest czyszczone nigdy.** Klucz `history:{user}:{portfolio}:{range}`
   powstał w PUL-79 (`src/api.py:1015-1063`, TTL 300 s), ale helper nigdy go nie objął.
   Klucz rozgałęzia się na cztery wartości `range`, więc potrzebny jest skan po prefiksie.
2. **Sentynel `all` nie jest czyszczony.** PUL-90 uczynił `portfolio_id="all"` realnym
   kluczem cache dla pozycji, kalendarza i historii, ale helper zna tylko konkretne id.
   Zakładka „Wszystkie" jest **pierwsza i domyślna**, czyli dokładnie ta, na którą użytkownik
   wraca po potwierdzeniu importu.

Dla zapisu masowego bezpieczniej jest wyczyścić wszystkie klucze `*:{user_id}:*` niż wołać
istniejący helper — albo naprawić helper, co przy okazji naprawia dzisiejszy zapis pojedynczej
pozycji.

Cache jest **per proces i bez eksmisji**, a Cloud Run chodzi z `--max-instances=2`, więc
zapis obsłużony przez instancję A nie unieważnia kopii instancji B. To własność istniejąca,
ale przepływ „potwierdź import, natychmiast czytaj pozycje" czyni ją znacznie bardziej widoczną.

**Budżet czasu.** Cloud Run ma `--timeout=60` (`deploy.yml:97`). Ryzykiem nie jest parsowanie
(pliki mają 15 i 41 KB), tylko **~20 sekwencyjnych MERGE-ów** przy commicie — każdy
`upsert_user_portfolio_position` to pełny `client.query(...).result()`, typowo 1–3 s.
To realnie 20–60 s. Rozwiązania: jeden MERGE po `UNNEST(@...)` albo zrównoleglenie przez
`asyncio.gather(asyncio.to_thread(...))`, jak już robi endpoint treemapy (`src/api.py:726-740`).
Uwaga: `rows` jest słowem zarezerwowanym w BQ — parametr tablicowy musi nazywać się inaczej.

**Walidacja tickerów.** `list_distinct_portfolio_tickers()` (`db/bigquery.py:2299-2312`) to
właściwy zbiór (`companies UNION DISTINCT etf_instruments`) — `list_distinct_tickers()`
odrzuciłby ETF-y. Funkcja **nie jest cache'owana**, więc import musi ją zawołać **raz**
i porównać cały zbiór tickerów z pliku, nie per wiersz.

Nieznany ticker w pliku należy do **podglądu**, nie do 422. Odrzucenie całego uploadu, bo
jeden z dwudziestu tickerów jest nierozpoznany, jest złym UX dla pliku, którego użytkownik
nie może edytować. Precedensem jest `PortfolioHistoryResponse` (`src/api.py:267-274`)
z listami `notes` / `excluded` — PUL-100 wybrał dokładnie to zamiast cichego pomijania.

### 6. Front — modal importu i ewentualna zakładka dywidend

**Modale istnieją — jest ich pięć i są niespójne.** Żadnego `<dialog>`, żadnego `.showModal()`;
wszystkie to ukryte `<div>` przełączane przez `style.display`.

| Wzorzec | Miejsce | Escape | Klik poza | Blokada scrolla |
|---|---|---|---|---|
| `#modal-overlay` (generyczny) | `static/index.html:1240-1250`, `openModal()` `:4625` | tak `:4686` | tak `:4685` | tak |
| `#idle-warning-overlay` | `:1252-1263` | nie | nie | nie |
| `#pp-edit-overlay` | `:3693-3712` | tak, strzeżony `:3947` | tak `:3946` | nie |
| `#pp-add-portfolio-overlay` | `:3799-3819` | **nie** | tak `:3915` | nie |
| `.tc-popup` (treemapa) | `:3747-3756` | tak, **niestrzeżony** `:3908` | tak `:3897` | nie |

**Najbliższy szablon okna importu to `#pp-add-portfolio-overlay`** — ma dokładnie ten kształt:
`<select>` + pole warunkowe + `<div>` błędu inline + `.pp-modal-actions` z anuluj/zapisz,
łącznie z obsługą 401 → `doLogout()` i 409 → tekst błędu (`_submitAddPortfolio()` `:3606-3633`).
Trzy poprawki wobec szablonu: dodać strzeżony handler Escape (ten wzorzec go nie ma),
umieścić markup wewnątrz szablonu `_buildPortfolioPositionsViewContent` (`:3653-3820`),
i nie powielać błędu niestrzeżonego globalnego listenera z `:3908`.

Warto odnotować standing note z archiwum treemapy: *„jeśli pojawi się trzeci modal —
skonsolidować"*. Okno importu jest już piątym. To nie musi blokować ticketu, ale zasługuje
na świadomą decyzję.

**Przycisk importu** ląduje przy `#pp-export-csv-btn` (`static/index.html:3671`).
Musi respektować tryb read-only sentynela — czyli wejść do `_ppSyncAddBtnVisibility()`
(`:3520-3523`), tak jak `#pp-add-toggle-btn`.

**Import masowy musi wyczyścić cztery cache frontowe**, których dzisiejszy zapis pojedynczej
pozycji nie czyści: `_ppTreemapData`, `_ppCalData`, `_ppHistDataActive`, `_ppHistDataAll`
(zmienne w `static/index.html:3246-3269`). Inaczej treemapa, kalendarz i wykres pokażą stan
sprzed importu.

**Zakładka dywidend — dwa warianty.** Jako czwarta zakładka `data-mode` obok
Tabela/Treemapa/Kalendarz wymaga dopisania linii w liście ukrywania `:3846-3849` (cztery miejsca)
oraz gałęzi w `_selectPortfolioTab()` (`:3536-3538`), żeby zmiana portfela odświeżała dane.
Jako osobny widok najwyższego poziomu wymaga dopisania `display='none'` w **pięciu** funkcjach
`show*View` (`:2418`, `:2432`, `:2513`, `:2634`, `:3985`) plus gałęzi w `_applyUrlState()` —
to lekcja #10 z `lessons.md`. Zakładka `data-mode` jest wyraźnie tańsza i lepiej pasuje
do treści związanej z portfelem.

**Precedens dropdownu lat**: w widoku portfela przełączniki to inline'owe pigułki
(`.pp-view-tabs-inline`, `#pp-history-ranges` `:3775-3784`), nie `<select>`. Jedyny `<select>`
w chrome dashboardu to `#pp-portfolio-type-select`. Wybór roku pigułkami byłby spójniejszy
wizualnie, ale przy rosnącej liczbie lat `<select>` skaluje się lepiej — decyzja do planu.

**Ponownie użyteczne helpery**: `_showSkeleton(tbodyId, cols)` `:2274`, `_sortRows` `:2263`,
`_downloadCsv` `:4488`, `showToast` `:4695`, `esc()` `:4704` (obowiązkowy na każdej ścieżce
`innerHTML`), `_renderPortfolioHistory(data, chartEl)` `:4257` (sparametryzowany po elemencie,
z namespace'owanym id gradientu). `_renderPortfolioTable` jest przywiązany do dziewięciu
stałych kolumn — tabela dywidend potrzebuje własnej funkcji renderującej.

Wszystkie napisy są po polsku. Motyw ciemny to jedna długa lista jawnych selektorów
(`:930-998`) — nowy markup trzeba dopisać ręcznie do właściwych grup.

### 7. Testy, CI i precedens FIFO

**Stan wyjściowy**: 693 testy jednostkowe zielone (15,4 s), 118 e2e, razem 811.
`uv run pytest` odpala wszystko; `uv run pytest --ignore=tests/e2e` to udokumentowane
wyjście awaryjne dla faz celowo czerwonych na e2e.

**Konwencja testowania parsera plików**: dominującym wzorcem jest wklejenie próbki jako
stałej modułowej (`tests/test_backfill_historical_closes.py:20-46` trzyma `_CSV`, `_ASCII`).
Dla xlsx to się załamuje, bo plik jest binarny. Wniosek: **czysta warstwa parsera powinna
operować na już wyekstrahowanych wierszach** (`list[dict]` na arkusz), a `openpyxl` siedzieć
w cienkim adapterze. Wtedy testy jednostkowe idą na literałach, dokładnie jak dziś.

Realnych próbek **nie wolno commitować** — `export_xtb/` zawiera numery rachunków
i jest w `.gitignore`. Fixture'y trzeba wygenerować.

**Pułapka conftestu e2e** (`tests/e2e/conftest.py:542-675`): `live_server_url` wchodzi
w ~50 patchy przez `ExitStack`, a `create_app()` odpala hook startowy z DDL. **Każda
niezapatchowana funkcja DDL oznacza, że cała sesja e2e startuje przeciwko żywemu BigQuery.**
Nowy endpoint wymaga dopisania: `create_dividends_table_if_not_exists`,
`ensure_dividends_schema_current` oraz każdej nowej funkcji danych — patchowanych na
`src.api.*` (miejsce importu), nie `db.bigquery.*`.

Podgląd musi być **stateful** (`side_effect=`, nie `return_value=`), bo przepływ
import → podgląd → commit jest wielożądaniowy. Dodatkowo lekcja z PUL-100: fake musi
renderować **obie** gałęzie — wiersze z pliku i sekcję „są w portfelu, nie ma w pliku" —
bo tam gałąź `excluded` nigdy nie została wykonana, skoro każdy fake zwracał `[]`.
*Najgorszy przypadek to ten, którego żaden test nie renderuje.*

Pułapki lokatorów w e2e portfela: domyślna zakładka to read-only „Wszystkie", więc każdy
test najpierw klika „Główny"; gołe `expect(page.locator(".pp-portfolio-tab"))` pada
na strict-mode przy dwóch zakładkach; PUL-90 **odrzuciło** dodanie drugiego portfela
do wspólnego conftestu, bo destabilizowało cały pakiet.

**CI**: `tests.yml` odpala pełny pakiet bez GCP. Wiążącą bramką jest status
`ai-code-review/verdict` (`ai-code-review.yml:260`), fail-closed. **Nie ma w CI kroku lint
ani typecheck** — `ruff`, `mypy` i `tach` są konwencją egzekwowaną w review.
`ruff check .` nie jest spełnialnym kryterium (33 pre-istniejące błędy) — kryteria lintu
trzeba zawęzić do zmienionych plików.

**Precedens FIFO nie istnieje** — `grep -i fifo` po `.py`/`.html` nie zwraca nic poza `change.md`.
Koszt nabycia liczony jest w czterech miejscach i silnik FIFO musi być z nimi zgodny liczbowo:

| Miejsce | Wzór |
|---|---|
| `src/api.py:806-811` | `pnl_pln = (current - avg) * shares` |
| `src/portfolio_treemap.py:40-48` | `since_purchase_pct = (current / avg - 1) * 100` |
| `db/bigquery.py:617` | `pnl_pln = SUM(shares * (px_ff - avg))` |
| `src/api.py:355-392` `_merge_positions_by_ticker` | `avg = Σ(shares × avg) / Σ shares` — **średnia ważona kosztem, nie FIFO** |

Warunek spójności: FIFO musi zapisywać `avg_buy_price = pozostały_koszt / pozostałe_akcje`.
Wtedy wszystkie cztery wzory nadal dają dokładnie niezrealizowany wynik na pozostałych lotach,
a tryb „Wszystkie" (który uśrednia ponownie między portfelami) pozostaje poprawny.

Wartość musi być `> 0`, bo `src/api.py:822-823` odrzuca `avg_buy_price <= 0`. Celowe 0,01 zł
przy S2B przechodzi tę bramkę — ale ścieżka commitu importu omijająca ten endpoint musi
zastosować własny równoważny guard.

## Code References

- `src/api.py:395-1067` — `create_app()`; `app.mount("/static")` na `:1065` musi zostać ostatni
- `src/api.py:409-422` — hook startowy DDL; tu rejestruje się nową tabelę
- `src/api.py:98-103` — `_perf_invalidate_portfolio`; nie zna `history:` ani sentynela `all`
- `src/api.py:181-341` — wszystkie modele Pydantic
- `src/api.py:267-274` — `PortfolioHistoryResponse`, wzorzec koperty pod podgląd importu
- `src/api.py:355-392` — `_merge_positions_by_ticker`, średnia ważona kosztem
- `src/api.py:726-740` — zrównoleglenie zapytań BQ przez `asyncio.to_thread`
- `src/api.py:816-842` — `POST /api/portfolio/positions`, wzorzec do sklonowania
- `src/api.py:822-823` — guard `shares <= 0 or avg_buy_price <= 0` → 422
- `db/bigquery.py:149-181` — `ensure_schema_current`, wyłącznie addytywne
- `db/bigquery.py:700-729` — schemat i DDL `user_portfolio_positions`
- `db/bigquery.py:732-783` — `upsert_user_portfolio_position`, pełny MERGE nadpisujący
- `db/bigquery.py:2299-2312` — `list_distinct_portfolio_tickers`, niecache'owana
- `db/bigquery.py:2801-2862` — `_merge_insert_only` z `QUALIFY`, prymityw pod dywidendy
- `db/bigquery.py:3174-3199` — `notification_sent_log`, najczystszy wzorzec nowej tabeli
- `db/bigquery.py:623-650` — join meta-first, wzorzec pod listę dostępnych lat
- `static/index.html:3671` — `#pp-export-csv-btn`, obok niego przycisk importu
- `static/index.html:3799-3819` — `#pp-add-portfolio-modal`, szablon okna importu
- `static/index.html:3606-3633` — `_submitAddPortfolio()`, wzorzec submitu z błędem inline
- `static/index.html:3520-3523` — `_ppSyncAddBtnVisibility()`, tryb read-only
- `static/index.html:3246-3269` — zmienne stanu `_pp*`, w tym cztery cache do wyczyszczenia
- `static/index.html:3846-3849` — lista ukrywania paneli `data-mode` (cztery miejsca)
- `static/index.html:2418, 2432, 2513, 2634, 3985` — pięć funkcji `show*View`
- `src/portfolio_treemap.py:4-65` — wzorzec czystej funkcji z testem 1:1
- `tests/e2e/conftest.py:542-675` — `live_server_url` i pełna lista patchy
- `tests/test_backfill_historical_closes.py:7-18` — wzorzec importlib do testowania skryptów
- `scripts/test_bq_insert_only_merge.py:31-71` — round-trip na tabeli jednorazowej
- `.github/workflows/deploy.yml:81-97` — `puls-gpw-api`: 512 MiB, 1 CPU, timeout 60 s, max 2 instancje

## Architecture Insights

**Startowy hook DDL jest mechanizmem migracji.** Nie ma Alembica ani żadnego odpowiednika —
schemat wędruje na produkcję przez `create_* + ensure_*` przy starcie rewizji. To działa,
ale niesie konsekwencję: partycjonowanie i klastrowanie trzeba wybrać **zanim** tabela
powstanie na produkcji, bo później są niemigrowalne.

**Trzy odmiany MERGE to realny wybór projektowy**, nie szczegół implementacyjny:
pełny upsert nadpisuje wszystkie kolumny (więc użyty do importu wyzeruje pola pisane inną
ścieżką), insert-only nie potrafi korygować, update-only nie potrafi wstawiać. Commit importu
pozycji jest najbliżej „update-or-insert zawężonego do tickerów obecnych w pliku" —
i **nigdy** `WHEN NOT MATCHED BY SOURCE`, bo to skasowałoby S2B.

**Mapowanie kolumn po nagłówku, nigdy po pozycji.** To bezpośrednia lekcja z PUL-98, gdzie
indeksowanie pozycyjne pozwoliło defektowi żyć miesiąc. Dla arkuszy xlsx oznacza to odczyt
wiersza nagłówkowego i mapowanie po tekście, z głośnym błędem przy braku kolumny — nigdy
zgadywanie.

**Prowenienca czyni tabelę o mieszanym pochodzeniu audytowalną.** PUL-98 dodało kolumnę
`source` właśnie po to. Wiersze dywidend powinny nieść znacznik brokera i identyfikator
przebiegu importu.

**Podwójna warstwa własności.** Warstwa DB nigdy nie weryfikuje, czy `portfolio_id` należy
do `user_id` — dokłada tylko oba warunki do `WHERE`. Weryfikacja własności jest wyłącznie
w API. Każdy nowy endpoint musi ją powtórzyć, bo nie ma helpera.

## Historical Context (from prior changes)

- `context/archive/2026-07-24-backfill-historical-closes/` — najbliższy krewny: jednorazowy
  import zewnętrznych plików do BQ. Stąd pochodzi MERGE insert-only, kontrakt CLI z `--dry-run`
  jako domyślnym trybem raportującym, oraz zasada, że nigdy nie używamy streaming insert
  do importu (duplikuje przy powtórce, a bufor blokuje MERGE na ~90 minut).
- `context/archive/2026-07-27-official-close-source/` — stąd: mapowanie kolumn po nagłówku,
  kolumna prowieniencji, bramka niejednoznaczności przy mapowaniu nazwa→ticker, oraz
  „archiwum jest wyrocznią, arytmetyka na kursach nie" — bezpośrednia analogia do FIFO
  kontra średnia ważona.
- `context/archive/2026-07-26-history-coverage-gate/` — stąd koperta `{series, notes, excluded}`
  i zasada, żeby w tekście dla użytkownika nie twierdzić więcej, niż dane pozwalają.
  Bezpośrednio stosuje się do sekcji „są w portfelu, nie ma w pliku".
- `context/archive/2026-07-24-pul-90-wszystkie-aggregate/` — scalanie wierszy o wspólnym kluczu:
  suma udziałów, średnia ważona ceny, pakiet rynkowy z wiersza o najświeższym `price_as_of`.
  Ponownie użyteczne przy scalaniu fill-ów z pliku.
- `context/archive/2026-07-22-pul-79-portfolio-value-history/` — udokumentowane przybliżenie
  „dzisiejsza liczba akcji × historyczny kurs", **ponieważ nie przechowujemy dat transakcji**.
  Zobacz Open Questions.
- `context/archive/2026-06-29-pul-67/` — nowa klasa instrumentów dostała własną tabelę
  zamiast poszerzenia `companies`; walidacja tickerów to unia obu.
- `context/foundation/lessons.md:211-235, 270-291, 294-325` — trzy lekcje bezpośrednio wiążące:
  round-trip na realnym BQ, pięć miejsc ukrywania widoku, kolejność przy kolumnach `REQUIRED`.

## Open Questions

1. **Czy zapisujemy daty transakcji?** To pytanie sięga daleko poza ten ticket. PUL-79 świadomie
   przyjęło przybliżenie „dzisiejsze udziały × historyczny kurs", bo dat nabycia nie było.
   **Import XTB to pierwsza zmiana, która te daty faktycznie ma.** Zapisanie ich otwiera
   poprawny wykres wartości historycznej i zrealizowany P/L; niezapisanie oznacza świadome
   dziedziczenie przybliżenia mimo posiadania danych. Decyzja należy do planu i powinna być
   podjęta jawnie, bo determinuje, czy powstaje jedna tabela (dywidendy) czy dwie
   (dywidendy + transakcje).

2. **Zakładka `data-mode` czy osobny widok dla dywidend?** Zakładka jest wyraźnie tańsza
   (cztery miejsca zamiast pięciu plus routing) i lepiej pasuje treściowo. Osobny widok
   ma sens tylko wtedy, gdy chcemy deep-linka i osobnej pozycji w nawigacji.

3. **Czy naprawiamy dziury w cache przy okazji, czy osobno?** `history:` i sentynel `all`
   nie są dziś unieważniane. To defekty pre-istniejące, ale import je uwidoczni w najgorszym
   możliwym miejscu — na domyślnej zakładce zaraz po potwierdzeniu. Naprawa jest mała
   i przy okazji poprawia dzisiejszy zapis pojedynczej pozycji, ale formalnie to poszerzenie
   zakresu.

Trzy rzeczy, które **nie** są otwarte, bo dane je rozstrzygnęły: klucz deduplikacji
(kolumna `ID`), metoda liczenia średniej (FIFO odtwarza XTB co do grosza) oraz nieniszczący
charakter importu (S2B nie ma w pliku, a jest realną pozycją).
