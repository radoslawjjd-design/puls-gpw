# Lessons Learned

> Append-only register of recurring rules and patterns. Re-read at start by /10x-frame, /10x-research, /10x-plan, /10x-plan-review, /10x-implement, /10x-impl-review.

## GCP client initialization — load_dotenv + ADC quota project

**Context**: db/bigquery.py, main.py — każdy moduł inicjalizujący klienta Google Cloud

**Problem**: Plan nie uwzględnił dwóch operacyjnych wymagań odkrytych przy F-02:
1. `load_dotenv()` musi być wywołane w entry point *przed* importami modułów GCP
   — `BIGQUERY_DATASET` i `GOOGLE_CLOUD_PROJECT` są czytane przy imporcie modułu
2. Lokalny ADC może mieć `quota_project_id` ustawiony na inny projekt niż
   `GOOGLE_CLOUD_PROJECT`, co powoduje 403 na każdym wywołaniu BQ API

**Rule**: Przy planowaniu każdego change'a który:
- Dodaje nowy klient GCP (BQ, Cloud Storage, Pub/Sub, itp.) — uwzględnij
  `with_quota_project` guard (z `hasattr`) w fazie inicjalizacji klienta
- Dodaje nowy entry point skrypt — uwzględnij `load_dotenv()` jako pierwszy
  import przed jakimkolwiek modułem czytającym env vars

**Applies to**: Każdy change z nowym klientem GCP lub nowym entry pointem pipeline'u

## Gemini JSON output — trailing comma

**Context**: `src/analyzer.py` — `_call_analysis()` z `response_mime_type="application/json"`

**Problem**: Gemini Flash (`gemini-2.5-flash-lite`) pomimo `response_mime_type="application/json"`
zwraca czasem JSON z trailing comma (np. `{"a": 1,}`), którego Python `json.loads` nie akceptuje
→ `JSONDecodeError`. Failure rate ~14% (3/22) w teście produkcyjnym 2026-06-07.

**Rule**: Przy każdym `json.loads(response.text)` z Gemini — użyj parsera tolerującego trailing
commas zamiast stdlib `json`. Opcje (w kolejności preferencji):
1. `import json5; json5.loads(response.text)` — dodać `json5` do `pyproject.toml`
2. Regex strip przed parsowaniem: `re.sub(r",\s*([}\]])", r"\1", response.text)`

**Applies to**: Każdy call Gemini z `response_mime_type="application/json"` w tym projekcie

---

## M4L1: Skalowanie kontekstu — lean root + sygnały eskalacji

**Context**: AGENTS.md / CLAUDE.md w miarę rozrostu projektu

**Rule**: Root `AGENTS.md` to spis treści, nie encyklopedia. Cel: poniżej ~200-300 linii.
Drabina dojrzałości (wchodzisz _na żądanie_, nie z góry):
1. **root AGENTS.md + centralized `context/`** — punkt startowy każdego projektu (w tym puls-gpw)
2. **AGENTS.md per moduł** — gdy moduł ma specyficzne konwencje, których nie zmieścisz w rocie
3. **własny `context/` per moduł** — gdy moduł wymaga dedykowanego PRD/roadmapy, osobny zespół

**Sygnały eskalacji** (tylko gdy):
- root spuchnie i jest nieczytelny (>300 linii),
- agent wielokrotnie gubi kontekst modułu mimo poprawek w rocie,
- moduł zyskuje własny deploy albo właściciela.

**Badania (dokładne liczby z linków)**:
- Paper 1 (10 repos, 124 PRs): AGENTS.md → −28.64% mediany czasu, −16.58% tokenów wyjściowych, porównywalna jakość
- Paper 2: nadmiarowy kontekst → koszt +>20%, gorsze wyniki. Winowajca: **zbędne wymagania** (nie sama długość) — zachęcają agenta do szerszej eksploracji (więcej testów, więcej przeglądania plików)

**Lazy loading (Claude Code)**: pliki CLAUDE.md w podkatalogach ładują się leniwie gdy agent wejdzie do danego katalogu — nie wszystkie od razu. Po `/compact` root CLAUDE.md jest re-czytany z dysku; zagnieżdżone NIE są automatycznie re-injektowane.

