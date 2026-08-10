---
title: Niezmiennik i agregat-strażnik — plan refaktoru
created: 2026-08-08
type: refactor-plan
---

# Niezmiennik i agregat-strażnik — plan refaktoru

> **Czym jest ten dokument.** Planem jednego refaktoru: wybraniem **jednej** reguły domenowej,
> która musi być zawsze prawdziwa, pokazaniem, że dziś nie jest egzekwowana nigdzie, i
> zaprojektowaniem agregatu, który staje się jej jedynym strażnikiem.
>
> **Wejście.** `context/domain/01-domain-distillation.md` (Ubiquitous Language, klasyfikacja
> Core/Supporting/Generic, 7 kandydatów na agregaty, 13 rozjazdów). Nie wyprowadzam tego na nowo —
> buduję na tym i w dwóch miejscach **koryguję**.
>
> **Rygor cytowania.** Każdy `plik:linia` w tym dokumencie został otwarty i sprawdzony
> 2026-08-08 na commicie `e4b2066`. Tam, gdzie destylacja podała zakres, który po sprawdzeniu
> wyszedł inaczej, podaję zakres sprawdzony i odnotowuję różnicę.
>
> **Czego tu nie ma.** Kodu produkcyjnego. Wszystkie sygnatury i ciała funkcji poniżej są
> pseudokodem projektowym.

---

## KROK 0 — Kontekst: gdzie w tym systemie mieszka logika biznesowa

Ustalenia z destylacji, które są tu warunkiem wstępnym i które potwierdziłem niezależnie:

| Warstwa | Plik | Co realnie w niej siedzi |
| -- | -- | -- |
| Wejścia (7 jobów + 1 serwis) | `main.py`, `post_main.py`, `api_main.py`, … | orkiestracja; `main.py:114-123` trzyma jednak **filtr domenowy** |
| API / kompozycja | `src/api.py` (1568 linii, 32 trasy) | **walidacja domenowa** — `src/api.py:1148-1149` |
| Domena „czysta" | `src/portfolio_lots.py`, `src/portfolio_realized.py`, `src/post_supervisor.py`, `src/brokers/xtb.py` | jedyne moduły bez I/O; `src/portfolio_lots.py:31-33` importuje wyłącznie stdlib |
| Persystencja **+ druga domena** | `db/bigquery.py` (4493 linie) | reguły domenowe zapisane w SQL — `db/bigquery.py:2376-2379`, `:944-960`, `:1118-1120` |
| Frontend | `static/index.html` (6326 linii) | walidacja formularza — `:4997`, `:5010`, `:4580`, `:4595` |

Dokumenty wymagań: `context/foundation/prd.md` (`version: 1`, `status: draft`, `created: 2026-05-18`),
`AGENTS.md`, `roadmap.md`, plus `context/archive/` (79 zamkniętych zmian) jako rejestr decyzji.
`README.md` w korzeniu ma 0 bajtów.

**Runner testowy.** `pytest` skonfigurowany w `pyproject.toml:41-45` (`pythonpath = ["."]`),
uruchamiany `uv run pytest` (`AGENTS.md:44`). 47 plików w `tests/`, e2e w `tests/e2e/`.
Osobno: 14 skryptów round-trip `scripts/test_bq_*.py`, z których **żaden nie biegnie w CI**
(`context/changes/refactor-opportunities/research.md:59`). To rozróżnienie jest w KROKU 5
load-bearing: część niezmiennika daje się przypiąć czystym `pytest`, część **strukturalnie nie**.

---

## KROK 1 — Niezmienniki: co w tej domenie musi być zawsze prawdziwe

Konwencja statusu (za destylacją):
**EGZEKWOWANY** — kod nie pozwala na naruszenie;
**ZADEKLAROWANY** — kod opisuje regułę albo ją *liczy*, ale nie blokuje naruszenia;
**IGNOROWANY** — reguła wynika z modelu, w kodzie nie ma jej śladu.

### 1A. Komunikat i jego ocena (Core)

