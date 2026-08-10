---
title: Destylacja domeny — puls-gpw / Faro
created: 2026-08-08
type: domain-distillation
---

# Destylacja domeny — puls-gpw / Faro

> **Czym jest ten dokument.** Mapą domeny, nie planem refaktoru i nie kodem. Odpowiada na trzy
> pytania: jakim językiem mówi ten produkt, która jego część jest rdzeniem, i w których miejscach
> wiedza domenowa istnieje w dokumentach albo w docstringach, a **nie istnieje w kodzie**.
>
> **Metoda.** Odkrycie → analiza → klasyfikacja. Każde twierdzenie ma cytat `plik:linia`, który
> realnie otworzyłem. Tam, gdzie pojęcie nie ma odpowiednika w kodzie, jest to napisane wprost
> jako **BRAK w kodzie** — to nie luka w badaniu, to wynik.
>
> **Stan na commit `e4b2066`** (2026-08-07), 79. dzień życia repozytorium.

---

## KROK 0 — Kontekst projektu

### Co odkryłem, zanim zacząłem nazywać

Dokumenty wymagań **istnieją i są kompletne** — nie musiałem odtwarzać intencji z kodu.
`context/foundation/` zawiera `prd.md` (v1, `status: draft`, `created: 2026-05-18`),
`roadmap.md` (v1, `updated: 2026-06-08`), `shape-notes.md`, `tech-stack.md`, `lessons.md`.
`README.md` w korzeniu ma **0 bajtów** — jest pusty i nie niesie nic.

Do tego dochodzą dwa źródła nietypowe i wyjątkowo nośne:

- `context/archive/` — **79 zamkniętych zmian**, każda z `change.md` / `plan.md` / `impl-review.md`.
  To de facto rejestr decyzji projektu. `context/map/repo-map.md:118-134` stwierdza to wprost:
  *„wiedza o projekcie mieszka w `context/`, nie w kodzie"*. Archiwum okazało się przy tym
  najlepszym źródłem **negatywnym**: to w nim widać, czego projekt nigdy nie postanowił, bo
  brak decyzji w rejestrze decyzji jest sam w sobie dowodem (patrz R-02).
- **docstringi**. W tym repozytorium docstring nie opisuje sygnatury — opisuje **decyzję domenową
  wraz z odrzuconą alternatywą i pomiarem**. `src/portfolio_lots.py:1-29` czy
  `db/bigquery.py:414-461` to nie komentarze, to zapisy rozstrzygnięć. Traktuję je jako
  pełnoprawne źródło modelu, na równi z PRD.

### Stack i warstwy

Python 3.13 + FastAPI + Pydantic + BigQuery + Cloud Run, zarządzane przez `uv`
(`AGENTS.md:3`). Warstwy — w kolejności, w jakiej realnie leży w nich logika:

| Warstwa | Gdzie | Uwaga |
| -- | -- | -- |
| Punkty wejścia (7) | `main.py`, `post_main.py`, `api_main.py`, `company_stats_main.py`, `etf_quotes_main.py`, `cost_report_main.py` | joby Cloud Run + jeden serwis HTTP |
| API / kompozycja | `src/api.py` (1568 linii, 32 endpointy) | tu siedzi **walidacja domenowa**, nie w modelach |
| Domena „czysta" | `src/portfolio_lots.py`, `src/portfolio_realized.py`, `src/post_supervisor.py`, `src/brokers/xtb.py`, `src/tickers.py` | jedyne moduły bez I/O; `portfolio_lots.py:31-33` importuje wyłącznie stdlib |
| Persystencja + **druga domena** | `db/bigquery.py` (4493 linie, 106 funkcji) | zawiera reguły domenowe zapisane w SQL-u — patrz R-01 |
| Frontend | `static/index.html` (6326 linii, jeden plik) | brak modułów; język UI **polski**, język kodu **angielski** |

**Gdzie naprawdę żyje logika biznesowa — wniosek nieoczywisty.** Nie w warstwie domenowej.
Trzy najważniejsze reguły produktu leżą poza nią: próg jakości X-posta to `WHERE` w SQL-u
(`db/bigquery.py:2377-2379`), rekonstrukcja stanu posiadania to CTE w stringu
(`db/bigquery.py:944-960`), a walidacja pozycji portfela to `if` w handlerze HTTP
(`src/api.py:1148-1149`). Warstwa „czysta" jest czysta, ale **nie jest kompletna**.

### Ograniczenia tej destylacji