**Praktyki z oficjalnych Claude Code docs** (nie było w lekcji):
- Cel: **poniżej 200 linii** per plik (nie 300)
- CLAUDE.md to **user message po system prompt** — nie jest system promptem; brak gwarancji bezwzględnego przestrzegania
- **Komentarze HTML** (`<!-- treść -->`) usuwane z kontekstu — notatki dla ludzi bez kosztów tokenów
- **`@path` importy** ładują się przy starcie i wchodzą do kontekstu — nie oszczędzają tokenów, tylko organizują
- **`.claude/rules/` + frontmatter `paths:`** — reguły ładowane tylko dla pasujących plików (prawdziwa oszczędność tokenów)
- Claude Code czyta `CLAUDE.md`, nie `AGENTS.md` — jeśli repo ma AGENTS.md: `@AGENTS.md` w CLAUDE.md

**Applies to**: Każda decyzja o rozbudowie struktury plików kontekstowych projektu

---

## M4L2: Wide Scan → Mapa projektu (legacy)

**Context**: Analiza nieznanego lub legacy repo przed dokonaniem zmian

**Problem**: "hej Agent, przeczytaj całe repo" przepala okno kontekstowe bez dobrego wyniku.

**Rule**: Dwuetapowy proces — **Wide Scan** (tanie CLI) → **Deep Focus** (agent na wybranym obszarze):

**Wide Scan** — 3 składowe mapy projektu:
1. **Terytorium** (`artifact-1-territory.md`): git history — `TOP 10 najczęściej modyfikowanych folderów/plików`, podział kwartalny, co-change (pary/trójki katalogów w tych samych commitach)
2. **Struktura** (`artifact-2-structure.md`): `dependency-cruiser` (JS/TS) — cykle, warstwy, metryki Ca/Ce, cienkie wejścia vs centra; dla Python: `tach`/`pydeps`; dla Go: `goda`
3. **Kontrybutorzy** (`artifact-3-contributors.md`): kto ma kontekst konkretnych obszarów, filtruj boty i agentów AI

**Finalna synteza** (`context/map/repo-map.md`): TL;DR, teren, powiązania, strefy ryzyka, kogo zapytać, pierwszy dzień (5-8 plików), ograniczenia.

**Kluczowe reguły**:
- Drzewo katalogów to statyczny snapshot — nie mówi o dynamice projektu
- Graf zależności + history + contributors razem = mapa; każde z osobna to hipoteza
- Etykieta bez dowodu to domysł; zapisuj `unknowns`

**Applies to**: Każde wejście w nieznane lub legacy repozytorium przed planowaniem zmian

---

## M4L3: Deep Focus — analiza feature z ast-grep

**Context**: Po zbudowaniu mapy projektu, przed refaktoryzacją

**Process**: Mapa z M4L2 → wybierz jeden przepływ → `/10x-research` z 3 równoległymi sub-agentami:
1. **Trace e2e** — sekwencja kroków file:line + diagram Mermaid
2. **Luki w testach** — które metody/gałęzie nie mają pokrycia
3. **Blast radius** — co musi zmienić się razem (graf statyczny + co-change z gita)

Raport ma dwie sekcje: **Feature overview** (przepływ, nie spis plików) + **Technical debt** (konkretne ryzyka).

**ast-grep** — weryfikacja twierdzeń strukturalnych z raportu:
- Wzorce w składni danego języka (np. `$X.Post().Save($$$A)` dla Go)
- Instalacja: `npm i -g @ast-grep/cli` (lub brew, cargo, pip)
- **Reguła**: używaj ast-grep dla precyzji, ale **każde zero potwierdzaj klasycznym grepem** (zero = zły wzorzec lub brak wystąpień — musisz odróżnić)
- ast-grep potrafi też przepisywać kod (`-r` rewrite) — na etapie analizy tylko czytamy

**Prompt weryfikacji**: "Wypisz wszystkie twierdzenia STRUKTURALNE (liczby call-site'ów, 'tylko tutaj', 'zawsze przez X')… dla każdego zbuduj wzorzec ast-grep, wywołaj i porównaj z twierdzeniem"