| id | Niezmiennik | Źródło | Status | Dowód |
| -- | -- | -- | -- | -- |
| **N-A1** | Sprawdzenie duplikatu jest **warunkiem koniecznym przed** jakimkolwiek wywołaniem AI | `prd.md:47` (Guardrail 1), `AGENTS.md:13` | **EGZEKWOWANY** | `continue` w `src/scraper.py:110-111`, kilka warstw przed jedynym `analyze_announcement` w `main.py:132` |
| **N-A2** | Ten sam komunikat nie generuje więcej niż jednego outputu, **niezależnie od liczby uruchomień schedulera** | `prd.md:104` | **EGZEKWOWANY warunkowo** — patrz korekta poniżej | `src/scraper.py:46-50` + `db/bigquery.py:3233-3248` |
| **N-A3** | Output AI jest walidowany schematem **przed** oceną bramki | `AGENTS.md:35` | **EGZEKWOWANY** | `_AnalysisResponse.model_validate` (`src/analyzer.py:48`, wywołanie `:176`) poprzedza `_call_gate` (`:274`) |
| **N-A4** | `event_type` należy do zamkniętego słownika 15 wartości | `src/analyzer.py:21-26` | **EGZEKWOWANY przez fallback** | nieznana wartość → `"inne"` + WARNING (`src/analyzer.py:265-270`); typ pola pozostaje gołym `str` (`:52`) |
| **N-A5** | Komunikat bez rozwiązywalnego tickera nie trafia do bazy | `main.py:114-123` (reguła **powstała w kodzie**) | **EGZEKWOWANY** | `continue` przed `insert_announcement` (`main.py:118-124`) — sprzeczne z `prd.md:73` (FR-003 „bez filtra") |

> **Korekta destylacji nr 1.** Destylacja (`01-domain-distillation.md:261`) klasyfikuje N-A2 jako
> ZADEKLAROWANY, argumentując, że „dedup jest oknowy, nie globalny". Sprawdziłem arytmetykę okien
> i **to jest zbyt surowa ocena**. Scraper w ogóle nie rozważa pozycji starszych niż okno:
> `if item_dt < cutoff: continue` (`src/scraper.py:86-87`), gdzie `cutoff = now − window`
> (`:47`). Zbiór znanych ID pokrywa natomiast `now − 2×window` (`:48-50`). Okno dedupu jest więc
> **ścisłym nadzbiorem** okna kandydatów: każdy komunikat, który w ogóle może być kandydatem,
> jest w zasięgu sprawdzenia. Reguła trzyma się strukturalnie, nie przez szczęście.
>
> Realna, węższa luka jest gdzie indziej: `announcement_id` to hash **URL-a**
> (`db/bigquery.py:2198`), więc ten sam komunikat opublikowany pod dwoma URL-ami to dwa byty.
> To jest reguła o **tożsamości komunikatu**, nie o dedupie, i jej kosztem jest jeden nadmiarowy
> e-mail — nie zła liczba.

### 1B. Dystrybucja: X-post i powiadomienia (Core)

| id | Niezmiennik | Źródło | Status | Dowód |
| -- | -- | -- | -- | -- |
| **N-B1** | Post nie wychodzi bez zatwierdzenia supervisora | `prd.md:49`, `AGENTS.md:12` | **EGZEKWOWANY** | zapis, publikacja i e-mail siedzą **wewnątrz** `if result.approved:` (`post_main.py:328-344`) |
| **N-B2** | Maksymalnie 3 próby; po trzeciej — trwały ślad + alert do właściciela | `prd.md:49`, `:87` | **EGZEKWOWANY** | `_MAX_ATTEMPTS = 3` (`post_main.py:38`), pętla `:313`, wyjście `:361-367` (`save_x_post(..., None, ...)` + `send_no_post_email`) |
| **N-B3** | Nigdy nie publikuj pustego / bezsubstancjalnego wątku | zasada projektu (`lessons.md`) | **EGZEKWOWANY** | `is_publishable` (`post_main.py:62-84`), wołany **niezależnie od supervisora** przed `publish_thread` (`:161-164`, publikacja `:172`) |
| **N-B4** | System nie rekomenduje kupna/sprzedaży | `prd.md:151` (Non-goal — licencja KNF) | **EGZEKWOWANY** | 12 wzorców `src/post_supervisor.py:9-23`, blokujące `issues.append` `:83-85` |
| **N-B5** | Maksymalnie **jeden cashtag na tweet** (inaczej X zwraca 403) | `src/post_generator.py:77-90` | **ZADEKLAROWANY** | supervisor sprawdza tylko, czy cashtag *jest tickerem* (`src/post_supervisor.py:92-94`) — **nie liczy** ich w tweecie; deterministycznie domykany jest wyłącznie tweet body (`src/post_generator.py:209-221`) |
| **N-B6** | Komunikat poniżej 50 pkt nigdy nie wchodzi do posta | `post_main.py:44-48` (reguła powstała w kodzie) | **EGZEKWOWANY w SQL** | `AND analysis_score >= @min_score` (`db/bigquery.py:2379`), razem z `event_type != 'inne'` (`:2377`) |
| **N-B7** | Jedno okno pory dnia = najwyżej jedna publikacja dziennie | `post_main.py:153-155` | **EGZEKWOWANY** | `x_post_already_published(window)` przed `publish_thread` (`post_main.py:168-172`) |

### 1C. Portfel i księga lotów (Supporting wg PRD)

| id | Niezmiennik | Źródło | Status | Dowód |
| -- | -- | -- | -- | -- |
| **N-C1** | **Stan posiadania musi dać się objaśnić operacjami:** `\|shares − Σ(±volume)\| ≤ DUST` | docstring `db/bigquery.py:373-383` | **ZADEKLAROWANY** — policzony, nazwany, zalogowany, **nieegzekwowany** | liczony `db/bigquery.py:677-679` i `:1118-1120`; odczytany `:696`, `:1183`; zalogowany `_log_unexplained_holdings` `:370-393`. Nic się nie zatrzymuje |
| **N-C2** | `ledger_open(D) + residual == shares_on_day(D)` dla każdego D | `context/archive/2026-08-05-fifo-basis-on-read-paths/research.md:41-44` | **ZADEKLAROWANY** — zmierzony **raz** | pomiar na 508 operacjach produkcyjnych; architektura na nim stoi (`db/bigquery.py:1146-1147` liczy P&L tą tożsamością); **nigdy nie zakodowany jako asercja** |
| **N-C3** | Reguła prawej krawędzi: ostatni dzień historii = dzisiejszy stan i dzisiejsza podstawa | docstring nazywa to wprost „the PUL-100 right-edge invariant" — `db/bigquery.py:430`, `:883-885` | **ZADEKLAROWANY** | docstring sam przyznaje: trzyma się *„by construction rather than by care"* (`:884-885`) |
| **N-C4** | `shares > 0` i `avg_buy_price > 0` | `src/api.py:1148-1149` | **EGZEKWOWANY na jednej z dwóch ścieżek zapisu** | warunek w handlerze; `PortfolioPositionIn` (`src/api.py:588-594`) nie ma **ani jednego** `Field(gt=…)`; ścieżka importu (`src/api.py:1453` → `db/bigquery.py:1346`) nie sprawdza nic |
| **N-C5** | „Brak lotu" nigdy nie zapada się w zerową podstawę (zero czyta się jako 100% zysku) | docstring `src/portfolio_lots.py:26-28`, `:274-275` | **EGZEKWOWANY** | trzy rozłączne gałęzie `src/portfolio_lots.py:321-329`; brak podstawy → `continue`, nigdy zero |
| **N-C6** | Sprzedaż w jednym portfelu nie może skonsumować lotu z drugiego | docstring `src/portfolio_lots.py:266-268` | **EGZEKWOWANY** | kluczowanie per portfel `src/portfolio_lots.py:289`; `portfolio_id` na każdym wierszu odczytu `db/bigquery.py:4147` |
| **N-C7** | `uncovered` nigdy nie jest ujemne | `src/portfolio_lots.py:206-208` | **EGZEKWOWANY** | `uncovered=remaining if remaining > DUST else 0.0` (`:208`) |
| **N-C8** | Import nie kasuje pozycji, której eksport nie może zobaczyć | docstring `db/bigquery.py:1355-1357` | **EGZEKWOWANY** | świadomy **brak** gałęzi `WHEN NOT MATCHED BY SOURCE` |
| **N-C9** | **Import brokerski jest atomowy:** operacje, pozycje i skreślenia albo wchodzą razem, albo wcale | **nigdzie** — reguła wynika z modelu, nie z dokumentu | **IGNOROWANY** | `src/api.py:1450-1454` to **trzy osobne instrukcje DML**; `grep -E "BEGIN TRANSACTION\|COMMIT\|ROLLBACK" db/` → **0 trafień** |
| **N-C10** | Saldo **niepodane** i saldo **podane jako zero** to dwa różne fakty | docstring `src/api.py:507-514` | **EGZEKWOWANY** | `if cash_pln is None: return None` (`src/api.py:520-521`) |
| **N-C11** | Ujemne saldo gotówki nie może wyglądać jak krótka pozycja | docstring `src/api.py:516-518` | **EGZEKWOWANY** | `max(0.0, …)` (`src/api.py:525`) |
| **N-C12** | Porządek zdarzeń karmiących FIFO musi być **totalny** | docstring `src/portfolio_lots.py:134-140` | **EGZEKWOWANY połowicznie** | `_sort_key` (`:133-142`) rozstrzyga wyłącznie remis buy-vs-sell; `ORDER BY occurred_at` **bez klucza wtórnego** (`db/bigquery.py:4153`) |
| **N-C13** | Widok zbiorczy `portfolio_id = "all"` jest **tylko do odczytu** | komentarz `src/api.py:426-428` | **EGZEKWOWANY na jednej ścieżce, przypadkowo na drugiej** | import: jawne 422 (`src/api.py:429`); `POST /api/portfolio/positions`: odrzucone dopiero przez „Wallet not found" 404 (`src/api.py:1155-1156`) — dokładnie ten „silently by accident", przed którym ostrzega komentarz `:427-428` |

### 1D. Notowania (Supporting) — dwa niezmienniki, dla pełności obrazu

| id | Niezmiennik | Źródło | Status |
| -- | -- | -- | -- |
| **N-D1** | Zamknięcie GPW/NC zawsze wygrywa; Bankier wyłącznie łata luki | `context/archive/2026-07-27-official-close-source/change.md` | **EGZEKWOWANY** — reguła wyrażona przez to, że `Kurs odn.` nigdy nie jest zapisywany jako cena |
| **N-D2** | Seria kursów jest **surowa**, nieskorygowana o splity i dywidendy | `context/archive/2026-07-24-backfill-historical-closes/plan.md:24` | **EGZEKWOWANY** — bitmask `&o=1111111` wyłącza korekty |

> **N-D2 jest ukrytym zobowiązaniem N-C1.** Skoro ceny nie są korygowane o split, **liczba akcji
> musi być**. W tym produkcie nie koryguje jej nic (`01-domain-distillation.md:160-163`). To nie
> jest osobny problem — to powód, dla którego N-C1 będzie łamany częściej, niż jest dziś.

---

## KROK 2 — Klasyfikacja i wybór #1

### Trzy osie

**(a) Rdzeniowość.** Tu jest cała trudność tego wyboru i nie chcę jej ominąć. Portfel jest
w `prd.md:150` **jawnym Non-goalem → v3+**. Gdyby oś (a) mierzyła rdzeniowość *subdomeny*,
każdy niezmiennik portfelowy dostawałby 2/5 i konkurs kończyłby się po stronie ESPI/EBI.

Rozdzielam więc oś na dwie kolumny i punktuję drugą:

- **a₁ — rdzeniowość subdomeny**: gdzie leży obszar wg celów produktu (`prd.md:20-22`, `:41-50`).
- **a₂ — rdzeniowość reguły**: czy naruszenie tej reguły łamie coś, co produkt o sobie
  **deklaruje jako warunek istnienia**.

Uzasadnienie rozdziału: `prd.md:105` wymienia wśród wymagań niefunkcjonalnych **Integrity** —
*„post nie może zawierać liczb ani faktów nieobecnych w źródłowym komunikacie"*, a `prd.md:48`
(Guardrail 2) nazywa to *„No hallucinations"*. To jest zdanie o **prawdziwości liczby, którą
produkt pokazuje człowiekowi**. Produkt zbudował drugą powierzchnię pokazującą liczby człowiekowi
— portfel — i przeniósł tam własną, najwyższą regułę **bez jej strażnika**. Reguła jest ta sama;
zmieniła się tylko powierzchnia.

**(b) Rozproszenie.** W ilu warstwach/plikach reguła żyje. Liczę realne miejsca, nie pliki.

**(c) Egzekwowanie.** `luka` 0–5: 0 = kod nie pozwala naruszyć, 5 = kod mierzy naruszenie i jedzie dalej.

### Tabela

| id | a₁ subdomena | a₂ reguła | b rozproszenie | c luka | a₂×b×luka | Nota |
| -- | -- | -- | -- | -- | -- | -- |
| N-A1 dedup przed AI | 5 | 5 | 3 | **0** | 0 | egzekwowany strukturalnie |
| N-A2 dedup globalny | 5 | 5 | 3 | **1** | 15 | okno dedupu ⊃ okno kandydatów (korekta wyżej); realna luka = tożsamość po URL |
| N-A3 schemat przed bramką | 5 | 4 | 2 | **0** | 0 | |
| N-A4 słownik `event_type` | 4 | 3 | 2 | **1** | 6 | fallback = głośne naruszenie |
| N-B1 twarda bramka | 5 | 5 | 2 | **0** | 0 | |
| N-B2 3 próby + alert | 5 | 5 | 2 | **0** | 0 | |
| N-B3 pusty wątek | 5 | 4 | 2 | **0** | 0 | guard niezależny od supervisora |
| N-B4 brak rekomendacji | 5 | 5 | 2 | **0** | 0 | ryzyko regulacyjne — egzekwowane |
| **N-B5 cashtag/tweet** | 5 | 3 | 3 | **3** | **27** | jedyny realnie niedomknięty niezmiennik Core |
| N-B6 próg 50 pkt | 5 | 3 | 2 | **0** | 0 | |
| **N-C1 residuum** | 2 | **5** | **5** | **5** | **125** | policzone, nazwane, zalogowane, nieegzekwowane |
| **N-C2 zgodność rekonstrukcji** | 2 | 5 | 5 | **5** | 125 | pochodna N-C1 — patrz niżej |
| N-C3 prawa krawędź | 2 | 4 | 4 | **4** | 64 | pochodna N-C1 w punkcie D = dziś |
| N-C4 `shares>0` | 2 | 3 | 4 | **3** | 36 | |
| **N-C9 atomowość importu** | 2 | 4 | 3 | **5** | **60** | **produkuje** naruszenia N-C1 |
| N-C12 porządek totalny | 2 | 3 | 3 | **3** | 27 | warunek aktywacji `unknown` |
| N-C13 „all" read-only | 2 | 3 | 2 | **2** | 12 | |

### Uczciwe rozpatrzenie strony ESPI/EBI

Zanim wybiorę portfel, muszę pokazać, że po stronie rdzeniowej **nie ma czego naprawiać**
w tej klasie. Przeszedłem po każdym Core-owym niezmienniku z osobna i wynik jest jednoznaczny:

- **Bramka supervisora, 3 próby, zakaz rekomendacji, guard pustego wątku, próg 50 pkt,
  idempotencja okna, dedup przed AI** — wszystkie **EGZEKWOWANE**, każdy przez konstrukcję
  sterowania (`if approved:`, `continue`, `raise`, `WHERE`), nie przez log.
- **Dedup globalny (N-A2)** — po sprawdzeniu arytmetyki okien wypada lepiej, niż zapisała
  destylacja (korekta nr 1). Zostaje realna, ale wąska luka tożsamości po URL, której kosztem
  jest jeden nadmiarowy e-mail.
- **Cashtag na tweet (N-B5)** — jedyny prawdziwie ZADEKLAROWANY niezmiennik Core i najpoważniejszy
  konkurent. Odrzucam go z jednego, decydującego powodu: **jego naruszenie jest głośne**.
  X zwraca 403, wyjątek leci do `except Exception` w `_publish_to_x` (`post_main.py:180-183`),
  status wątku ustawia się na `failed`, a właściciel dostaje alert (`_persist_and_alert`).
  Niezmiennik, którego złamanie samo krzyczy, ma inną wagę niż niezmiennik, którego złamanie
  wygląda jak poprawna liczba. Zostaje jako pierwszy kandydat spoza tego planu.

### Wybór #1 — i dlaczego wygrywa mimo niższej rdzeniowości subdomeny

> **Wybieram N-C1** — *stan posiadania musi dać się objaśnić operacjami* — z **N-C9**
> (atomowość) i **N-C2/N-C3** (zgodność rekonstrukcji, prawa krawędź) jako jego **rozszerzeniem
> w czasie**, nie jako osobnymi pozycjami.

**Zgadzam się z rekomendacją destylacji, ale nie z jej uzasadnieniem w całości.** Destylacja
(`01-domain-distillation.md:481`) uzasadnia wybór głównie zbieżnością rozjazdów R-02/R-03/R-04.
To prawda, ale to argument o *kodzie*. Argument, który wytrzymuje zderzenie z `prd.md:150`,
jest inny i podaję go zamiast tamtego:

1. **Reguła jest rdzeniowa, nawet jeśli subdomena nie jest.** Produkt zapisał sobie
   w wymaganiach niefunkcjonalnych (`prd.md:105`) i w Guardrailach (`prd.md:48`), że **liczba,
   którą pokazuje człowiekowi, musi mieć pokrycie w źródle**. Po stronie komunikatu to pokrycie
   sprawdza bramka LLM i egzekwuje `if result.approved:`. Po stronie portfela źródłem są
   operacje brokerskie, pokrycie nazywa się residuum — i nie sprawdza go nic. To nie jest nowa
   reguła zaimportowana z zewnątrz; to **własny, najwyższy guardrail produktu, zastosowany
   do drugiej powierzchni**.

2. **Po stronie Core nie ma nic porównywalnego do naprawienia.** Wykazane wyżej, po jednym
   niezmienniku. Iloczyn `rdzeniowość × luka` po stronie ESPI/EBI jest bliski zera nie dlatego,
   że reguły są mało ważne, tylko dlatego, że **są egzekwowane**. Wybór „najbardziej rdzeniowy
   I najsłabiej egzekwowany" to iloczyn, nie suma — a iloczyn ma dokładnie jedno maksimum.

3. **Non-goal z `prd.md:150` jest przestarzałym dowodem, nie aktualną intencją — ale nie
   powołuję się na to jako na główny argument.** Odnotowuję dla porządku: z pięciu Non-goals
   trzy zostały zbudowane (panel webowy `prd.md:149`, publikacja na X `prd.md:148`, portfele
   `prd.md:150`), a dokument nigdy nie został zaktualizowany. To jednak argument *ad hominem*
   wobec dokumentu i sam w sobie by nie wystarczył. Punkty 1 i 2 wystarczają.

4. **Koszt naruszenia jest pieniężny, cichy i już nazwany.** `PUL-124`: rozbieżność
   **−4 482 PLN** wobec wyciągu XTB, do dziś bez ustalonej przyczyny
   (`context/archive/2026-08-05-fifo-basis-on-read-paths/research.md:183-188`, za destylacją
   `:444-445`). Zła podstawa kosztowa wchodzi jednocześnie w cztery powierzchnie UI: tabelę
   pozycji, kafelek podsumowania, kalendarz i wykres wartości — wszystkie czytają
   `avg_buy_price` albo `basis` z tego samego korzenia.

5. **Moment jest optymalny, nie awaryjny.** Pomiar z `2026-07-30` pokazał, że rekonstrukcja
   `SUM(±volume)` zbiega do `user_portfolio_positions.shares` co do czwartego miejsca po
   przecinku dla każdego tickera akcyjnego we wszystkich pięciu portfelach; naruszenia są
   dokładnie trzy — dwa razy `_CASH` i raz pozycja wpisana ręcznie (`XTB`, 1,0 szt.)
   (`01-domain-distillation.md:426-433`). **Znane wyjątki dają się jeszcze wyliczyć na palcach**,
   więc zapisanie reguły jest tanie. Po pierwszym splicie, który rozjedzie kilkanaście pozycji
   naraz, już nie będzie.

**Dlaczego N-C2 nie jest osobnym #1.** Bo jest **objawem**. Dwie rekonstrukcje stanu posiadania
istnieją dokładnie dlatego, że nie ma jednego miejsca będącego właścicielem odpowiedzi na
pytanie „ile sztuk było dnia D". SQL liczy to wstecz (`db/bigquery.py:944-960`), Python liczy
to wprzód (`src/portfolio_lots.py:308-317`), spotykają się w jednym zapytaniu
(`db/bigquery.py:924` vs `:944`), bo nie mają gdzie indziej się spotkać. W projekcie z KROKU 4
tożsamość N-C2 przestaje być zbiegiem okoliczności i staje się **definicją metody agregatu** —
a dopisanie asercji, o którą prosi `refactor-opportunities/research.md:117`, staje się jedną
linijką zamiast dwunastą kontrolą skryptu.

**Dlaczego N-C9 wchodzi do tego samego pakietu.** Bo jest **jedyną znaną, powtarzalną maszyną
produkującą naruszenia N-C1**. `src/api.py:1450-1454` wykonuje trzy niezależne instrukcje DML.
Awaria między pierwszą a drugą zostawia operacje **przed** pozycjami — czyli dokładnie
niezerowe residuum. Awaria między drugą a trzecią zostawia ticker skreślony w eksporcie, ale
nadal trzymany. Nic tego nie cofa, bo w `db/` nie ma ani jednej transakcji.

---

## KROK 3 — Diagnoza: gdzie dziś żyje N-C1

### 3.1. Reguła nie istnieje jako reguła. Istnieje jako **pomiar** w dwóch miejscach

| Rola | Miejsce | Cytat |
| -- | -- | -- |
| Definicja (proza) | `db/bigquery.py:373-383` | *„Report holdings the broker operations do not account for, at a level prod sees."* |
| Pomiar #1 | `db/bigquery.py:677-679` | `(SELECT COUNT(*) FROM holders WHERE ticker != '_CASH' AND ABS(today_shares - total_signed) > 1e-9) AS residual_holders` |
| Pomiar #2 | `db/bigquery.py:1118-1120` | identyczny predykat, w `meta` zapytania historii |
| Odczyt | `db/bigquery.py:696`, `:1183` | `_residual = getattr(rows[0], "residual_holders", None) if rows else 0` |
| Reakcja | `db/bigquery.py:385-393` | `if not residual: return` … `logger.info("%s: %s holding(s) not explained by broker operations …")` |
| Kontynuacja | `db/bigquery.py:702-703`, `:1189-1190` | log, a zaraz po nim `return` z liczbami — **niezmienionymi** |

To jest podręcznikowy „połknięty błąd": kod **wie**, że reguła jest łamana, **nazywa** to
poprawnie, **mówi** o tym na poziomie, który produkcja widzi (świadomie INFO, nie DEBUG,
bo `api_main.py` konfiguruje root logger na INFO — `db/bigquery.py:376-379`), i **zwraca
policzoną z naruszeniem liczbę użytkownikowi**.

Warto zauważyć, jak dobra jest tu robota poza jednym brakującym krokiem: pomiar jest
**dwustronny** (kalendarz i wykres), **niesiony przez `meta`**, żeby przeżył okno bez ani jednego
dnia przechodzącego bramkę (`db/bigquery.py:1116-1117`), i **wycisza zero**, żeby nie generować
szumu. Brakuje wyłącznie tego, że po `logger.info` nie ma `raise`.

Drugi, bliźniaczy przypadek w tej samej funkcji: `basis_gaps` (`db/bigquery.py:1149-1153`) —
komentarz mówi *„Diagnostic only… silent under-valuation is exactly what must not happen
quietly"*, po czym liczba nie opuszcza zapytania.

### 3.2. Warstwy, które reguły nie egzekwują

**Trzy ścieżki zapisu, żadna nie sprawdza residuum:**

| # | Ścieżka | Co robi | Skutek dla N-C1 |
| -- | -- | -- | -- |
| W1 | `POST /api/portfolio/positions` (`src/api.py:1142-1168`) | `upsert_user_portfolio_position` — pisze **wyłącznie** do `user_portfolio_positions` | **Tworzy residuum z definicji.** Jedyne dostępne użytkownikowi lekarstwo na split zamienia pozycję z lotami w pozycję, której loty nie tłumaczą |
| W2 | `POST /api/portfolio/import/commit` (`src/api.py:1435-1464`) | trzy DML: `merge_user_broker_operations` → `merge_user_portfolio_positions_bulk` → `delete_user_portfolio_positions` | **Może zostawić stan pośredni** (N-C9). Zero transakcji w `db/` |
| W3 | `DELETE /api/portfolio/positions/{ticker}` (`src/api.py:1170-1189`) | kasuje wiersz pozycji, **nie tyka operacji** | Residuum o przeciwnym znaku: operacje mówią „trzymam", pozycji nie ma |

**Pięć ścieżek odczytu, każda z własnym zapytaniem:**

| # | Miejsce | Co czyta |
| -- | -- | -- |
| R1 | `src/api.py:1118-1121` — `GET /api/portfolio/positions` | `list_user_portfolio_positions(..., include_first_buy_date=True)` |
| R2 | `src/api.py:1276` — `GET /api/portfolio/treemap` | `list_user_portfolio_positions(user_id)` (wszystkie portfele) |
| R3 | `src/api.py:1555` — `GET /api/portfolio/realized` | `list_broker_trades` → `compute_realized_pnl` — **wyłącznie operacje, zero pozycji** |
| R4 | `src/api.py:454` — resolve importu | `list_user_portfolio_positions(user_id, portfolio_id)` |
| R5 | `db/bigquery.py:781-784` — `_basis_segments_for` | **oba źródła naraz**, jedyne miejsce, gdzie się spotykają |

Do tego dwa zapytania SQL rekonstruujące stan posiadania niezależnie od Pythona:
`get_portfolio_calendar_data` (`db/bigquery.py:396`) i `get_portfolio_history`
(`db/bigquery.py:829`). Razem: **osiem miejsc czytających „ile sztuk", zero miejsc
odpowiadających za to, żeby odpowiedź była jedna.**

### 3.3. Gdzie reguła jest egzekwowana niespójnie

**N-C4 (`shares > 0`, `avg_buy_price > 0`) — dwie ścieżki, dwa zachowania:**

```
src/api.py:1148-1149    if body.shares <= 0 or body.avg_buy_price <= 0:
                            raise HTTPException(status_code=422, ...)      # ← W1: jest
src/api.py:588-594      class PortfolioPositionIn(BaseModel):
                            shares: float                                  # ← zero Field(gt=…)
                            avg_buy_price: float
src/api.py:1453         merge_user_portfolio_positions_bulk(...)           # ← W2: nie ma
```

Ścieżka W2 pisze to, co wyprodukował parser. Dziś jest to bezpieczne, ale **przez przypadek,
nie przez kontrakt**: `reconstruct_positions` (`src/brokers/xtb.py:226-229`) odrzuca `shares <= DUST`
do listy `closed`. Jednocześnie ta sama ścieżka **świadomie pisze wiersz o zerowych sztukach**:
`_cash_position` (`src/api.py:520-527`) zwraca `_CASH` z `shares = max(0.0, …)`, i to jest
poprawne — `src/api.py:507-514` tłumaczy, dlaczego pominięcie zerowego salda zostawiało wydaną
gotówkę w portfelu na zawsze. Czyli: **jedyny wyjątek od N-C4 istnieje, jest uzasadniony
i nie jest nigdzie nazwany jako wyjątek.**

**N-C13 („all" tylko do odczytu) — jawnie na jednej ścieżce, przypadkowo na drugiej:**

```
src/api.py:426-429   # No write path resolves the aggregate sentinel; the ownership check below
                     # would reject it only by accident, and silently.
                     raise HTTPException(status_code=422, detail="Nie mozna importowac …")
src/api.py:1155-1156 if not any(w["portfolio_id"] == body.portfolio_id for w in wallets):
                         raise HTTPException(status_code=404, detail="Wallet not found")
```

Drugi fragment to dokładnie to „by accident, and silently", przed którym ostrzega pierwszy.

### 3.4. Gdzie klient jest (współ)strażnikiem

Tu **koryguję pochopne oczekiwanie**: klient **nie** jest jedynym strażnikiem N-C4 — serwer
sprawdza to samo w `src/api.py:1148-1149`. Ale strzeże **innego progu**:

| Warstwa | Reguła | Cytat |
| -- | -- | -- |
| HTML | `shares ≥ 0.001` | `static/index.html:4580`, `:4595` (`min="0.001"`) |
| JS | `shares > 0 && avgBuyPrice > 0` | `static/index.html:4997`, `:5010` |
| Serwer | `shares > 0 && avg_buy_price > 0` | `src/api.py:1148-1149` |

Zgłoszenie 0,0005 sztuki przejdzie przez API i zostanie odrzucone przez przeglądarkę. Trzy
warstwy, dwa różne progi, żaden nie jest kanoniczny.

**Natomiast klient jest jedynym miejscem, gdzie residuum w ogóle mogłoby być widoczne — i nie
jest.** `static/index.html` nie renderuje ani residuum, ani `origin`, ani żadnego znacznika
„ta pozycja nie ma pokrycia w operacjach". Jedyny objaw, jaki widzi użytkownik, to
`first_buy_date = None` → puste `—` w kolumnie okresu posiadania (`src/api.py:619-623`).

### 3.5. Czego nie sprawdza żaden test

| Twierdzenie | Co naprawdę asertuje test |
| -- | -- |
| „residuum jest liczone" | `tests/test_bigquery.py:1694` — `assert "ABS(today_shares - total_signed) > 1e-9) AS residual_holders" in sql`. **Pin stringa SQL na zamockowanym kliencie** |
| „residuum jest raportowane" | `tests/test_bigquery.py:1727` — `assert "2 holding(s) not explained by broker operations" in caplog.text`. Liczba `2` pochodzi z **ręcznie wpisanego wiersza** (`:1723`), nie z żadnych danych |
| „zero nie hałasuje" | `tests/test_bigquery.py:1730-1736` — poprawne i wartościowe, ale to test o logowaniu |
| „obie rekonstrukcje się zgadzają" | **nie istnieje.** `refactor-opportunities/research.md:114`: *„w `tests/` i `scripts/` nie ma żadnego porównania obu rekonstrukcji"* |
| „reguła prawej krawędzi trzyma" | **nie istnieje.** e2e podmienia oba czytniki: `tests/e2e/conftest.py:588` (`_fake_list_broker_trades`), `:619` (`_fake_list_user_portfolio_positions`) |

**Podsumowanie diagnozy w jednym zdaniu.** N-C1 jest dziś regułą, którą system potrafi wyliczyć
w dwóch zapytaniach, nazwać po polsku i po angielsku, zalogować na produkcji — i której nie
potrafi **odmówić**.

---

## KROK 4 — Projekt agregatu-strażnika

### 4.0. Granica, której ten projekt nie przekracza

`src/portfolio_lots.py:22-28` odmawia rozstrzygnięcia, czym jest sprzedaż, której loty nie
pokrywają: *„The ledger reports what it could not cover; it does not decide what to do about
it."* **Ta granica jest poprawna i zostaje nietknięta.** Księga lotów naprawdę nie może wiedzieć,
czym jest pozycja, której nie widzi.

Problem polega na tym, że decyzja należy do warstwy wyżej, a tej warstwy nie ma — więc odmowa
księgi jest ostatnim słowem systemu. Ten refaktor **daje księdze rozmówcę**, nie zabiera jej
granicy.

### 4.1. Korzeń i granice agregatu

```
Agregat:  Portfolio
Korzeń:   (user_id, portfolio_id)
Wewnątrz: Holding (linia posiadania per ticker) + księga lotów tego portfela
Na zewnątrz: Company/instrument (referencja po tickerze), notowania, użytkownik
```

Granica jest wyznaczona przez **niezmiennik**, nie przez tabelę: N-C1 wiąże `shares` z sumą
operacji **w obrębie jednego portfela** — `src/portfolio_lots.py:266-268` już dziś zabrania
sprzedaży w jednym portfelu konsumować lotu z drugiego. Agregat na `(user_id, portfolio_id)`
jest więc najmniejszą granicą, w której reguła jest wyrażalna.

`portfolio_id = "all"` **nie jest agregatem** — jest widokiem czytającym N agregatów
(`src/api.py:628`, scalanie `:659-704`). Wynika z tego wprost, że nie da się do niego pisać,
i to przestaje być przypadkiem (N-C13).

### 4.2. Nowe pojęcie: pochodzenie linii posiadania

**Naruszeniem N-C1 nie jest niezerowe residuum.** Gotówka, dywidenda rzeczowa (S2B), pozycja
wpisana ręcznie — wszystkie legalnie mają residuum i wszystkie są udokumentowane
(`db/bigquery.py:422-430`, `src/api.py:386-388`, `db/bigquery.py:1355-1357`).

Naruszeniem jest **residuum, którego nikt nie zadeklarował**. Stąd jedno brakujące pojęcie:

```python
class PositionOrigin(StrEnum):
    RECONCILED = "reconciled"   # sztuki w całości objaśnione operacjami; residuum MUSI być ~0
    DECLARED   = "declared"     # użytkownik/import stwierdził stan, którego operacje nie tłumaczą
    CASH       = "cash"         # `_CASH` — residualne z definicji (dziś: WHERE ticker != '_CASH')
```

Nazwa `DECLARED` nie jest wymyślona — podnoszę ją z istniejącego stringa w logu:
*„holding(s) not explained by broker operations"* (`db/bigquery.py:387-388`) i z sekcji
*„Absence is not zero"* (`context/archive/2026-08-05-holding-period-column/change.md:44-49`,
za destylacją `:436-439`).