- **Nie odpytywałem produkcyjnego BigQuery na potrzeby tej destylacji.** Twierdzenia o danych
  („czy rozjazd jest aktywny") były tu `unknown`. **AKTUALIZACJA 2026-08-10:** te konkretne
  pytania zostały już zmierzone — patrz sekcja „Pomiary produkcyjne" w
  `context/changes/portfolio-lots-flow/research.md`. Wynik: rozjazd **jest** aktywny (1 ze 108
  wierszy `holders`), ale na przypadku łagodnym — pozycja wpisana ręcznie, bez operacji.
  Nie zmienia to żadnego wniosku poniżej; **wzmacnia** R-03 („pozycja bez lotów"), bo pokazuje,
  że taka pozycja realnie istnieje w produkcyjnych danych, a nie tylko w rozważaniach.
- **Nie mierzyłem pokrycia testami** — `pytest-cov` nie jest zainstalowany (potwierdzone tam samo,
  w sekcji o T4). „Brak testu" oznacza „nie znalazłem asercji", nie „zmierzyłem 0%".
- **Nie czytałem wszystkich 79 zmian archiwum w całości** — czytałem je przekrojowo pod kątem
  decyzji domenowych. Dokument nie rości sobie kompletności rejestru decyzji.
- `oldProjectData/`, `d_pl_txt/`, `stooq_raw/`, `broker_data/` pominąłem jako dane, nie kod.

---

## KROK 1 — Ubiquitous Language

Pierwsze odkrycie jest strukturalne i determinuje resztę dokumentu: **to nie jest jedna domena
z jednym słownikiem.** Ten sam produkt mówi trzema słownikami, które nie znają swoich nazw.
Rozdzielam je, bo scalenie ich w jedną tabelę zatarłoby najważniejszy fakt — że słowo *portfel*
znaczy tu dwie różne rzeczy, a słowo *supervisor* dwie inne.

### 1A. Słownik: komunikat i jego ocena (kontekst rdzeniowy)

| Pojęcie | Definicja | Źródło (dokument) | Gdzie żyje w kodzie |
| -- | -- | -- | -- |
| **Komunikat (announcement)** | Pojedynczy raport ESPI/EBI spółki z GPW/NewConnect, zescrapowany z Bankier.pl | `prd.md:20`, `prd.md:36` | dataclass `src/scraper.py:15-22`; tabela `announcements`, schemat `db/bigquery.py:56-75` |
| **announcement_id** | Deterministyczny SHA256 URL-a — stabilny klucz dedup, wyliczalny **bez** insertu | *brak w PRD* | `db/bigquery.py:118-120`; użycie przed zapisem `main.py:112` |
| **ESPI vs EBI** | Dwa systemy raportowania; ESPI = rynek regulowany, EBI = NewConnect. **Nazwa produktu** | `prd.md:20`, `AGENTS.md:3` | odczytane `src/scraper.py:102-103` → **BRAK w kodzie dalej**: `insert_announcement` (`db/bigquery.py:2207-2212`) nie przyjmuje `source`, w `_SCHEMA` nie ma takiej kolumny |
| **espi_code** | Kod raportu z tytułu (część przed dwukropkiem) | *brak w PRD* | wyliczany `src/scraper.py:100` → **BRAK w kodzie dalej** — nigdzie niezapisywany |
| **priority („Ważny")** | Badge Bankiera oznaczający komunikat wyróżniony | *brak w PRD* | `src/scraper.py:105-106`; kolumna `db/bigquery.py:68`; jedyne użycie: `src/analyzer.py:228` |
| **parsed_content** | Treść komunikatu wyciągnięta z PDF-a albo HTML-a (fallback) | `prd.md:77-80` (FR-004, FR-005) | `src/parser.py:41`; kolumna `db/bigquery.py:67` |
| **hop_url** | URL profilu spółki na Bankierze — drugi skok HTTP, z którego bierze się tożsamość spółki | *brak w PRD* | `src/parser.py:178-195`, `src/company_profile.py:82-88` |
| **event_type (typ zdarzenia)** | Kategoria zdarzenia; **15 dopuszczonych wartości** | `prd.md:111-119` mówi o **dwóch** trybach: „finansowy" i „korporacyjny" | `src/analyzer.py:21-26`; fallback na `"inne"` `:265-270` |
| **sentiment (sentyment)** | Wydźwięk komunikatu dla inwestora: `pozytywny \| negatywny \| neutralny` | *brak w PRD* | zbiór `src/analyzer.py:45`; cicha koercja `:55-58`; **nie ma własnej kolumny** — żyje w JSON-ie `structured_analysis` |
| **key_numbers** | Lista kluczowych kwot z komunikatu — jedyne, co weryfikuje bramka | `prd.md:50` („format liczbowy"), `prd.md:130` | kontrakt w prompcie `src/analyzer.py:87-129`; pole `_AnalysisResponse` `:52` |
| **structured_analysis** | Analiza jako JSON-string (`event_type`/`sentiment`/`key_numbers`/`summary_pl`) | `prd.md:85` (FR-007) | serializacja `src/analyzer.py:272`; kolumna `db/bigquery.py:69` |
| **analysis_score (istotność)** | Liczbowa waga komunikatu. **Nie pochodzi z AI** mimo nazwy: `tier_bonus + event_score + priority_bonus`. Pojęcie **admin-only** — nie zostało usunięte, tylko ukryte przed użytkownikiem | *brak w PRD* — pojęcie powstało w kodzie; zakres widoczności rozstrzygnięty w `context/archive/2026-06-19-remove-analysis-score` (*„still written by the analyzer, still read by admin"*) | `src/analyzer.py:221-229`; składniki `:27-35` i `:37-43`; gate widoczności `src/api.py:974`, `:995` |
| **tier spółki** | Waga „ta spółka mnie obchodzi" — **33 zahardkodowane tickery w trzech koszykach** | `roadmap.md:153` nazywa T1 wprost *„portfel"* | `src/analyzer.py:27-33` |
| **Supervisor / gate (bramka analizy)** | Drugie wywołanie Gemini sprawdzające, czy liczby w analizie są w źródle | `prd.md:87` (FR-008), `prd.md:128-134`, `AGENTS.md:12` | prompt `src/analyzer.py:132-151`; wywołanie `_call_gate` `:193`; werdykt `:207` |
| **analysis_approved** | Werdykt bramki: `True` / `False` / `None` (= bramka nie odpowiedziała) | `prd.md:49` | `src/analyzer.py:274-298`; semantyka NULL-i udokumentowana `db/bigquery.py:3-16` |
| **Duplikat** | Komunikat już przetworzony; sprawdzany **przed** wywołaniem AI | `prd.md:47` (Guardrail 1), `prd.md:104`, `AGENTS.md:13` | `src/scraper.py:110-111`, zbiór z `:50`, okno `:48` |
| **Spółka (company)** | Wpis słownikowy `ticker/name/hop_url/isin` | *brak w PRD* | tabela `db/bigquery.py:2063`; `upsert_company` `:2097` |
| **Ticker** | Symbol giełdowy; jeden regex dzielony przez 4 ścieżki zapisu | `prd.md:63`, `prd.md:122` | `src/tickers.py:21`, `is_valid_ticker` `:24-31` |

### 1B. Słownik: dystrybucja — X-post i powiadomienia (kontekst rdzeniowy, druga połowa)

| Pojęcie | Definicja | Źródło (dokument) | Gdzie żyje w kodzie |
| -- | -- | -- | -- |
| **X-post / wątek (thread)** | Zestaw tweetów: hook + jeden per spółka + closing | `prd.md:91` (FR-009), `prd.md:121-126` | `GeneratedPost` `src/post_generator.py:178-180`; reguła liczby `post_main.py:306` |
| **Okno (window)** | `ranek \| poludnie \| wieczor` — pora dnia, dla której powstaje jeden wątek | *brak w PRD* — PRD zakłada **post per komunikat** | `post_main.py:185-200`, granice `:237-250` |
| **Cashtag** | `$TICKER`; X zwraca 403 przy więcej niż jednym w **jednym tweecie** | *brak w PRD* | reguła w prompcie `src/post_generator.py:77-90`; domykanie `_enforce_body_cashtag` `:209-221`; regex `src/post_supervisor.py:29` |
| **Supervisor postu** | **Deterministyczny walidator regułowy**, bez wywołania LLM | `prd.md:128-134` opisuje kryteria; nie mówi, że są deterministyczne | `src/post_supervisor.py:39-104` |
| **Próba (attempt)** | Jedna runda generacja → ocena; limit 3, z uwagami dopisywanymi do promptu | `prd.md:87` (FR-008) | `_MAX_ATTEMPTS` `post_main.py:38`; pętla `:313-359`; feedback `src/post_generator.py:418-427` |
| **Próg jakości (MIN_XPOST_SCORE)** | Komunikat poniżej 50 pkt nigdy nie wejdzie do posta | *brak w PRD* | `post_main.py:48`; egzekucja w SQL `db/bigquery.py:2377-2379` |
| **Guard rekomendacji** | 12 wzorców frazowych blokujących sugestię inwestycyjną | `prd.md:151` (Non-goal: rekomendacje wymagają licencji KNF) | `src/post_supervisor.py:9-24`, egzekucja `:83-85` |
| **x_publish_status** | `published \| skipped \| failed \| partial` | *brak w PRD* — publikacja przez API to Non-goal `prd.md:148` | `src/x_publisher.py:9-12`; zapis `db/bigquery.py:3064` |
| **Watchlist** | Lista tickerów obserwowanych przez użytkownika | *brak w PRD* | tabela `db/bigquery.py:1224-1233` |
| **Powiadomienie (notification)** | E-mail do obserwującego, wysyłany **w chwili analizy komunikatu** | `prd.md:33` mówi o e-mailu **do właściciela** | hook `main.py:150-173`; dedup `db/bigquery.py:4341-4344` |

### 1C. Słownik: portfel i loty FIFO (kontekst wspierający)

**Cały ten słownik nie ma ani jednego odpowiednika w `prd.md` ani w `roadmap.md`.** Portfel
występuje w PRD wyłącznie jako **Non-goal** (`prd.md:150`). To nie jest przeoczenie w moim
badaniu — to najważniejszy pojedynczy fakt o tej domenie i wracam do niego w KROKU 4.

| Pojęcie | Definicja | Źródło (dokument) | Gdzie żyje w kodzie |
| -- | -- | -- | -- |
| **Portfel / wallet (`portfolio_id`)** | Rachunek użytkownika: `glowny \| ikze \| ike \| ppk \| ppe \| inny` | **BRAK w PRD**; decyzje w archiwum | `Literal` `src/api.py:584`; tabela `db/bigquery.py:1674-1681` |
| **„Wszystkie"** | Sentynel `portfolio_id = "all"` — widok agregujący wszystkie portfele | **BRAK w PRD** | `_ALL_PORTFOLIOS` `src/api.py:628`; scalanie `_merge_positions_by_ticker` `:669-692` |
| **Pozycja (position)** | Stan posiadania: `(portfolio_id, ticker) → shares, avg_buy_price` | **BRAK w PRD** | model `src/api.py:588-593`; tabela `db/bigquery.py:1262-1271` |
| **Operacja brokerska** | Wiersz z eksportu brokera; **rejestr ruchów, które broker widział — nie stanu posiadania** | **BRAK w PRD**; definicja w docstringu `src/portfolio_lots.py:22-25` | tabela `db/bigquery.py:4060-4076` |
| **op_type** | `buy \| sell \| dividend \| withholding_tax \| cash` | **BRAK w PRD** | `src/brokers/xtb.py:19-23`; kolumna `REQUIRED STRING` **bez enuma** `db/bigquery.py:4065` |
| **Lot** | Paczka akcji kupiona w jednym momencie, po jednej cenie, pamiętająca **kiedy** | **BRAK w PRD**; definicja `src/portfolio_lots.py:63-73` | `OpenLot` `src/portfolio_lots.py:63-73` |
| **Księga lotów (ledger)** | Jedyny przebieg FIFO w repo; jeden na ticker | **BRAK w PRD**; docstring `src/portfolio_lots.py:1-29` | `build_ledger` `src/portfolio_lots.py:145-217` |
| **Sprzedaż (Sale)** | Byt pierwszej klasy niosący **własny wolumen**, z dopasowaniami lotów jako dziećmi | docstring `src/portfolio_lots.py:16-20` | `src/portfolio_lots.py:85-107` |
| **uncovered (niepokryte)** | Sprzedane sztuki, których żaden lot nie potrafił wycenić; **podłogowane do zera** | docstring `src/portfolio_lots.py:25-28` | `src/portfolio_lots.py:208`; agregat `:127-130` |
| **residuum (residual)** | `today_shares − Σ(±volume)` — sztuki, których operacje **nie tłumaczą**; stałe w czasie | docstring `db/bigquery.py:427-436` | wyliczenie `src/portfolio_lots.py:306`; wariant SQL `db/bigquery.py:1120` |
| **Podstawa kosztowa datowana (BasisSegment)** | Koszt na akcję jako **funkcja schodkowa** — zmienia się tylko w dni transakcji | docstring `src/portfolio_lots.py:241-245` | `src/portfolio_lots.py:241-250`, `basis_segments` `:260-347` |
| **Okres posiadania** | Data nabycia **najstarszego otwartego lotu** — świadomie **nie** `MIN(buy date)` | docstring `src/portfolio_lots.py:354-360` | `first_open_lot_dates` `:350-366`; UI `static/index.html:3885` |
| **DUST** | `1e-9` — próg, poniżej którego lot uznaje się za skonsumowany (akcje ułamkowe) | docstring `src/portfolio_lots.py:38-42` | `src/portfolio_lots.py:42` |
| **Gotówka (`_CASH`)** | Wolne środki trzymane jako **zwykła pozycja** wyceniana po 1,00 PLN | docstring `db/bigquery.py:1665-1669` i `src/api.py:501-518` | `CASH_TICKER` `db/bigquery.py:1670`; `_cash_position` `src/api.py:500-527`; UI „Wolne środki" `static/index.html:4333` |
| **Zysk zrealizowany** | Co sprzedaż realnie zwróciła wobec kosztu akcji — **świadomie oddzielone od dywidend** | docstring `src/portfolio_realized.py:6-9` | `compute_realized_pnl` `src/portfolio_realized.py:49` |
| **Nieobjaśnione posiadanie** | Pozycja, której operacje brokerskie nie tłumaczą | docstring `db/bigquery.py:369-380` | `_log_unexplained_holdings` `db/bigquery.py:371-392`; licznik `residual_holders` `:1120` |
| **Akcja korporacyjna** | Split, scalenie, asymilacja, dywidenda rzeczowa, spin-off | nazwane w 4 docstringach: `src/portfolio_lots.py:24`, `db/bigquery.py:425`, `db/bigquery.py:1355-1357`, `src/api.py:386` | **BRAK w kodzie** — patrz R-02 |

### 1D. Słownik: notowania (kontekst wspierający)

Ten słownik również nie występuje w `prd.md` — powstał w całości z potrzeby wyceny portfela.
Zawiera jednak **najostrzejsze rozstrzygnięcia domenowe w całym projekcie**, i to one czynią
lukę z R-02 dotkliwą.

| Pojęcie | Definicja | Źródło (dokument) | Gdzie żyje w kodzie |
| -- | -- | -- | -- |
| **Kurs zamknięcia (oficjalny)** | Zamknięcie z GPW/NewConnect; to, co pokazuje listing Bankiera, **nie jest** oficjalnym zamknięciem (raz best bid, raz best ask) | `context/archive/2026-07-27-official-close-source/change.md` | kolumna `source` w korektach `db/bigquery.py:3269`; `src/gpw_archive.py` |
| **Priorytet źródeł** | GPW/NC zawsze wygrywa; Bankier wyłącznie jako łatacz luk i **nigdy** nie nadpisuje wartości GPW | decyzja właściciela, `2026-07-27-official-close-source` | `_CLOSE_CORRECTION_COLUMNS` `db/bigquery.py:3768` |
| **`Kurs odn.`** | Cena odniesienia, **nie** poprzednie zamknięcie — GPW koryguje ją o akcje korporacyjne, więc w dniu ex-dywidendy i splitu legalnie różni się od zamknięcia. **Detektor, nigdy źródło wartości** | `context/archive/2026-07-27-official-close-source/plan.md:122-126` — zmierzone: `ECHO` 2026-06-24 zamknęło **+0,10% oficjalnie wobec −7,40% policzonego naiwnie** | zasada wyrażona przez to, że kolumna nie jest zapisywana jako cena |
| **Kurs surowy vs skorygowany** | Projekt wybrał **serię surową (nieskorygowaną)**, bo zgadza się z liczbą akcji, którą użytkownik realnie trzyma | `context/archive/2026-07-24-backfill-historical-closes/plan.md:24` — bitmask `&o=1111111` wyłącza korekty o **splity, dywidendy, prawa poboru**; zmierzone: KRU 2026-01-02 = 498,40 surowo vs 475,38 skorygowane | `src/stooq_raw.py`, `src/gpw_archive.py` |
| **ETF / ETC / ETN** | Fundusze, **nie spółki** — osobny rejestr instrumentów | `context/archive/2026-06-29-pul-67/plan-brief.md` | tabela `etf_instruments`; wycena przez `COALESCE(company_daily_stats, etf_quotes)` |
| **Pokrycie historii (coverage gate)** | Bezpiecznik odpalający się dopiero, gdy ticker **nigdzie** nie ma ceny — nie gdy brakuje jej w części okna | `context/archive/2026-07-26-history-coverage-gate` | `db/bigquery.py:1143-1147` |
| **LOCF / BOCF** | Uzupełnianie ceny w przód (ostatnia znana) i w tył (cena debiutu). 0-fill jest **błędem**: dawał fałszywy skok ~25% wartości | `context/archive/2026-07-22-pul-79-portfolio-value-history/plan.md:44-49` | `db/bigquery.py:846`, `:1079-1091` |

**Dlaczego to jest ważne dla R-02.** Wybór serii surowej jest spójny i uzasadniony: skorygowana
cena kłamie o tym, ile akcji dziś trzymasz. Ale ten wybór **przenosi obowiązek korekty na liczbę
akcji** — jeśli ceny nie są korygowane o split, to liczba sztuk musi być. W tym produkcie
nie koryguje jej nic.

### 1E. Trzy kolizje nazw, które trzeba nazwać, zanim cokolwiek dalej

To nie są ciekawostki. Każda z nich to miejsce, w którym rozmowa o produkcie może się rozjechać
bez niczyjej winy.

1. **„Supervisor" to dwie różne rzeczy.** W kontekście komunikatu to **LLM-audytor liczb**
   (`src/analyzer.py:132-151`), jednostrzałowy. W kontekście X-posta to **deterministyczny
   walidator regułowy** bez Gemini (`src/post_supervisor.py:39-104`), z 3 próbami. Kolumna
   `x_posts.supervisor_attempts` liczy wyłącznie ten drugi.
2. **„Portfel" to dwie różne rzeczy.** `user_portfolios` / `user_portfolio_positions` to portfel
   **śledzony** (ma loty, `portfolio_id`, **nie ma waluty**). `portfolio_snapshots`
   (`db/bigquery.py:202-214`) to portfel **zrzucony ze zrzutu ekranu brokera** na potrzeby wątku
   X — klucz nazywa się `wallet`, **ma kolumnę `currency`** (`:209`), nie ma lotów ani historii.
   Dwa byty, jedno słowo, zero wspólnego kodu.
3. **„Score" nie znaczy oceny AI.** `analysis_score` jest w 100% deterministyczne
   (`src/analyzer.py:221-229`). Nazwa sugeruje ocenę modelu; to jest waga redakcyjna właściciela.

**Waluta — obserwacja przekrojowa.** `currency` istnieje w produkcie dokładnie tam, gdzie
portfel jest zrzutem ekranu (`db/bigquery.py:209`, `src/gemini_client.py:97`,
`src/portfolio_thread_composer.py:30`), i **nie istnieje** tam, gdzie portfel jest księgowany:
ani w `LotEvent` (`src/portfolio_lots.py:45-60`), ani w `user_broker_operations`
(`db/bigquery.py:4060-4076`). Jedyną osłoną jest odrzucenie instrumentów bez sufiksu `.PL`
(`src/brokers/xtb.py:129-135`). Kontekst, który waluty **nie ma**, jest tym, który liczy pieniądze.

---

## KROK 2 — Klasyfikacja subdomen

### Jak rozstrzygnąłem, co jest rdzeniem

To jest pytanie, w którym łatwo pomylić **gdzie leży kod** z **gdzie leży wartość**. Rozstrzygam
je celami produktu, bo tak każe definicja: rdzeń to to, co stanowi przewagę i sens produktu.

**Za rdzeniem = analiza ESPI/EBI przemawia wszystko, co produkt o sobie napisał:**

- Vision (`prd.md:20-22`) mówi wyłącznie o komunikatach; przewaga jest nazwana wprost:
  właściciel *już* prowadzi konto X z takimi analizami robionymi ręcznie, więc *„istnieje dowód
  popytu i istniejąca publiczność"*. To jest zdanie o przewadze konkurencyjnej.
- Success Criteria — **primary** (`prd.md:41`) to jedno zdanie od komunikatu do e-maila.
  **Secondary** (`prd.md:44`) mierzy jakość promptów. **Guardrails** (`prd.md:47-50`) to cztery
  reguły, wszystkie o komunikacie.
- North star roadmapy (`roadmap.md:24`) to S-04 — pierwszy pełny przebieg pipeline'u.
- Wszystkie 11 wymagań funkcjonalnych (FR-001…FR-011, `prd.md:69-98`) dotyczy komunikatów.
- Nazwa produktu w `AGENTS.md:3` i `repo-map.md:11-13`: analizator ESPI/EBI, który podaje wynik
  *„w formie listy, watchlisty i portfela"* — portfel jest tam **powierzchnią dostarczenia**.

**Przeciw rdzeniowi = portfel przemawia jedno zdanie, ale bardzo mocne:** `prd.md:150` wymienia
*„Integracja z systemem AI brokerów (portfele, sygnały)"* jako **Non-goal → v3+**. Portfel nie
jest w PRD niedopowiedziany — jest **jawnie wykluczony**.

**Wniosek.** Core = analiza i ocena komunikatu + jej dystrybucja. Portfel jest Supporting: nie
jest tym, po co produkt istnieje, ale bez niego produkt nie ma dziś użytkownika. Odnotowuję przy
tym paradoks, który jest osią KROKU 4: **subdomena wspierająca zjadła 3 z 4 ostatnich miesięcy
pracy i większość kodu, nie mając ani jednego zapisanego wymagania.**

### Tabela klasyfikacji

| # | Obszar | Klasa | Uzasadnienie celami produktu |
| -- | -- | -- | -- |
| 1 | **Ocena istotności komunikatu** — `event_type`, `sentiment`, `analysis_score`, tiery, bramka antyhalucynacyjna | **CORE** | To jedyne miejsce, gdzie produkt robi coś, czego nie da się kupić: zamienia 30-stronicowy PDF w ocenę „warte uwagi / nie". Guardrail „no hallucinations" (`prd.md:48`) i Secondary Criterion (`prd.md:44`) mierzą dokładnie ten obszar. Skala tierów (`src/analyzer.py:27-33`) jest **redakcyjnym gustem właściciela zapisanym w kodzie** — to definicja przewagi, nie konfiguracja |
| 2 | **Dystrybucja: X-post i powiadomienia** — kompozycja wątku, cashtagi, guardy, 3 próby, wybór odbiorców | **CORE** | Primary Success Criterion (`prd.md:41`) kończy się na dostarczeniu, nie na analizie. Reguły formatu (`prd.md:121-126`) i kryteria supervisora (`prd.md:128-134`) są w PRD wypisane co do punktu — produkt sam uznał je za część rdzenia. Guard rekomendacji (`src/post_supervisor.py:9-24`) realizuje Non-goal `prd.md:151` (licencja KNF) — to ograniczenie regulacyjne, czyli rdzeń przez ryzyko |
| 3 | **Pozyskanie komunikatu** — scraper Bankiera, parser PDF/HTML, deduplikacja | **SUPPORTING** | FR-001…FR-005 są must-have, ale PRD samo traktuje sposób pozyskania jako wymienny: OQ-1 (`prd.md:156`) rozważa oficjalny feed zamiast scrapingu. Wartość jest w ocenie treści, nie w jej zdobyciu. Wyjątek: **dedup jest Core-owym niezmiennikiem**, bo Guardrail 1 (`prd.md:47`) czyni go warunkiem koniecznym |
| 4 | **Portfel i księga lotów FIFO** — pozycje, loty, podstawa kosztowa, zysk zrealizowany, import XTB | **SUPPORTING** (de facto drugi rdzeń) | Jawny Non-goal `prd.md:150`. Zarazem: to jedyny obszar z **oryginalnym modelem domenowym** w tym repo (`src/portfolio_lots.py`) i jedyny, który daje produktowi wielu użytkowników. Klasyfikuję jako Supporting **zgodnie z celami**, ale z ostrzeżeniem: jeśli intencja produktu się zmieniła, to PRD, a nie kod, jest nieaktualny — i wtedy zmienia się cały ranking KROKU 5 |
| 5 | **Notowania i statystyki spółek** — kursy zamknięcia, `company_daily_stats`, ETF/ETC/ETN, archiwum GPW | **SUPPORTING** | Nie ma o nich słowa w PRD; powstały, bo portfela nie da się wycenić bez cen. Klasyczna subdomena wspierająca: konieczna, niedyferencjująca, kupowalna. Zarazem **najlepiej rozstrzygnięta domenowo część repo** (§1D): priorytet źródeł, `Kurs odn.` jako detektor, wybór serii surowej, ETF-y jako osobny rejestr instrumentów — wszystko z pomiarami. Klasyfikacja „supporting" nie jest tu oceną jakości |
| 6 | **Słownik spółek i tickerów** | **SUPPORTING** | Klej między komunikatem, watchlistą a portfelem. `src/tickers.py` to jedyna wspólna definicja kształtu tickera dla 4 ścieżek zapisu — wartość leży w tym, że jest jedna, nie w tym, co robi |
| 7 | **Tożsamość i uprawnienia** — Firebase Auth, JWT, rola admin, weryfikacja e-mail, reset hasła | **GENERIC** | `prd.md:138` mówi wprost: *„Brak logowania użytkownika, brak UI webowego w MVP"*. Zbudowane w całości na gotowym dostawcy; zero reguł specyficznych dla giełdy |
| 8 | **Dostarczanie e-maili (SMTP)** | **GENERIC** | FR-010 (`prd.md:93`) sam zaznacza: *„Wybór dostawcy email to decyzja implementacyjna"* |
| 9 | **Parsowanie `.xlsx`, klient HTTP z retry, logging, raport kosztów GCP** | **GENERIC** | Infrastruktura. `src/brokers/xlsx_reader.py`, `src/http_client.py`, `src/logging_setup.py`, `src/cost_report.py` — żadne nie zna pojęcia giełdy |

### Jedna obserwacja, która wyjaśnia resztę dokumentu

Zestawienie klasyfikacji z jakością niezmienników daje zależność zbyt regularną, żeby była
przypadkiem:

> **Obszary, które mają zapisane wymagania, mają egzekwowane reguły. Obszar, który wymagań nie
> ma, ma reguły wyłącznie w docstringach.**

Dedup, bramka supervisora, limit 3 prób, próg 50 punktów, zakaz rekomendacji — wszystko to jest
w kodzie **twarde** (`if`, `WHERE`, `raise`, gałąź `if approved`). Wszystkie one były wcześniej
zdaniem w `prd.md` albo w `AGENTS.md`. Niezmienniki portfela — reguła prawej krawędzi, zgodność
dwóch rekonstrukcji, `avg_buy_price > 0` w bazie, dodatniość sald — są **wyłącznie prozą**.
To nie jest różnica staranności. To różnica w tym, czy ktoś kiedykolwiek zapisał regułę
w miejscu, które można sprawdzić.

---

## KROK 3 — Kandydaci na agregaty i ich niezmienniki

Konwencja statusu: **EGZEKWOWANY** (kod nie pozwala na naruszenie), **ZADEKLAROWANY** (kod
opisuje regułę w docstringu/komentarzu albo ją liczy, ale nie blokuje naruszenia),
**IGNOROWANY** (reguła istnieje w dokumencie, w kodzie nie ma jej śladu).

### A-1. `Announcement` (Komunikat) — korzeń: `announcement_id`

| Niezmiennik | Cytat źródłowy | Status | Dowód w kodzie |
| -- | -- | -- | -- |
| Ten sam komunikat nie generuje więcej niż jednego outputu, **niezależnie od liczby uruchomień schedulera** | `prd.md:104`; `AGENTS.md:13` | **ZADEKLAROWANY** | Dedup jest **oknowy, nie globalny**: `known_ids = get_processed_ids_since(now − 2×okno)` (`src/scraper.py:48-50`), a zapytanie filtruje `published_at >= @cutoff` (`db/bigquery.py:3240`). Poza oknem gwarancji nie ma. Funkcja globalna `is_processed(url)` **istnieje** (`db/bigquery.py:2195`), ale pipeline jej nie woła |
| Duplikat check jest **warunkiem koniecznym przed analizą** | `prd.md:47` | **EGZEKWOWANY** | `continue` w `src/scraper.py:110-111`, kilka warstw przed jedynym wywołaniem AI w `main.py:132` |
| Analizowane są **wszystkie** nie-duplikaty, bez filtra tytułu | `prd.md:73` (FR-003, z jawnie przyjętym kontrargumentem o false-negatives) | **IGNOROWANY** | Kod wprowadza filtr, którego PRD nie zna: brak rozwiązywalnego tickera → komunikat **nie jest w ogóle zapisywany** (`main.py:114-123`). Komentarz przyznaje, że taki komunikat nie jest też deduplikowany |
| Analiza AI jest walidowana schematem przed oceną supervisora | `AGENTS.md:35` | **EGZEKWOWANY** | `_AnalysisResponse.model_validate` (`src/analyzer.py:48-58`, wywołanie `:176`) przed `_call_gate` `:274` |
| `event_type` należy do ustalonego słownika | `src/analyzer.py:21-26` | **EGZEKWOWANY**, ale przez fallback | Nieznana wartość → `"inne"` z WARNING (`:265-270`). Typ pola pozostaje gołym `str` (`:50`) — **brak `Literal`/`Enum`** |
| Komunikat klasyfikuje się jako **finansowy albo korporacyjny** | `prd.md:111-119` | **IGNOROWANY** | Ten dwupodział nie istnieje w kodzie. Zamiast niego 15 płaskich `event_type`. Częściowym cieniem oryginału jest `NUMBER_DEPENDENT_EVENT_TYPES` (`src/post_selection.py:25`), ale to inny podział i w innej warstwie |

### A-2. `XPost` (Wątek) — korzeń: `x_post_id`

| Niezmiennik | Cytat źródłowy | Status | Dowód w kodzie |
| -- | -- | -- | -- |
| Post nie wychodzi bez zatwierdzenia supervisora | `prd.md:49`; `AGENTS.md:12` | **EGZEKWOWANY** | Publikacja i zapis siedzą **wewnątrz** gałęzi `if result.approved` (`post_main.py:328`) |
| Maksymalnie 3 próby; po trzeciej porażce alert do właściciela, komunikat odłożony | `prd.md:49`, `prd.md:87` | **EGZEKWOWANY** | `_MAX_ATTEMPTS = 3` (`post_main.py:38`), pętla `:313`; po wyczerpaniu — wiersz `x_posts` z `post_text = NULL` jako trwały ślad + `send_no_post_email` (`:361-367`) |
| Post zawiera nazwę spółki i ticker, liczby, hashtagi, mieści się w limicie, nie urywa zdania | `prd.md:129-134` | **EGZEKWOWANY** | `src/post_supervisor.py:52-94` — każda reguła osobnym blokującym `issues.append` |
| System nie rekomenduje kupna/sprzedaży | `prd.md:151` | **EGZEKWOWANY** | 12 wzorców `src/post_supervisor.py:9-24`, blokada `:83-85` |
| Maksymalnie **jeden cashtag na tweet** (inaczej X 403) | `src/post_generator.py:77-90` | **ZADEKLAROWANY** | Reguła żyje w prompcie. Supervisor **nie liczy** cashtagów w tweecie — sprawdza tylko, czy każdy jest tickerem (`src/post_supervisor.py:92-94`). Deterministycznie domykany jest wyłącznie tweet body (`src/post_generator.py:209-221`) |
| Post nie zawiera klikalnej domeny | `src/post_supervisor.py:96-102` | **ZADEKLAROWANY** | Świadomie non-blocking — trafia do `warnings`, nie wpływa na `approved` |
| Nigdy nie publikuj pustego wątku | *zasada projektu z `lessons.md`* | **EGZEKWOWANY** | Niezależny od supervisora guard `is_publishable` przed `publish()` (`post_main.py:62-84`, `:162`) |

### A-3. `TickerLedger` (Księga lotów) — korzeń: `(portfolio_id, ticker)`

Jedyny agregat w repo z **prawdziwymi granicami**: `Sale` jest bytem pierwszej klasy, a dostęp
do wolumenu sprzedaży prowadzi wyłącznie przez nią (`src/portfolio_lots.py:16-20`). To wzorcowa
robota — i dlatego warto zobaczyć, gdzie mimo to przecieka.

| Niezmiennik | Cytat źródłowy | Status | Dowód w kodzie |
| -- | -- | -- | -- |
| FIFO ma sens tylko wobec realnej kolejności czasu; przy remisie **kupno przed sprzedażą** | docstring `src/portfolio_lots.py:135-140` | **EGZEKWOWANY połowicznie** | `_sort_key` (`:133-142`) rozstrzyga wyłącznie remis buy-vs-sell. Dwa **kupna** o identycznym znaczniku zostają w kolejności z BigQuery, a `ORDER BY occurred_at` nie ma klucza wtórnego (`db/bigquery.py:4153`) |
| `uncovered` nigdy nie jest ujemne | komentarz `src/portfolio_lots.py:206-208` | **EGZEKWOWANY** | `uncovered=remaining if remaining > DUST else 0.0` (`:208`) |
| „Brak lotu" nie może zapaść się w zerową podstawę — zero czyta się jako 100% zysku | docstring `src/portfolio_lots.py:26-28`, powtórzone `:234-235`, `:274-275`, `:325-327` | **EGZEKWOWANY** | Trzy rozłączne gałęzie w `basis_segments` (`:321-329`); brak podstawy → `continue`, czyli brak segmentu, nigdy zero |
| Sprzedaż w jednym portfelu **nie może skonsumować lotu z drugiego** | docstring `src/portfolio_lots.py:266-268` | **EGZEKWOWANY** | Kluczowanie per portfel w `basis_segments` (`:289`) i `portfolio_id` na każdym wierszu (`db/bigquery.py:4147`) |
| Księga **raportuje** to, czego nie pokryła; **nie rozstrzyga**, co z tym zrobić | docstring `src/portfolio_lots.py:25-28` | **EGZEKWOWANY jako granica** | I to jest sedno: `uncovered` wychodzi na zewnątrz, a decyzji nie ma **nigdzie** — patrz A-4 |

### A-4. `Portfolio` / `Position` (Portfel i pozycja) — korzeń: `(user_id, portfolio_id)`

**To jest agregat, którego nie ma.** Nie istnieje typ, funkcja ani moduł, który trzymałby razem
pozycję, jej loty i jej saldo. Jest tabela, są dwie ścieżki zapisu i jest pięć miejsc odczytu.

| Niezmiennik | Cytat źródłowy | Status | Dowód w kodzie |
| -- | -- | -- | -- |
| **Reguła prawej krawędzi**: ostatni dzień historii musi równać się dzisiejszemu stanowi posiadania i dzisiejszej podstawie | docstring nazywa to **wprost „the PUL-100 right-edge invariant"** — `db/bigquery.py:430` oraz `:883-885` | **ZADEKLAROWANY** | Docstring mówi, że trzyma się *„by construction rather than by care"*. Zweryfikowano go **raz, ręcznie, w chwili wdrożenia** — delta +0,00 na pięciu portfelach (`context/archive/2026-07-30-calendar-holdings-as-of-date/change.md`). Od tamtej pory nic go nie sprawdza: testy PUL-100 badają **kształt koperty** (`tests/test_bigquery.py:1812`) i **treść SQL-a jako stringa** (`:1925-1961`), a e2e podmienia całe `get_portfolio_history` (`tests/e2e/conftest.py:446`). Reguła przeszła z „sprawdzona" w „zapamiętana" |
| Obie rekonstrukcje stanu posiadania muszą dawać **ten sam wynik co do sztuki** | `context/changes/portfolio-lots-flow/research.md:183` | **ZADEKLAROWANY** | Rekonstrukcja SQL wstecz (`db/bigquery.py:944-960`) i FIFO wprzód w Pythonie (`src/portfolio_lots.py:260`) spotykają się w jednej funkcji, w jednym zapytaniu (`db/bigquery.py:924` vs `:944`) — licznik i mianownik P&L pochodzą z dwóch implementacji. **Nic nie porównuje ich wyników** |
| `shares > 0` i `avg_buy_price > 0` | `src/api.py:1149` | **EGZEKWOWANY tylko na jednej ścieżce** | Warunek jest w handlerze HTTP, nie w modelu: `PortfolioPositionIn` (`src/api.py:588-593`) nie ma **ani jednego** `Field(gt=…)`. Schemat BQ deklaruje `avg_buy_price` jako `REQUIRED` (`db/bigquery.py:1267`), co blokuje NULL, ale **nie zero i nie liczbę ujemną** |
| Ujemne saldo gotówki nie może wyglądać jak krótka pozycja | docstring `src/api.py:516-518` | **EGZEKWOWANY** | `max(0.0, …)` w `_cash_position` (`src/api.py:525`) |
| Saldo **niepodane** i saldo **podane jako zero** to dwa różne fakty | docstring `src/api.py:507-515` | **EGZEKWOWANY** | `None` → brak wiersza; `0.0` → wiersz `_CASH` z zerem. Docstring tłumaczy, że zlanie ich w jedno pozostawiało wydaną gotówkę w portfelu na zawsze |
| Import nie może skasować pozycji, której eksport nie widzi | docstring `db/bigquery.py:1355-1357` | **EGZEKWOWANY** | Świadomy **brak** gałęzi `WHEN NOT MATCHED BY SOURCE` |
| Stan posiadania musi dać się objaśnić operacjami: `\|today_shares − Σ(±volume)\| ≤ DUST` | docstring `db/bigquery.py:369-380` | **ZADEKLAROWANY — i to jest najciekawszy przypadek w całym repo** | Reguła jest **policzona** (`residual_holders`, `db/bigquery.py:1120` i `:679`), **nazwana** (*„holding(s) not explained by broker operations"*, `:387-392`) i **zalogowana na poziomie, który produkcja widzi** (`:386`, INFO, bo DEBUG nie opuszcza Cloud Run). System wie, że reguła jest łamana, mówi o tym w logu i **nie robi z tym nic** |

### A-5. Agregaty peryferyjne

| Agregat | Korzeń | Najważniejszy niezmiennik | Status |
| -- | -- | -- | -- |
| `Company` | `ticker` | Ticker musi mieć kształt tickera na 4 ścieżkach zapisu | **EGZEKWOWANY** — `src/tickers.py:24-31` + walidacja w `db/bigquery.py:2114-2116`, `:2163-2167`, `:2265-2270` |
| `NotificationSubscription` | `(user_id, announcement_id)` | Jeden e-mail na parę użytkownik–komunikat | **EGZEKWOWANY** — `NOT EXISTS` po obu stronach (`db/bigquery.py:4341-4344`, `:4368-4376`) |
| `Watchlist` | `(user_id, ticker)` | Ticker musi być znany | **EGZEKWOWANY** — HTTP 422 w `src/api.py:892-894` |

---

## KROK 4 — Rozjazdy MODEL vs KOD

Uporządkowane od najgroźniejszego. **Groźny** znaczy tu: wiedza domenowa istnieje, kod jej nie
odwzorowuje, a skutkiem jest liczba, która wygląda poprawnie i jest nieprawdziwa.

### Tabela zbiorcza

| # | Dokument / model mówi | Kod robi | Dowód |
| -- | -- | -- | -- |
| **R-01** | Stan posiadania w dniu X to jedna wielkość domenowa | Liczy ją **dwa razy, dwoma algorytmami, w dwóch językach**, w jednym zapytaniu — i nie porównuje wyników | `db/bigquery.py:924` (Python FIFO) vs `:944-960` (korekta SQL wstecz) |
| **R-02** | Akcje korporacyjne istnieją i są nazwane w 4 miejscach | **Zero** pojęcia zdarzenia korporacyjnego w portfelu; nieznana etykieta staje się polskim stringiem w `warnings` | `src/brokers/xtb.py:88-100`, `:139`; docstringi `src/portfolio_lots.py:24`, `db/bigquery.py:425`, `:1355-1357`, `src/api.py:386` |
| **R-03** | „Pozycja" to jedno pojęcie z jedną tabelą i jednym modelem | Kod **czterokrotnie** zachowuje się inaczej wobec pozycji bez lotów, nigdy jej nie nazywając | `src/api.py:621-623`; `db/bigquery.py:886-888`; `:371-392`; `src/portfolio_realized.py:49` |
| **R-04** | Portfel to **Non-goal**, do v3+ | Portfel to większość kodu i 3 z 4 ostatnich miesięcy pracy | `prd.md:150` vs `context/map/repo-map.md:73-75` |
| **R-05** | Bramka supervisora działa **na komunikacie**: 3 próby z uwagami, potem odłożenie i alert | Na komunikacie: **jedno** wywołanie, brak pętli, odrzucenie zapisywane i porzucane. Trzy próby przeniosły się na X-post | `prd.md:87` i `AGENTS.md:12` vs `src/analyzer.py:274-298`; migracja udokumentowana `db/bigquery.py:14` |
| **R-06** | Nazwa produktu to „analizator **ESPI/EBI**" | Rozróżnienie ESPI/EBI jest odczytywane i **wyrzucane** — z bazy nie da się zapytać „czy to był EBI" | `src/scraper.py:100-103` vs `db/bigquery.py:2207-2212` i `_SCHEMA` `:56-75` |
| **R-07** | Właściciel dostaje e-mail z X-postem w 15 min od publikacji komunikatu | Per komunikat wychodzi **digest do obserwujących**; X-post powstaje w **osobnym jobie**, z top-N w oknie pory dnia | `prd.md:33`, `:61`, FR-010 `:93` vs `main.py:150-173` i `post_main.py:192-200`, `:313` |
| **R-08** | Komunikat jest **finansowy albo korporacyjny** (dwa tryby ekstrakcji) | 15 płaskich `event_type`; dwupodziału nie ma nigdzie | `prd.md:111-119` vs `src/analyzer.py:21-26` |
| **R-09** | Ten sam komunikat nie da outputu dwa razy, **niezależnie od liczby uruchomień** | Dedup jest **oknowy** (2× okno scrapowania); poza oknem gwarancji brak | `prd.md:104` vs `src/scraper.py:48-50` |
| **R-10** | Analizuj wszystko, co nie jest duplikatem — bez filtra | Brak rozwiązywalnego tickera → komunikat **nie jest w ogóle zapisywany** ani deduplikowany | `prd.md:73` vs `main.py:114-123` |
| **R-11** | Skala istotności to wiedza produktowa | 33 tickery zahardkodowane w module analizy — w tym **portfel właściciela** | `roadmap.md:153` („T1 **portfel**") vs `src/analyzer.py:27-33` |
| **R-12** | „Portfel" to jedno pojęcie | Dwa byty pod jednym słowem: `user_portfolios` (loty, bez waluty) i `portfolio_snapshots` (waluta, bez lotów) | `db/bigquery.py:1674-1681` vs `:202-214` |
| **R-13** | Reguła prawej krawędzi to **niezmiennik** (tak nazwany) | Nazwany dwa razy w docstringach, nie sprawdzany ani razu | `db/bigquery.py:430`, `:883-885` vs `tests/test_bigquery.py:1812`, `:1925-1961` |

### R-02 rozwinięte — czym staje się lot po splicie?

To pytanie zadano tej destylacji wprost. Odpowiedź brzmi: **niczym. Lot zostaje dokładnie taki,
jaki był — z przedrozłamową liczbą sztuk i przedrozłamową ceną.** Poniżej dowód i, ważniejsze,
konsekwencja, której się nie spodziewałem.

**Po stronie portfela pojęcia nie ma w ogóle.** `_TYPE_MAP` (`src/brokers/xtb.py:88-100`) zna
11 etykiet brokera; komentarz nad nim mówi, że to słownik *zaobserwowany w całości* na dwóch
prawdziwych eksportach (573 wiersze). Żadna z 11 nie dotyczy splitu, scalenia ani asymilacji.
Nieznana etykieta wychodzi jako polski string w `warnings` (`:139`) i **nie ma ani jednego testu**
— `grep "Nieznany typ" tests/` daje zero (potwierdzone w `research.md:349`). `build_ledger`
odfiltrowuje wszystko poza `buy`/`sell` już na wejściu (`src/portfolio_lots.py:157-161`), więc
zdarzenie korporacyjne nie dociera nawet do księgi.

**Po stronie komunikatów słownik akcji korporacyjnych już istnieje.** I to jest najmocniejsza
obserwacja tej sekcji. `_VALID_EVENT_TYPES` (`src/analyzer.py:21-26`) zawiera `dywidenda`,
`emisja_akcji`, `skup_akcji`, `przejecie`, `fuzja`, `wezwanie` — sześć z piętnastu wartości to
zdarzenia korporacyjne, każde z własną wagą istotności (`:37-43`). **Produkt umie rozpoznać
akcję korporacyjną — po prostu robi to w kontekście, który nie ma dostępu do lotów.**

**Split jest przy tym niewidzialny nawet dla tej połowy produktu, która widzi resztę.** Splitu,
scalenia ani asymilacji nie ma wśród 15 wartości, więc taki komunikat dostaje `"inne"`
(`src/analyzer.py:270`) → 20 punktów (`:42`) → odpada podwójnie: `event_type != 'inne'`
**oraz** `analysis_score >= 50` (`db/bigquery.py:2377-2379`, próg `post_main.py:48`). Komunikat
o splicie spółki, którą użytkownik trzyma w portfelu, jest dla tego produktu strukturalnie
niewidoczny **z obu stron naraz**.

**Archiwum potwierdza, że to nie jest odłożona decyzja — to decyzja nigdy niepodjęta.** Przeszukanie
79 zamkniętych zmian pod kątem `split` (jako akcji korporacyjnej), `scalenie`, `asymilacj`,
`prawo poboru`, `denominacj` daje **zero** zmian, których tematem byłaby korekta liczby akcji.
Termin *corporate action* pada w archiwum wyłącznie w `2026-07-27-official-close-source`
i wyłącznie jako powód, dla którego **nie wolno ufać cudzym danym** (`Kurs odn.`, różnicowanie
zamknięć) — nigdy jako coś, co dzieje się z portfelem użytkownika. Świadomie odłożono natomiast
dwie wąskie manifestacje tematu, obie z nazwaniem i uzasadnieniem:

- **Dywidendy rzeczowe / spin-offy** — `context/archive/2026-07-29-xtb-portfolio-import/change.md`
  nazywa to *ślepą plamką formatu*: S2B (Syn2Bio, 4 szt.) otrzymane za posiadanie Synektika nie
  występuje w eksporcie *„pod żadnym tickerem ani nazwą — bo nie jest ani ruchem gotówki, ani
  pozycją zamkniętą"*, a *„to samo powtórzy się z Shoperem i Cyber_Folks, więc jest to **stała
  właściwość, nie przypadek brzegowy**"*. Przyjęta droga naprawy: **ręcznie**, a widok świadomie
  nazwano „dywidendy gotówkowe" (`plan.md:84`).
- **Instrumenty walutowe** — parser je pomija (11 operacji, wszystkie na pozycjach zamkniętych),
  a `change.md` tej samej zmiany odnotowuje, że *„parser potrzebuje polityki"*. Polityka nie
  została napisana.

**Jedyna akomodacja, jaka istnieje, jest obronna, nie modelowa.** Import świadomie nie kasuje
pozycji nieobecnych w pliku (`db/bigquery.py:1355-1357`), a uzasadnienie nazywa konkretne
zdarzenie: *„a physical dividend like S2B never appears in a broker export"* — dywidenda rzeczowa.
`src/api.py:386` mówi o *„S2B and the spin-offs to come"*. Rekonstrukcja stanu posiadania celowo
idzie **wstecz**, właśnie po to, żeby nie skasować tego, czego operacje nie tłumaczą
(`db/bigquery.py:422-430`). To wszystko są mechanizmy, które pozwalają akcji korporacyjnej
**przetrwać**, ale żaden z nich jej nie **rozumie**.

**Konsekwencja, czyli dlaczego oba tropy to jeden problem.** Po splicie eksport brokera nie
niesie zdarzenia, więc `reconstruct_positions` (`src/brokers/xtb.py:185`) odtwarza pozycję
**przedrozłamową** — zarówno liczbę sztuk, jak i cenę. Jedyna droga naprawy dostępna
użytkownikowi to ręczne nadpisanie stanu przez `POST /api/portfolio/positions`. Ale ta ścieżka
pisze wyłącznie do `user_portfolio_positions` i **nie tyka operacji** — więc pozycja zostaje
z poprawionym stanem i nieaktualnymi lotami, czyli z niezerowym residuum. Innymi słowy:

> **Jedyne dostępne lekarstwo na akcję korporacyjną zamienia pozycję z lotami w pozycję,
> której loty nie tłumaczą. Trop 1 produkuje trop 2.**

I system to widzi: `residual_holders` (`db/bigquery.py:1120`) policzy tę pozycję, a
`_log_unexplained_holdings` (`:371-392`) napisze o niej w logu produkcyjnym. Zgodnie z granicą
z `src/portfolio_lots.py:25-27` — *raportuje, nie rozstrzyga* — i nikt tego logu nie czyta jako
sygnału o błędnej wycenie.

### R-03 rozwinięte — czy pozycja bez lotów to ta sama rzecz co pozycja z lotami?

Pytanie postawiono jako pytanie o pojęcia. Odpowiedź jest jednoznaczna i **kod już jej udzielił**
— tylko czterokrotnie, mimochodem i nigdzie jej nie zapisując.

Formalnie oba byty są tym samym: jedna tabela `user_portfolio_positions`, jeden model
`PortfolioPositionOut` (`src/api.py:602-623`), jedna ścieżka odczytu. Zachowaniowo różnią się
w czterech obserwowalnych miejscach:

| # | Zachowanie | Pozycja z lotami | Pozycja bez lotów | Dowód |
| -- | -- | -- | -- | -- |
| 1 | Data nabycia / okres posiadania | data najstarszego otwartego lotu | **zawsze `None`** — kolumna okresu posiadania jest pusta | `src/api.py:621-623` — komentarz mówi wprost: *„None when no operation explains the position, which a hand-entered one never does"* |
| 2 | Podstawa kosztowa w historii | funkcja schodkowa `BasisSegment` | **nie ma żadnego segmentu**; wyceniana zapisanym `avg_buy_price` na każdy dzień wstecz | `db/bigquery.py:886-888`; `src/portfolio_lots.py:321-322` |
| 3 | Zysk zrealizowany | uczestniczy | **nie istnieje** — `/api/portfolio/realized` czyta wyłącznie operacje | `src/portfolio_realized.py:49`; `db/bigquery.py:4147-4153` |
| 4 | Status w rekonciliacji | residuum ≈ 0 | liczona jako **„holding not explained by broker operations"** i logowana na produkcji | `db/bigquery.py:1120`, `:371-392` |

**I to jest zmierzone na produkcji.** `context/archive/2026-07-30-calendar-holdings-as-of-date/plan.md:32-41`
zawiera pomiar z realnego BigQuery: rekonstrukcja `SUM(±volume)` z operacji zbiega do
`user_portfolio_positions.shares` **co do czwartego miejsca po przecinku dla każdego tickera
akcyjnego we wszystkich pięciu portfelach**. Rozjazd występuje w dokładnie trzech wierszach —
dwa razy `_CASH` (2 160,11 i 84,03) i raz pozycja wpisana ręcznie (`XTB`, 1,0 szt.). Innymi
słowy: **niezmiennik dziś się trzyma, a jego jedyne naruszenia to dokładnie te dwa pojęcia,
których model nie nazywa.** To najlepszy możliwy moment na zapisanie reguły — nie po awarii,
tylko wtedy, gdy znane wyjątki dają się jeszcze wyliczyć na palcach.

**Projekt ma zresztą na ten temat spójną doktrynę — po prostu rozproszoną po ośmiu zmianach.**
Najczystsze jej sformułowanie to sekcja *„Absence is not zero"*
(`context/archive/2026-08-05-holding-period-column/change.md:44-49`): *„A hand-entered position
has no operations, so `first_buy_date` is `null` and there is no holding period. It renders as
the existing neutral `—`, never `0 dni` and never today."* Do tego: pozycja bez lotów nigdy nie
jest wyceniana po zerze, nigdy nie jest kasowana przez import, a jej brak pokrycia jest
raportowany, nie pochłaniany. **Czego w archiwum nie ma: ani jednej zmiany, która ten rozjazd
by *usuwała*** — nie ma rekoncyliacji operacje-vs-pozycje, nie ma sposobu dopisania lotu do
pozycji ręcznej, nie ma ostrzeżenia w UI (świadomie: log i skrypt). Jest za to otwarty,
nazwany dług pieniężny: rozbieżność **−4 482 PLN** wobec wyciągu XTB, wydzielona jako **PUL-124**
z trzema kandydatami na przyczynę (`context/archive/2026-08-05-fifo-basis-on-read-paths/research.md:183-188`).

Cztery różnice zachowania to nie wariant jednego pojęcia. To **dwa pojęcia dzielące jeden typ**.
Docstring `src/portfolio_lots.py:25-28` odmawia rozstrzygnięcia świadomie i **ma rację na swoim
poziomie** — księga lotów rzeczywiście nie może wiedzieć, czym jest pozycja, której nie widzi.
Ale odmowa jest tam prawidłowa dlatego, że decyzja należy do **warstwy wyżej**: do agregatu
portfela. Problem polega na tym, że tej warstwy nie ma (A-4), więc odmowa księgi jest ostatnim
słowem w całym systemie.

Nazwa dla brakującego rozróżnienia już zresztą w repo istnieje — powstała jako komunikat logu:
**„posiadanie nieobjaśnione operacjami"** (`db/bigquery.py:387-388`). Jedyne, czego brakuje, to
podniesienie jej z poziomu stringa w logu do poziomu pojęcia w modelu.

### R-04 rozwinięte — dlaczego to nie jest zarzut wobec projektu

`prd.md` ma `version: 1`, `status: draft` i datę 2026-05-18. Od tego czasu zbudowano **trzy z
pięciu jego Non-goals**: panel webowy (`prd.md:149`), automatyczną publikację na X
(`prd.md:148`) i portfele (`prd.md:150`). Dokument nigdy nie został zaktualizowany.

To nie jest niechlujstwo — to jest **normalny los dokumentu wymagań w produkcie, który znalazł
użytkownika gdzie indziej, niż zakładał**. Odnotowuję to jako rozjazd, bo ma jedną bardzo
konkretną, mierzalną konsekwencję, opisaną na końcu KROKU 2: obszary z zapisanymi wymaganiami
mają twarde niezmienniki, a obszar bez wymagań ma je wyłącznie w prozie. **Portfel nie jest
gorzej napisany od reszty. Portfel jest jedyną częścią produktu, dla której nikt nigdy nie
zapisał, co musi być prawdą.**

---

## KROK 5 — Ranking refaktoru

Oceniam dwiema osiami. **Wartość** = jak rdzeniowy jest niezmiennik: ile pojęć od niego zależy
i czy jego naruszenie jest widoczne. **Ryzyko** = jak słabo jest dziś egzekwowany: czy istnieje
cokolwiek, co zatrzyma naruszenie przed produkcją.

| # | Kandydat na agregat | Niezmiennik do wprowadzenia | Wartość | Ryzyko | Uzasadnienie |
| -- | -- | -- | -- | -- | -- |
| **1** | **`Portfolio` / `Position`** — agregat, którego nie ma | Pozycja objaśniona operacjami i pozycja zadeklarowana ręcznie to **dwa różne byty**; `\|today_shares − Σ(±volume)\| ≤ DUST` musi być sprawdzalne, nie tylko logowane | **najwyższa** | **najwyższe** | Zbiega się tu wszystko: R-02, R-03 i R-04. Naruszenie jest **ciche i pieniężne** — zła podstawa kosztowa to zły P&L na wykresie, w kafelku i w treemapie naraz. Reguła jest **już policzona** (`db/bigquery.py:1120`) i **już nazwana** (`:387-388`) — brakuje wyłącznie miejsca, do którego mogłaby należeć |
| **2** | **`Portfolio.history`** — zgodność dwóch rekonstrukcji | Rekonstrukcja SQL wstecz i FIFO wprzód muszą dawać ten sam stan posiadania; reguła prawej krawędzi ma być testem, nie zdaniem | wysoka | **najwyższe** | Connascence znaczenia Python↔SQL na maksymalnym dystansie; ani typy, ani `tach`, ani kompilator nie pomogą. Rozjazd o jedną sztukę nie wywali żadnego testu — objawi się jako krzywa, która „trochę nie gra". Numer 2, a nie 1, bo **oba kierunki są świadome i udokumentowane** (`db/bigquery.py:414-440`): to nie jest bałagan do posprzątania, tylko brakujący dowód równoważności |
| **3** | **`CorporateAction`** — pojęcie, którego nie ma nigdzie | Zdarzenie zmieniające liczbę sztuk lub cenę **bez przepływu gotówki** musi mieć reprezentację; nieznana etykieta brokera nie może być stringiem w `warnings` | wysoka | średnie | Wartość jest wysoka, bo split cicho psuje podstawę kosztową — tym bardziej że projekt świadomie wybrał **surowe, nieskorygowane kursy** (§1D), co przenosi obowiązek korekty na liczbę akcji. Ryzyko klasyfikuję jako średnie, nie najwyższe, z dwóch powodów: mechanizmy obronne (`db/bigquery.py:1355-1357`, korekta wstecz) sprawiają, że dane **przeżywają**, a produkt ma już połowę rozwiązania — słownik zdarzeń korporacyjnych w `src/analyzer.py:21-26`. To refaktor **łączący dwa konteksty**, więc nie powinien iść przed uporządkowaniem tego, do czego miałby się podłączyć (#1) |
| **4** | **`LotEvent`** — determinizm i waluta | Kolejność zdarzeń musi być totalna (klucz wtórny do `ORDER BY`); instrument musi nieść walutę albo być jawnie odrzucony | średnia | średnie | Dwa kupna w tej samej mikrosekundzie mogą dać inną podstawę przy dwóch odświeżeniach strony (`db/bigquery.py:4153`). Tanie do naprawy, ale warunek aktywacji jest `unknown` — nie wiem, czy takie pary istnieją na produkcji |
| **5** | **`Announcement`** — dedup i tożsamość ESPI/EBI | Dedup globalny zamiast oknowego; `source` i `espi_code` zapisywane | średnia | niskie | R-06 i R-09 są prawdziwe, ale konsekwencją jest **duplikat e-maila albo brak wymiaru raportowego**, nie zła liczba. Nazwa produktu obiecuje przy tym rozróżnienie, którego baza nie potrafi zwrócić — to dług **znaczeniowy**, nie finansowy |
| **6** | **`Announcement.event_type` / `sentiment` / `window` / `x_publish_status`** — słowniki jako typy | Zamknięte słowniki mają być `Literal`/`Enum`, nie gołym `str` + zbiorem modułowym | niska | niskie | Kosmetyka o realnej wartości dla czytelnika, ale każdy z tych słowników ma już fallback albo walidację (`src/analyzer.py:265-270`, `:55-58`), więc naruszenie jest głośne, nie ciche |

### #1 do refaktoru: agregat `Portfolio` / `Position`

**Dlaczego ten, a nie zgodność rekonstrukcji (#2), która brzmi groźniej.**

Bo #2 jest **objawem braku #1**. Dwie rekonstrukcje stanu posiadania istnieją dlatego, że nie
ma jednego miejsca, które byłoby właścicielem odpowiedzi na pytanie *„ile sztuk było tego dnia
i po jakim koszcie"*. Odpowiedź policzona w SQL-u i odpowiedź policzona w Pythonie spotykają się
w `db/bigquery.py:829`, bo nie mają gdzie indziej się spotkać. Dopisanie testu porównującego je
byłoby prawdziwym zyskiem — i zostawiłoby przyczynę nietkniętą.

**Trzy powody, dla których to jest jedyny sensowny punkt startu:**

1. **Zbiega się tu każdy trop tej destylacji.** Akcja korporacyjna (R-02) trafia dokładnie tam,
   gdzie pozycja przestaje być objaśniona operacjami (R-03), a jedyna dostępna naprawa jednego
   produkuje drugie. To nie są dwa problemy do zaplanowania osobno; to jedno pojęcie, którego
   brakuje.
2. **Reguła jest już policzona i już nazwana.** `residual_holders` (`db/bigquery.py:1120`) liczy
   dokładnie ten niezmiennik. `_log_unexplained_holdings` (`:371-392`) nadaje mu nazwę i wypisuje
   go na produkcji, świadomie na poziomie INFO, żeby *„zauważyli to my, a nie użytkownik"*.
   To nie jest refaktor od zera — to **podniesienie istniejącego pomiaru do rangi reguły**.
3. **Koszt naruszenia jest pieniężny i niewidoczny.** Wszystkie pozostałe długi objawiają się
   jako brakująca funkcja, wolniejsze zapytanie albo gorzej czytelny kod. Ten objawia się jako
   **liczba pieniędzy, która wygląda dobrze i jest nieprawdziwa**, powielona jednocześnie
   w czterech powierzchniach UI. W backlogu stoi już zresztą nazwany dług tej klasy: **PUL-124**,
   rozbieżność −4 482 PLN wobec wyciągu brokera, do dziś bez ustalonej przyczyny.
4. **Moment jest optymalny, a nie awaryjny.** Pomiar z 2026-07-30 pokazał, że reguła **dziś się
   trzyma**, a jej jedyne trzy naruszenia to dwie gotówki i jedna pozycja ręczna. Zapisanie
   niezmiennika, którego znane wyjątki da się jeszcze wyliczyć na palcach, jest tanie. Zapisanie
   go po tym, jak pierwszy split rozjedzie kilkanaście pozycji naraz — nie jest.

**Czego ten refaktor NIE powinien zrobić.** Nie powinien znosić granicy z
`src/portfolio_lots.py:25-28`. Ta granica jest poprawna: księga lotów naprawdę nie może wiedzieć,
czym jest pozycja, której nie widzi, i próba nauczenia jej tego cofnęłaby najlepszą decyzję
projektową w tym repozytorium. Rzecz w tym, żeby **dać jej rozmówcę** — warstwę, która ma prawo
podjąć decyzję, której księga słusznie odmawia. Dziś tej warstwy nie ma i dlatego odmowa księgi
jest ostatnim słowem systemu.

---

## Podsumowanie w jednym zdaniu

Ten produkt ma dwa modele domenowe: jeden **zapisany w wymaganiach i egzekwowany w kodzie**
(komunikat → ocena → dystrybucja) i drugi **zapisany w docstringach i nieegzekwowany nigdzie**
(portfel → loty → wycena) — a wszystkie trzy zbadane tropy okazały się jednym pytaniem, którego
nikt nigdy nie zapisał: *czym jest pozycja, której operacje nie tłumaczą.*