**Connascence — pełna taksonomia** (z connascence.io):
- **Statyczna** (wykrywalna analizą kodu): Name, Type, Meaning/konwencja, **Position/kolejność**, Algorithm
- **Dynamiczna** (wymaga runtime): Execution/kolejność wywołań, Timing, Value, Identity
- Trzy osie siły: Strength (jak trudna w refaktorze), Locality (bliskość w kodzie), Degree (liczba elementów)
- Dynamiczna jest groźniejsza: nic jej nie pilnuje narzędziowo

**Applies to**: Każda analiza technicznego długu lub feature przed planowaniem refaktoryzacji

---

## M4L4: Refaktoryzacja — explore → decide → plan (guard-first)

**Context**: Po Deep Focus (research.md z sekcjami Feature overview + Technical debt)

**Antywzorzec**: "zrefaktoruj ten moduł" — brak celu, historii i drogi odwrotu → 40 plików diff bez wartości.

**Trzy perspektywy przed decyzją**:
1. **Docelowy kształt** — spektrum archetypów: Transaction Script → Table Module → Domain Model + Service Layer (nie ranking, każdy ma próg opłacalności)
2. **Historia i intencjonalność** — ADR-y lub archeologia gita (`git log -L`, blame, PR-y). Werdykt per kandydat: **świadome ograniczenie** vs **przypadkowa złożoność**
3. **Odwracalna droga** — Strangler Fig, Branch by Abstraction, metoda Mikado (spróbuj → cofnij → rozwiązuj od liści)

**Reguła**: `guard, nie przebudowa` — świadome ograniczenie dostaje tanią osłonę (test), nie zmianę kształtu.

**Workflow**:
```
/10x-new refactor-opportunities  (jawna intencja: eksploracja ≠ refaktor)
/10x-research refactor-opportunities  (3 sub-agenty: kształt, historia, wykonalność)
→ przeczytaj raport
→ zweryfikuj ast-grepem twierdzenia strukturalne
/10x-plan refactor-opportunities  (bramka decyzyjna: KTÓREJ opcji realizujemy?)
```

**Własności planu**:
- Charakteryzacja testem PRZED dotknięciem kodu
- Fazy = osobne, odwracalne commity
- Mechanizm ląduje na zielono, egzekwowanie włącza się osobno
- Jawna sekcja "czego NIE robimy"

**Branch by Abstraction — 5 kroków** (z martinfowler.com):
1. Identyfikuj moduł do zastąpienia
2. Utwórz warstwę abstrakcji (interfejs) między kodem klienta a starym dostawcą
3. Migruj cały kod klienta do używania abstrakcji (dodaj testy przy okazji)
4. Zbuduj nową implementację za abstrakcją
5. Stopniowo przełączaj na nową implementację, usuń starą
→ System pozostaje wdrażalny na każdym kroku. Użyj Feature Flags do testowania nowej implementacji.

**Kwadrant długu technicznego — kiedy spłacać**:
- **Prudent-Deliberate** (świadomy/rozsądny): spłacaj gdy odsetki (koszty utrzymania) rosną
- **Prudent-Inadvertent** (odkryty w trakcie): planuj czas refaktoryzacji, nieunikniony u najlepszych zespołów
- **Reckless-Deliberate** (świadomy/nierozsądny): unikaj — długoterminowo najdroższy
- **Reckless-Inadvertent** (bałagan): spłacaj natychmiast
- Praktyczna reguła Cunninghama: **spłacaj dług tylko w kodzie który aktywnie modyfikujesz** — stabilny brzydki kod można zostawić; kruft degeneruje szybkość "w tygodniach, nie miesiącach"

**Joel Spolsky — dlaczego NIE przepisywać od zera**:
- Stary kod zawiera poprawki błędów odkrytych w rzeczywistości (każda linia = wiedza)
- Konkurenci dostają 2-3 lata na nowe funkcje podczas gdy ty odbudujesz
- Nowy kod popełni "większość starych błędów ponownie"
- Przykłady: Netscape 6.0, Borland Quattro Pro, MS Word Pyramid

**Applies to**: Każda decyzja o refaktoryzacji kodu legacy lub technicznego długu

---

## M4L5: DDD modernizacja — odkryj domenę, potem deleguj agentowi