### 4.3. Typy i sygnatury

Moduł `src/portfolio/aggregate.py` — **czysty, bez I/O**, jak `src/portfolio_lots.py:1-33`.

```python
DUST = 1e-9                       # reużyte z src.portfolio_lots, nie kopiowane
ALL_PORTFOLIOS = "all"            # przeniesione z src/api.py:628

@dataclass(frozen=True)
class Holding:
    """Jedna linia posiadania w portfelu — stan, pochodzenie i to, ile z niego
    nie jest objaśnione operacjami."""
    ticker: str
    shares: float
    avg_buy_price: float | None       # None = ticker sprzedany do zera (StoredPosition:231-234)
    origin: PositionOrigin
    declared_shares: float            # nazwane residuum; 0.0 dla RECONCILED
    declared_reason: str | None       # dlaczego zadeklarowane (split, spin-off, wpis ręczny)

@dataclass
class Portfolio:
    """AGREGAT ROOT. Jedyne miejsce, które odpowiada na pytanie
    „ile sztuk i po jakim koszcie" — dziś i w dowolnym dniu wstecz."""
    user_id: str
    portfolio_id: str
    _holdings: dict[str, Holding]
    _ledgers: dict[str, TickerLedger]     # z src.portfolio_lots.build_ledger — bez zmian
    _events: dict[str, list[LotEvent]]    # surowe zdarzenia, do przewijania w czasie
    _known_instruments: frozenset[str]    # słownik tickerów, wstrzykiwany przez repozytorium
```

### 4.4. Błędy domenowe — nazwane, nie ciche

```python
class PortfolioInvariantError(Exception):
    """Baza. Każda podklasa niesie http_status — mapowanie jest częścią domeny,
    nie handlera, żeby nowa trasa nie mogła o nim zapomnieć."""
    http_status: int

class UnexplainedHoldingError(PortfolioInvariantError):     # 409
    """N-C1. Linia RECONCILED, której operacje nie tłumaczą.
    Niesie listę (ticker, shares, explained, residual) — żeby komunikat mówił,
    czego dotyczy, a nie tylko, że coś jest nie tak."""

class ReconstructionMismatchError(PortfolioInvariantError): # 500
    """N-C2. ledger_open(D) + declared != shares_on_day(D) po stronie SQL."""

class NonPositiveHoldingError(PortfolioInvariantError):     # 422
    """N-C4. shares <= 0 albo avg_buy_price <= 0, poza jawnym wyjątkiem CASH."""

class UnknownInstrumentError(PortfolioInvariantError):      # 422
    """Ticker spoza companies/etf_instruments (dziś: src/api.py:1159-1160)."""

class AggregateViewIsReadOnlyError(PortfolioInvariantError):# 422
    """N-C13. Próba zapisu do portfolio_id == "all"."""
```