**Context**: Projekt post-MVP, gdy domena zaczyna boleć (3xAccount, byt z PRD nie istnieje w kodzie, cotygodniowe skrypty ratunkowe, wymiana biblioteki = sprint)

**Nie kurs DDD** — bierzemy minimum potrzebne, 3 artefakty + Event Storming.

**Artefakt 1 — destylacja domeny** (`context/domain/01-domain-distillation.md`):
Prompt 5-krokowy: odkryj kontekst → ubiquitous language → subdomeny (Core/Supporting/Generic) → kandydaci na agregaty + niezmienniki → lista rozjazdów MODEL vs KOD → ranking refaktoru.

**Artefakt 2 — niezmiennik + agregat** (`context/domain/02-invariant-aggregate-refactor.md`):
Prompt: odkryj → identyfikuj niezmienniki → klasyfikuj na 3 osiach (rdzeniowość × rozsmarowanie × egzekwowanie) → diagnoza → projekt agregatu-strażnika (preconditions, named domain errors, one transaction).

**Artefakt 3 — Anti-Corruption Layer** (`context/domain/03-anti-corruption-layer.md`):
Prompt: identyfikuj przeciekające zależności → kryterium sukcesu: `grep po nazwie pakietu zwraca tylko pliki w ACL/`. Port (interfejs domenowy) + adapter (konkretna biblioteka).

**Event Storming**: narzędzie `event-storming-canvas` (czysty Node.js), agent jako moderator edytuje `board.json`, tablica odświeża się live. Fazy: chaotic-exploration → timeline → hotspots → aggregates.

**DDD zasila istniejący workflow**:
```
/10x-research @context/domain/01-domain-distillation.md
/10x-plan @context/domain/02-invariant-aggregate-refactor.md
/10x-roadmap  (hotspoty z Event Stormingu jako kolejność zmian)
```

**Kiedy DDD**: po MVP, gdy domena zaczyna boleć — nie od pierwszego dnia.

**Ekspert domenowy z LLM**: traktuj jak generator hipotez, nie źródło prawdy. Cross-check: dokumentacja źródłowa + dwa różne modele + konfrontacja z własnym kodem (plik:linia).

**Applies to**: Projekt post-MVP wymagający głębszego dopasowania kodu do domeny biznesowej

---

## BigQuery — kolumny o nazwach reserved keywords + limity mockowanych testów

**Context**: `db/bigquery.py` — każdy ręcznie sklejany SQL (INSERT/UPDATE/SELECT) odwołujący
się do kolumny, której nazwa koliduje ze słowem zarezerwowanym BQ (`window`, `range`, `rows`,
`hash`, `groups`, `partition`, itp.)

**Problem** (PUL-29, 2026-06-14): kolumna `x_posts.window` w INSERT bez backticków
(`(x_post_id, window, ...)`) → `400 Syntax error: Unexpected keyword WINDOW`. `WINDOW` jest
słowem zarezerwowanym (funkcje okienkowe). Wszystkie testy jednostkowe `save_x_post`
przeszły na zielono, bo mockują klienta BQ — string SQL nigdy nie trafił do parsera. Bug
wyszedł dopiero w round-tripie na realnym BigQuery (`scripts/test_bq.py`).

**Rule**:
1. Każdą nazwę kolumny będącą reserved keyword **backtickuj** w treści SQL:
   `` `window` `` (parametr `@window` jest OK — nazwy parametrów nie kolidują).
   Pełna lista: https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical#reserved_keywords
2. Mockowane testy BQ **nie weryfikują składni SQL** — traktuj round-trip na realnym BQ
   (`scripts/test_bq.py`) jako obowiązkowy krok manualnej weryfikacji każdego change'a, który
   dodaje/zmienia ręcznie sklejany SQL. Dodaj też tani test regresyjny na sam string zapytania
   (`assert "`window`" in insert_q`).
3. Skrypt round-trip musi wołać `ensure_schema_current()` (nie tylko
   `create_table_if_not_exists()`) — na istniejącej tabeli `create_*` jest no-opem i nie
   dołoży nowej kolumny; migrację kolumn robi wyłącznie `ensure_schema_current()`.

**Applies to**: Każdy change dotykający ręcznie pisanego SQL w `db/bigquery.py`