Wszystkie **rzucają**. Żaden nie loguje-i-jedzie dalej. To jest cały punkt tego refaktoru.

### 4.5. Metody domenowe z preconditions

```python
def declare_position(self, ticker, shares, avg_buy_price, reason) -> None:
    """Zadeklaruj stan posiadania. Legalne — także wtedy, gdy operacje go nie tłumaczą.

    To jest jedyne dostępne dziś lekarstwo na akcję korporacyjną (split, spin-off),
    i po tej zmianie przestaje być cichym psuciem residuum: różnica zostaje
    zapisana jako `declared_shares` z powodem, zamiast wyciekać do logu.
    """
    # --- preconditions, w kolejności od najtańszej ---
    if self.portfolio_id == ALL_PORTFOLIOS:
        raise AggregateViewIsReadOnlyError(self.portfolio_id)
    if shares <= DUST:
        raise NonPositiveHoldingError("shares", shares)
    if avg_buy_price <= 0:
        raise NonPositiveHoldingError("avg_buy_price", avg_buy_price)
    if ticker not in self._known_instruments:
        raise UnknownInstrumentError(ticker)

    explained = self._explained_shares(ticker)          # ledger.open_shares - ledger.uncovered
    residual  = shares - explained
    self._holdings[ticker] = Holding(
        ticker=ticker,
        shares=shares,
        avg_buy_price=avg_buy_price,
        origin=(PositionOrigin.RECONCILED if abs(residual) <= DUST
                else PositionOrigin.DECLARED),
        declared_shares=(0.0 if abs(residual) <= DUST else residual),
        declared_reason=(None if abs(residual) <= DUST else reason),
    )
    self._check_invariants()        # ← jedna linia, w której mieści się cały refaktor


def apply_broker_import(self, positions, operations, closed_tickers) -> ImportOutcome:
    """Nałóż eksport brokera na agregat. Jedna operacja domenowa, nie trzy zapisy.

    Zmiana zachowania wobec dziś: pozycja obecna w portfelu, a nieobecna
    w eksporcie, nie zostaje po cichu z niezerowym residuum — zostaje
    przeklasyfikowana na DECLARED z powodem "absent from broker export"
    (dziś to jest tylko `untouched` w preview, src/api.py:386-388, :478).
    """
    if self.portfolio_id == ALL_PORTFOLIOS:
        raise AggregateViewIsReadOnlyError(self.portfolio_id)
    self._absorb_operations(operations)           # idempotentnie, klucz: external_id
    for p in positions:
        self._reconcile_line(p)                   # ustawia origin z porównania z księgą
    for t in closed_tickers:
        self._close_line(t)                       # kasuje linię, ZOSTAWIA operacje i lot history
    for t in self._holdings.keys() - {p.ticker for p in positions} - set(closed_tickers):
        self._redeclare_as_absent_from_export(t)  # N-C8 pozostaje, ale przestaje być cichy
    self._check_invariants()
    return ImportOutcome(...)


def remove_position(self, ticker, reason) -> None:
    """Skreśl linię. Operacje zostają — historia nie jest własnością dzisiejszego stanu."""
    ...
    self._check_invariants()
```

### 4.6. Serce: `_check_invariants`

```python
def _check_invariants(self) -> None:
    """N-C1. Wołane po KAŻDEJ mutacji, nigdy przez klienta agregatu.

    Zwróć uwagę na predykat: nie `residual == 0`, tylko `origin is RECONCILED
    => residual ~ 0`. Gotówka, wpis ręczny i dywidenda rzeczowa mają residuum
    legalnie — dziś różnicę robi `WHERE ticker != '_CASH'` (db/bigquery.py:1119),
    czyli jeden ticker zaszyty w SQL. Tu robi ją pochodzenie linii.
    """
    drifted = [
        (h.ticker, h.shares, self._explained_shares(h.ticker))
        for h in self._holdings.values()
        if h.origin is PositionOrigin.RECONCILED
        and abs(h.shares - self._explained_shares(h.ticker)) > DUST
    ]
    if drifted:
        raise UnexplainedHoldingError(self.user_id, self.portfolio_id, drifted)
```

### 4.7. Oś czasu — jedyny właściciel odpowiedzi „ile sztuk dnia D"

```python
def shares_on(self, day: date) -> dict[str, float]:
    """Stan posiadania w dniu D.

    `declared_shares` jest z definicji stałe w czasie (db/bigquery.py:427-430:
    "leaves whatever the operations do not explain — the residual — constant
    across the window"), więc ta formuła JEST tożsamością N-C2, a nie jej
    przypadkową zgodnością:

        shares_on(D)[t] == ledger_open(t, D) + declared_shares(t)

    Prawa krawędź (N-C3) wychodzi z niej jako wniosek, nie jako osobna reguła:
    dla D = dziś ledger_open == explained, więc shares_on(dziś) == shares.
    """
    return {
        t: self._ledger_as_of(t, day).open_shares + h.declared_shares
        for t, h in self._holdings.items()
    }

def basis_on(self, day: date) -> dict[str, float]:
    """Podstawa kosztowa w dniu D. Deleguje do src.portfolio_lots.basis_segments —
    arytmetyka zostaje tam, gdzie jest przetestowana (tests/test_portfolio_lots.py)."""

def assert_agrees_with(self, sql_shares_on_day: Mapping[tuple[str, date], float]) -> None:
    """N-C2 jako sprawdzalna asercja. Porównuje shares_on(D) z rekonstrukcją SQL
    wstecz. Rzuca ReconstructionMismatchError z listą rozjazdów.

    Nie unifikuje obu implementacji — obie są świadome i udokumentowane
    (db/bigquery.py:414-440). Daje im miejsce, w którym można je porównać.
    """
```

### 4.8. Repozytorium — ładuje i zapisuje agregat, nie wiersze

Moduł `db/portfolio_repository.py`:

```python
class PortfolioRepository:
    def load(self, user_id: str, portfolio_id: str) -> Portfolio:
        """Jedno wejście zamiast pięciu rozsianych.

        Zastępuje: src/api.py:454, :1118, :1276, :1555 oraz db/bigquery.py:781-784
        i :1654. Dwie rundy do BQ (pozycje + operacje) — tyle samo, co dziś płaci
        _basis_segments_for (db/bigquery.py:777-779), a endpoint i tak cache'uje 300 s.
        """

    def save(self, portfolio: Portfolio) -> None:
        """Zapisuje agregat w JEDNEJ transakcji. To jest odpowiedź na N-C9."""
```

Kształt transakcji:

```sql
BEGIN TRANSACTION;
  MERGE `user_broker_operations`  ...  -- insert-only po external_id
  MERGE `user_portfolio_positions` ... -- bez WHEN NOT MATCHED BY SOURCE (N-C8 zostaje)
  DELETE FROM `user_portfolio_positions`
   WHERE user_id=@u AND portfolio_id=@p AND ticker IN UNNEST(@closed);
COMMIT TRANSACTION;
```

> **⚠ To jest jedyne miejsce w tym planie oznaczone jako `unknown`.** Nie zweryfikowałem
> empirycznie, że wielo-instrukcyjna transakcja BigQuery obejmie te trzy DML-e w tym projekcie
> i zmieści się w budżecie 60 s Cloud Run. Znany pomiar: sam merge 571 wierszy zajmuje ~4 s
> (`src/api.py:1448-1449`). **To musi zostać sprawdzone round-tripem przed fazą F3**,
> a nie założone — i sprawdzone **po kodzie wyjścia, nie po grepie logu**
> (zasada projektu: `feedback-verify-by-exit-code`). Jeśli transakcja okaże się niedostępna,
> planem B jest **odwrócenie kolejności** (pozycje przed operacjami) plus kompensacja: częściowa
> awaria zostawia wtedy stan, w którym operacje są *za* pozycjami, co daje residuum ujemne
> i wykrywalne przy najbliższym `load()` — gorsze niż transakcja, lepsze niż dziś.

### 4.9. Cienkie trasy — egzekucja przenosi się na serwer

```python
# src/api.py — jeden handler dla całej rodziny błędów domenowych
@app.exception_handler(PortfolioInvariantError)
async def _portfolio_invariant(request, exc: PortfolioInvariantError):
    return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})


@app.post("/api/portfolio/positions")
async def post_portfolio_position(body: PortfolioPositionIn, user_id=Depends(_get_user_id)):
    portfolio = repo.load(user_id, body.portfolio_id)          # parse → agregat
    portfolio.declare_position(                                 # metoda domenowa
        body.ticker, body.shares, body.avg_buy_price,
        reason=body.reason or "wpis reczny",
    )
    repo.save(portfolio)                                        # jedna transakcja
    return {"ticker": body.ticker, "upserted": True}
```

Trasa traci: sprawdzenie `shares <= 0` (`src/api.py:1148-1149`), sprawdzenie właściciela portfela
(`:1155-1156`), sprawdzenie tickera (`:1158-1160`) i mapowanie `BigQueryError` na 500
(`:1165-1167`). Wszystkie cztery przenoszą się w jedno miejsce.

Model wejściowy przestaje być workiem na liczby:

```python
class PortfolioPositionIn(BaseModel):
    portfolio_id: str
    ticker: str
    company_name: str
    shares: float = Field(gt=0)          # dziś: nic (src/api.py:593)
    avg_buy_price: float = Field(gt=0)   # dziś: nic (src/api.py:594)
    reason: str | None = None            # nowe: po co deklarujesz stan
```

To jest **podwójny pas** i tak ma być: `Field(gt=0)` odrzuca na krawędzi HTTP, a
`NonPositiveHoldingError` w agregacie chroni ścieżkę importu i każdą przyszłą trasę, która
o `Field` zapomni. Nota: `Field` **nie jest dziś w ogóle importowane** w `src/api.py`
(`refactor-opportunities/research.md:150`), więc to jest zmiana konwencji — i musi być zaznaczona
jako taka w PR-ze.

### 4.10. Czego ten refaktor NIE robi

- **Nie modeluje akcji korporacyjnych.** `CorporateAction` zostaje kandydatem #3 z destylacji
  (`01-domain-distillation.md:483`). Ten refaktor daje mu **miejsce do wylądowania**
  (`declare_position(reason=...)`) i nic więcej.
- **Nie unifikuje dwóch rekonstrukcji.** Zostają obie, obie świadome (`db/bigquery.py:414-440`).
  Zyskują miejsce, w którym da się je porównać.
- **Nie rusza granicy `src/portfolio_lots.py:22-28`** ani arytmetyki FIFO. `build_ledger`,
  `basis_segments`, `first_open_lot_dates` zostają bez jednej zmiany — mają 21 testów
  (`tests/test_portfolio_lots.py`) i są najlepiej zrobioną częścią repo.
- **Nie rusza pipeline'u komunikatów.** Zero zmian w `main.py`, `post_main.py`, `src/analyzer.py`.
- **Nie wstrzykuje repozytorium do wszystkich 32 tras** (kandydat P1 z
  `refactor-opportunities/research.md:151`) — tylko do pięciu portfelowych.

---

## KROK 5 — Before/after, plan faz, testy

### 5.1. Before / after dla każdego dzisiejszego miejsca reguły

| Miejsce | Dziś | Po refaktorze |
| -- | -- | -- |
| `db/bigquery.py:370-393` `_log_unexplained_holdings` | `logger.info(...)` i return; wywołanie z `:702`, `:1189` | **Usunięte.** Rolę przejmuje `Portfolio._check_invariants` → `UnexplainedHoldingError` |
| `db/bigquery.py:677-679`, `:1118-1120` `residual_holders` | dwa `COUNT(*)` z `WHERE ticker != '_CASH'` | Zostają jako **drugie źródło** dla `assert_agrees_with`; `_CASH` przestaje być zaszytym wyjątkiem — jest `origin = CASH` |
| `src/api.py:1148-1149` walidacja `>0` | `if` w handlerze | `Field(gt=0)` na modelu + `NonPositiveHoldingError` w agregacie |
| `src/api.py:588-594` `PortfolioPositionIn` | gołe `float` | `Field(gt=0)`, nowe pole `reason` |
| `src/api.py:1142-1168` `POST /positions` | 27 linii: wallety, ticker, upsert, cache, 3× `except BigQueryError` | 6 linii: `load` → `declare_position` → `save` |
| `src/api.py:1170-1189` `DELETE /positions` | kasuje pozycję, operacje zostają | `remove_position(reason)` → `_check_invariants` |
| `src/api.py:1435-1464` `import/commit` | **trzy niezależne DML** (`:1450-1454`) | `apply_broker_import` → `repo.save` w **jednej transakcji** (N-C9) |
| `src/api.py:426-429` guard „all" (import) | jawne 422 | `AggregateViewIsReadOnlyError` — ta sama reguła, jedno miejsce |
| `src/api.py:1155-1156` guard „all" (POST) | 404 „Wallet not found" **przypadkiem** | jawny 422, ten sam błąd co wyżej |
| `src/api.py:454`, `:1118`, `:1276`, `:1555` odczyty | cztery różne wywołania dwóch funkcji BQ | `repo.load(...)` |
| `db/bigquery.py:781-784` `_basis_segments_for` | pobiera trades + positions, składa `LotEvent`/`StoredPosition` | przenosi się do `PortfolioRepository.load`; funkcja znika |
| `db/bigquery.py:1646-1662` `_first_open_lot_dates` | drugi, niezależny pobór tych samych operacji | `Portfolio.first_open_lot_date(ticker)` — jeden pobór |
| `src/api.py:602-623` `PortfolioPositionOut` | `first_buy_date: date \| None` z komentarzem *„None when no operation explains the position"* | dochodzi `origin` i `declared_shares` — komentarz staje się polem |
| `static/index.html` | brak jakiegokolwiek śladu residuum | znacznik przy linii `DECLARED` (faza F7) |
| `tests/test_bigquery.py:1675-1737` | asercje na stringu SQL i na logu | zostają (pinują drugie źródło); dochodzą testy niezmiennika |

### 5.2. Fazy

Kolejność wymuszona dwiema zależnościami: **F0 przed wszystkim** (bo fail-fast na istniejących
danych to incydent, nie refaktor) oraz **F2 po C1 z `refactor-opportunities`** (bo `list_broker_trades`
— jedyne wejście produkcyjne do FIFO — ma dziś zero testów, `research.md:92`).

| Faza | Zakres | Test-first? | Czym weryfikowana |
| -- | -- | -- | -- |
| **F0** | **Pomiar na produkcji.** Ile linii ma dziś `\|shares − Σ(±volume)\| > DUST`, per portfel i ticker. Read-only `SELECT`. | nie (pomiar) | wynik zapytania; **warunek wejścia:** znane naruszenia muszą dać się wyliczyć, żeby F6 mógł je zaklasyfikować |
| **F1** | **Czysta domena**: `src/portfolio/aggregate.py` — `PositionOrigin`, `Holding`, `Portfolio`, błędy, `_check_invariants`, `declare_position`, `remove_position`, `shares_on`, `assert_agrees_with` | **TAK** | `uv run pytest` — zero I/O, tak jak `tests/test_portfolio_lots.py` |
| **F2** | **`PortfolioRepository.load`** — adapter odczytu | **TAK** | `uv run pytest` z `_mock_client_for_select` + `_Row(dict)` (`tests/test_bigquery_broker_operations.py:233-250` — **nie** `_mock_bq_client`, patrz pułapka `research.md:100-103`) **plus** round-trip |
| **F3** | **`PortfolioRepository.save`** — jedna transakcja (N-C9) | **nie** (infra) | **wyłącznie round-trip** `scripts/test_bq_*`. Zamockowany SQL porównywałby mock ze sobą — mechanizm awarii opisany w `lessons.md` (PUL-29, PUL-88) |
| **F4** | **Przepięcie tras** — 5 endpointów portfelowych, `exception_handler`, `Field(gt=0)` | **TAK** | `uv run pytest tests/test_api.py` + `tests/e2e` |
| **F5** | **`assert_agrees_with` jako kontrola nr 12** w `scripts/test_bq_portfolio_time_dimension.py` (N-C2) | **TAK** (test przed podpięciem) | round-trip; **wymaga potwierdzenia człowieka** — skrypt tworzy i kasuje własne tabele |
| **F6** | **Backfill `origin` / `declared_shares`** — dwie kolumny NULLABLE (`ALTER TABLE ADD COLUMN`, wymóg z `db/bigquery.py:3253-3254`), klasyfikacja istniejących wierszy wg wyniku F0 | nie (migracja) | round-trip + ponowny F0 = 0 nieobjaśnionych linii `RECONCILED` |
| **F7** | **UI** — `origin` widoczny w tabeli pozycji; residuum przestaje być wyłącznie logiem | **TAK** (e2e) | `npx playwright test` |

**Rollout fail-fast — i dlaczego to nie jest złagodzenie zasady.** Od F4 **zapis** rzuca zawsze:
nielegalna operacja zatrzymuje się i nic nie zapisuje. **Odczyt** portfela, który *już jest*
niespójny, nie rzuca — zwraca `origin` i `declared_shares` jako dane. Uzasadnienie: nielegalną
operacją jest zapis, który tworzy rozjazd; wyświetlenie zastanego stanu nie jest operacją
użytkownika, a 500 na `GET /positions` zamieniłoby cudzy stary błąd w dzisiejszą awarię.
F6 domyka lukę, klasyfikując zastane wiersze.

### 5.3. Przypadki testowe niezmiennika

**Legalne — nie mogą rzucać:**

| # | Przypadek | Oczekiwanie |
| -- | -- | -- |
| L1 | Import zapisuje pozycje zgodne z operacjami | `origin == RECONCILED`, `declared_shares == 0.0`, brak wyjątku |
| L2 | `declare_position` na tickerze bez żadnych operacji (wpis ręczny) | `origin == DECLARED`, `declared_shares == shares`, `declared_reason` niepuste |
| L3 | Linia `_CASH` z `shares = 0.0`, `avg_buy_price = 1.0` (`src/api.py:522-527`) | `origin == CASH`, zwolniona z N-C1 **i** z N-C4 |
| L4 | Ticker sprzedany do zera → linia znika, operacje zostają | `shares_on(dzień sprzed sprzedaży)` nadal zwraca dawny stan |
| L5 | Pozycja w portfelu, nieobecna w eksporcie (S2B) | zostaje (N-C8), ale jako `DECLARED` z powodem — nie jako ciche residuum |
| L6 | `declare_position` nadpisujące linię `RECONCILED` (naprawa po splicie) | legalne; `origin` → `DECLARED`, `declared_shares == nowe − explained` |
| L7 | Dryf poniżej progu: `shares − explained == 1e-10` | **nie rzuca** — granica `DUST` sprawdzona z obu stron |
| L8 | Portfel bez ani jednej operacji (tylko wpisy ręczne) | `load` działa, `shares_on` zwraca stałą — dziś to ścieżka pustej tablicy STRUCT (`db/bigquery.py:930-935`) |

**Nielegalne — muszą rzucić nazwany błąd:**

| # | Przypadek | Oczekiwany błąd |
| -- | -- | -- |
| N1 | Dopisanie operacji `buy` pod linią `RECONCILED` bez zmiany `shares` | `UnexplainedHoldingError`, komunikat zawiera ticker, `shares` i `explained` |
| N2 | `declare_position(shares=0)` / `shares=-1` | `NonPositiveHoldingError("shares", …)` |
| N3 | `declare_position(avg_buy_price=0)` / `= -1` | `NonPositiveHoldingError("avg_buy_price", …)` |
| N4 | `declare_position` na tickerze spoza słownika | `UnknownInstrumentError` (dziś: 422 z `src/api.py:1159-1160`) |
| N5 | Dowolny zapis przy `portfolio_id == "all"` | `AggregateViewIsReadOnlyError` (dziś: przypadkowe 404, `src/api.py:1155-1156`) |
| N6 | `assert_agrees_with` dostaje `shares_on_day` różniące się o **jedną sztukę** | `ReconstructionMismatchError` z listą `(ticker, day, python, sql)` |
| N7 | Import: merge operacji przechodzi, merge pozycji pada | **nic nie zostaje zapisane**; po `load()` residuum == 0 (dziś: operacje wchodzą, pozycje nie) |
| N8 | Sprzedaż większa niż suma lotów w tym portfelu | `uncovered > 0` (N-C7, `src/portfolio_lots.py:208`) **i** linia nie może zostać `RECONCILED` |
| N9 | Dwa `buy` o identycznym `occurred_at`, agregat ładowany dwukrotnie | `shares_on(D)` **identyczne** przy obu ładowaniach — przypina N-C12/T2 na granicy odczytu |

**Testy charakteryzujące istniejące zachowanie** (żeby refaktor nie zmienił nic po cichu) —
konwencja już w repo, `tests/test_portfolio_realized.py:231-236`:

| # | Co przypina |
| -- | -- |
| C1 | Prawa krawędź (N-C3): `basis_on(dziś) == avg_buy_price` co do `DUST` — dziś zmierzone `−0,00` (`archive/2026-08-05-fifo-basis-on-read-paths/research.md:56`) |
| C2 | `first_buy_date` liczone z **najstarszego otwartego lotu**, nie `MIN(buy)` — 3 z 13 tickerów produkcyjnych różnią się, do 424 dni (`src/portfolio_lots.py:356-359`) |
| C3 | „Brak lotu" nigdy nie daje zerowej podstawy (N-C5, `src/portfolio_lots.py:321-329`) |
| C4 | Saldo niepodane ≠ saldo zerowe (N-C10, `src/api.py:520-521`) |

### 5.4. Nowe nazwy „load-bearing" do zarejestrowania

Nazwy w kodzie po angielsku, w rozmowie i UI po polsku — zgodnie z konwencją projektu.

| Nazwa | Po polsku | Co znaczy | Skąd wzięta |
| -- | -- | -- | -- |
| `Portfolio` (agregat) | **portfel jako całość** | korzeń `(user_id, portfolio_id)`; jedyny właściciel odpowiedzi „ile sztuk i po jakim koszcie" | nowa; wskazana jako brakująca w `01-domain-distillation.md:296` |
| `Holding` | **linia posiadania** | stan jednego tickera w jednym portfelu, z pochodzeniem | nowa; zastępuje bezimienne „pozycja" z dwoma zachowaniami (R-03) |
| `PositionOrigin` | **pochodzenie pozycji** | `reconciled \| declared \| cash` | nowa; rozstrzyga pytanie z `01-domain-distillation.md:533` |
| `declared_shares` | **sztuki zadeklarowane** | nazwane residuum — ile z linii nie tłumaczą operacje | podniesione ze stringa logu `db/bigquery.py:387-388` |
| `declared_reason` | **powód deklaracji** | split / spin-off / wpis ręczny / nieobecne w eksporcie | nowa; miejsce lądowania dla `CorporateAction` |
| `explained_shares` | **sztuki objaśnione** | `ledger.open_shares − ledger.uncovered` | nowa nazwa na istniejące `total_signed` (`db/bigquery.py:958`) |
| `UnexplainedHoldingError` | — | linia `RECONCILED` z niezerowym residuum | nowa; **dokładnie to**, co dziś jest `logger.info` (`db/bigquery.py:387`) |
| `ReconstructionMismatchError` | — | dwie rekonstrukcje dały różny stan | nowa; nazwa dla luki z `refactor-opportunities/research.md:114` |
| `NonPositiveHoldingError` | — | `shares ≤ 0` lub `avg_buy_price ≤ 0` poza `CASH` | nowa; zastępuje anonimowe 422 (`src/api.py:1149`) |
| `UnknownInstrumentError` | — | ticker spoza słownika | nowa; zastępuje anonimowe 422 (`src/api.py:1160`) |
| `AggregateViewIsReadOnlyError` | — | próba zapisu do widoku „Wszystkie" | nowa; nazwa dla ostrzeżenia z `src/api.py:427-428` |
| `PortfolioRepository` | **repozytorium portfela** | `load` / `save`; jedno wejście zamiast pięciu | nowa |
| `Portfolio.shares_on(day)` | — | stan posiadania dnia D | nowa; przejmuje rolę CTE `shares_on_day` (`db/bigquery.py:1091`) |
| `Portfolio.assert_agrees_with(...)` | — | N-C2 jako asercja, nie jako pomiar sprzed trzech dni | nowa |

### 5.5. Ryzyka planu

| Ryzyko | Waga | Mitygacja |
| -- | -- | -- |
| Transakcja BQ nie obejmie trzech DML w budżecie 60 s | **wysoka** | F0/F3 weryfikują to round-tripem **przed** kodem; plan B w §4.8 |
| Fail-fast wywala produkcję na zastanych danych | wysoka | F0 mierzy stan **przed** F4; odczyt nie rzuca; F6 klasyfikuje wiersze |
| `Field(gt=0)` to zmiana konwencji (`Field` nieużywane w `src/api.py`) | średnia | jawnie oznaczone w PR-ze; bramka `ai-code-review` może to podnieść |
| Przepięcie 5 tras rusza `tests/e2e/conftest.py` (`:588`, `:619`, `:798-799`, `:844-845`) | średnia | F4 przed F7; e2e konftest ma być zmieniony **razem** z trasami, nie po |
| Round-trip nie biegnie w CI, więc F3/F5 nie są chronione regresyjnie | średnia | znany dług (`refactor-opportunities/research.md:59`, `:119`); ten plan go **nie rozwiązuje** i tego nie udaje |
| N-C12 (porządek totalny) nadal nie jest domknięty | niska | test N9 przypina objaw na granicy agregatu; naprawa źródła (`ORDER BY occurred_at, external_id`) to kandydat C3, poza tym planem |

---

## Podsumowanie w jednym zdaniu

Ten produkt ma jedną regułę, którą potrafi wyliczyć w dwóch zapytaniach, nazwać w dwóch językach
i wypisać na produkcji — *„stan posiadania musi dać się objaśnić operacjami"* — i nie ma ani
jednego miejsca, w którym mógłby jej **odmówić**; agregat `Portfolio` istnieje po to, żeby takie
miejsce było dokładnie jedno.
