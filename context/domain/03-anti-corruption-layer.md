---
title: Anti-Corruption Layer — puls-gpw / Faro
created: 2026-08-08
type: refactor-plan
---

# Anti-Corruption Layer — puls-gpw / Faro

> **Czym jest ten dokument.** Planem jednego refaktoru: wybraniem najgorzej przeciekającej
> zależności zewnętrznej i zaprojektowaniem warstwy, która zamknie ją w jednym pliku.
> Nie jest kodem produkcyjnym — sygnatury i pseudokod służą do rozstrzygnięcia kształtu,
> nie do wklejenia.
>
> **Metoda.** Odkrycie → inwentarz przecieków → klasyfikacja → diagnoza → projekt ACL →
> dowód izolacji → mierzalne kryterium sukcesu + plan faz. Każde twierdzenie ma `plik:linia`,
> które realnie otworzyłem. Wyniki negatywne (przeciek, którego **nie ma**) są zapisane
> wprost — to też wynik.
>
> **Buduje na** `context/domain/01-domain-distillation.md` (destylacja domeny) oraz
> `context/map/artifact-2-structure.md` (graf zależności i granice warstw).
>
> **Stan na commit `e4b2066`** (2026-08-07), 80. dzień życia repozytorium.

---

## KROK 0 — Kontekst

### Stack i manifest zależności

Python 3.13 + FastAPI + Pydantic + BigQuery + Cloud Run, zarządzane przez `uv`
(`pyproject.toml:6`, `AGENTS.md:3`). Manifest runtime'owy to **17 pakietów**
(`pyproject.toml:7-26`); dev to kolejne 8 (`:28-41`). Kandydaci na przeciek to te,
które niosą **własne typy** albo **własne dziwactwa protokołu**:

| Pakiet | `pyproject.toml` | Rola |
| -- | -- | -- |
| `google-cloud-bigquery` | `:14` | persystencja |
| `google-genai` | `:15` | Vertex AI / Gemini — analiza, bramka, generator postu, wizja |
| `firebase-admin` | `:21` | tożsamość |
| `pyjwt` | `:22` | sesja |
| `httpx` | `:10` | klient HTTP (scraping + Identity Toolkit) |
| `openpyxl` | `:24` | parser eksportu XTB |
| `tweepy` | `:20` | publikacja na X |
| `beautifulsoup4` | `:11` | parsowanie HTML |
| `json5` | `:19` | **nie jest zależnością własną — to obejście defektu Gemini** (patrz KROK 1) |

### Warstwy

Za `context/domain/01-domain-distillation.md:47-53` i `tach.toml:15-46`:

| # | Warstwa | Gdzie |
| -- | -- | -- |
| L1 | Punkty wejścia (7) | `main.py`, `post_main.py`, `api_main.py`, `company_stats_main.py`, `etf_quotes_main.py`, `cost_report_main.py`, `_run_generate_post.py` |
| L2 | API / kompozycja | `src/api.py` (1568 linii, 32 endpointy) |
| L3 | Serwisy / adaptery `src/` | `src/analyzer.py`, `src/post_generator.py`, `src/gemini_client.py`, `src/auth.py`, `src/notifier.py`, `src/http_client.py`, … |
| L4 | Domena „czysta" (liście) | `src/portfolio_lots.py`, `src/post_selection.py`, `src/tickers.py`, `src/post_supervisor.py` |
| L5 | Persystencja | `db/bigquery.py` (4493 linie) |
| L6 | Frontend | `static/index.html` (6326 linii, jeden plik) |
| L7 | Testy | `tests/` (30 plików + `tests/e2e/conftest.py`, 910 linii) |

### Czego szukałem w dokumentach: deklaracji wymienialności

Przeszukałem `context/foundation/` i `AGENTS.md` pod kątem słów `swap`, `replace`,
`vendor`, `lock-in`, `provider`, `pluggable`, `wymien`. **Znalazłem dokładnie dwie
deklaracje wymienialności i jedną deklarację przeciwną:**

1. **SMTP jest jawnie wymienialny** — `prd.md:94`: *„Wybór dostawcy email (SMTP/SendGrid/etc.)
   to decyzja implementacyjna"*. Destylacja klasyfikuje ten obszar jako **GENERIC**
   (`01-domain-distillation.md:231`).
2. **Źródło komunikatów jest jawnie kwestionowane** — `prd.md:156` (OQ-1): jeśli istnieje
   oficjalny feed GPW/NC, *„scraping HTML jest zbędny i mniej stabilny"*.
3. **Gemini jest jawnie NIEwymienialny** — `context/archive/2026-06-15-ci-cd-code-review/plan.md:39`
   odrzuca Claude Code Action, bo koliduje z *„the Gemini-only/no-cost constraint"*.

**Wniosek dla KROKU 2, ważny i niewygodny.** Ten projekt **nigdzie nie napisał, że dostawca
LLM ma być wymienialny**. Nie mogę więc oprzeć wyboru #1 na rozjeździe „dokument obiecuje
wymienialność, kod jej nie dowozi" — bo takiej obietnicy nie ma. Zamiast tego mam coś
mocniejszego i konkretniejszego: **dwa miejsca, w których projekt sam, na piśmie, uznał
zasięg tej zależności za problem do naprawienia** (`src/post_selection.py:1-15`,
`tach.toml:4-14`). Kryterium (c) rozstrzygam więc nie przez obietnicę wymienialności,
tylko przez **udokumentowaną, niespłaconą intencję ograniczenia zasięgu**. To ta sama klasa
sygnału, tylko zapisana precyzyjniej.

---

## KROK 1 — Inwentarz przecieków

### Uwaga metodyczna: dlaczego każde zero jest tu potwierdzone grepem

Reguła zadania — *licz `ast-grep` dla precyzji, ale każde ZERO potwierdzaj klasycznym grepem* —
zapracowała w tej sesji dosłownie. Poniższe polecenie **zwróciło pustą listę**:

```
$ ast-grep run -l python -p 'import google.genai as $A' -l
error: a value is required for '--lang <LANG>' but none was supplied
$ echo $?
2
```

`-l` to skrót `--lang`, więc drugie `-l` zjadło wzorzec i `ast-grep` **wyszedł z kodem 2**,
nie z zerem trafień. Bez kontrolnego `rg` odczytałbym „pakiet nigdzie nie występuje".
Wszystkie liczby w tym dokumencie pochodzą z `ast-grep --json=compact` (parsowanego, nie
grepowanego) **i** z kontrolnego `rg`; rozbieżności są zaznaczone.

### Tabela zbiorcza

Liczby dotyczą **kodu produkcyjnego** (L1–L6). Kolumna „test/skrypt" jest osobno, bo
`scripts/` to narzędzia jednorazowe, a `tests/` to warstwa, która przecieki **odzwierciedla**,
nie tworzy.

| # | Pakiet | Pliki produkcyjne | Warstwy | Test / skrypt | Typy biblioteki poza modułem właściciela? | Werdykt |
| -- | -- | -- | -- | -- | -- | -- |
| **P-1** | **`google-genai`** | **6** | **4** (L2, L3, L4, L5) | 3 testy + 2 artefakty w `.claude/` | **tak** — `genai.types.*` w 3 modułach, defekt SDK w 5 | **PRZECIEK #1** |
| P-2 | `firebase-admin` | 1 (`src/auth.py`) | 1 (L3, ale moduł łączy router HTTP z SDK) | 2 skrypty + 4 testy | typy SDK wewnątrz handlerów HTTP | przeciek lokalny, groźny inaczej |
| P-3 | `httpx` | 2 | 1 (L3) | 1 test | nie | drobny — obejście własnego klienta |
| P-4 | `smtplib` / `email.mime` | 2 | 2 (L1, L3) | — | **tak** — `_run_generate_post.py:199` sięga po prywatne `_smtp_creds` | drobny, ale przy jawnej deklaracji `prd.md:94` |
| P-5 | `beautifulsoup4` | 7 | 1 (L3) | 1 test | nie — `BeautifulSoup` nie opuszcza funkcji parsujących | szeroki, ale płaski |
| P-6 | `openpyxl` | 1 (`src/brokers/xlsx_reader.py`) | 1 (L3) | 4 testy | nie — ale `src/api.py:396` zna jego charakterystykę pamięciową | **prawie czysto** |
| P-7 | `google-cloud-bigquery` | 1 (`db/bigquery.py`) | 1 (L5) | 8 testów + 9 skryptów | **nie** — zweryfikowane, patrz niżej | **czysto** |
| P-8 | `pyjwt` | 1 (`src/auth.py`) | 1 (L3) | 3 testy | nie | czysto |
| P-9 | `tweepy` | 1 (`src/x_publisher.py`) | 1 (L3) | — | nie | czysto |

### Dwa wyniki negatywne, które trzeba zapisać wprost

**N-1. Firebase NIE jest wołany po stronie klienta.** Zadanie wskazywało to jako najsilniejszy
sygnał na granicy klient/serwer. **Sprawdziłem i tego przecieku nie ma.** W `static/index.html`
słowo „Firebase" pada dwa razy i oba razy w **komentarzu** (`static/index.html:1875`,
`:2216`); nie ma `initializeApp`, nie ma `signInWith*`, nie ma `apiKey` firebase'owego, nie ma
skryptu z `gstatic.com` (jedyne `gstatic` to `preconnect` do fontów, `:13`). Front rozmawia
wyłącznie z naszym API: token sesji leży w `sessionStorage` pod nazwą `apiKey`
(`static/index.html:1772`) i jedzie nagłówkiem `X-API-Key` (~35 wywołań `fetch`). Mylącą jest
tu **nazwa zmiennej**, nie architektura — to nasz JWT (`src/auth.py:401`), nie klucz Firebase.
Granica klient/serwer jest tu **czysta i to jest dobra wiadomość**, którą warto mieć zapisaną,
zanim ktoś ją niechcący przekroczy.

**N-2. Typy `google-cloud-bigquery` nie opuszczają `db/`.** Polecenie kontrolne:

```
$ rg -n --glob '*.py' --glob '!.venv/**' --glob '!db/**' --glob '!scripts/**' \
     'bigquery\.(ScalarQueryParameter|ArrayQueryParameter|SchemaField|QueryJobConfig|Client|Row|LoadJobConfig|Table)' -l
(brak wyników)
```

`ast-grep -p 'from google.cloud import $$$A'` daje 22 trafienia, ale w kodzie produkcyjnym
tylko dwa i oba w `db/bigquery.py:39-40`. Wiersze wychodzą z warstwy danych jako `dict`
(`db/bigquery.py:1636`, `:1725`) albo jako dataclassy/NamedTuple. **`src/api.py` nie zna
`bigquery.Row`.** To jest, wbrew rozmiarowi tego pliku, dobrze utrzymana granica — i dlatego
`google-cloud-bigquery` **nie** jest kandydatem na #1. (Prawdziwy dług `db/bigquery.py`
to 51 nazw importowanych do `src/api.py` i 62 łatki w `tests/e2e/conftest.py` —
`artifact-2-structure.md:99-105` — ale to problem *testowalności*, nie *korupcji przez typ obcy*.)

### P-1 — pełna lista plików, które dziś „znają" `google-genai`

Rozróżniam trzy stopnie wiedzy, bo tylko razem pokazują prawdziwy zasięg:

**(A) Import SDK i jego typów — 3 pliki, wszystkie w L3**

| `plik:linia` | Co dokładnie |
| -- | -- |
| `src/gemini_client.py:8` | `import google.genai as genai` |
| `src/gemini_client.py:21` | `_genai_client: genai.Client \| None` — typ SDK w adnotacji modułowej |
| `src/gemini_client.py:25` | `def get_client() -> genai.Client` — **typ SDK w sygnaturze publicznej** |
| `src/gemini_client.py:30-34` | `genai.Client(vertexai=True, project=…, location=…)` |
| `src/gemini_client.py:136` | `genai.types.Part.from_bytes(...)` |
| `src/gemini_client.py:145-148` | `genai.types.GenerateContentConfig(...)` |
| `src/analyzer.py:7` | `import google.genai as genai` |
| `src/analyzer.py:9` | `from google.genai import errors as _genai_errors` |
| `src/analyzer.py:170-173` | `genai.types.GenerateContentConfig(...)` — rekonstrukcja #2 |
| `src/analyzer.py:180-181` | `except _genai_errors.ClientError as exc: if exc.code == 429` |
| `src/analyzer.py:201-204` | `genai.types.GenerateContentConfig(...)` — rekonstrukcja #3 |
| `src/analyzer.py:208-209` | drugi, identyczny `except _genai_errors.ClientError` |
| `src/post_generator.py:10` | `import google.genai as genai` |
| `src/post_generator.py:462-465` | `genai.types.GenerateContentConfig(...)` — rekonstrukcja #4 |

**Cztery wywołania `client.models.generate_content` w kodzie produkcyjnym** —
`src/gemini_client.py:142`, `src/post_generator.py:459`, `src/analyzer.py:167`,
`src/analyzer.py:198` — i **cztery niezależne rekonstrukcje `GenerateContentConfig`**,
każda z tą samą parą `system_instruction=` + `response_mime_type="application/json"`.

**(B) Wiedza o defekcie SDK (`json5`) — 5 plików, 4 warstwy**

`json5` nie jest w tym projekcie zależnością własną. Jest **obejściem konkretnego, zmierzonego
defektu Gemini**: `context/foundation/lessons.md:23-36` zapisuje *„Gemini Flash
(`gemini-2.5-flash-lite`) pomimo `response_mime_type="application/json"` zwraca czasem JSON
z trailing comma… Failure rate ~14% (3/22) w teście produkcyjnym 2026-06-07"*.

| `plik:linia` | Warstwa | Co parsuje |
| -- | -- | -- |
| `src/gemini_client.py:150` | L3 | `json5.loads(response.text)` — surowa odpowiedź modelu |
| `src/analyzer.py:175` | L3 | `json5.loads(response.text)` — surowa odpowiedź modelu |
| `src/analyzer.py:206` | L3 | `json5.loads(response.text)` — surowa odpowiedź modelu |
| `src/post_generator.py:467` | L3 | `json5.loads(response.text)` — surowa odpowiedź modelu |
| `src/post_generator.py:387` | L3 | `json5.loads(raw)` — **`structured_analysis` z bazy** |
| `src/post_selection.py:38` | **L4 (domena czysta)** | `json5.loads(raw)` — `structured_analysis` z bazy |
| `src/api.py:238` | **L2 (API)** | `json5.loads(raw)` — `structured_analysis` z bazy |

**(C) Wiedza strukturalna — 1 plik w L4 i jedna krawędź w L5**

`src/post_selection.py:1-15` to docstring, który sam nazywa problem:

> *„Dependency-light leaf module (imports only `json5` + `logging`) so the data-access layer
> (`db/bigquery.py`) can call it without dragging the `google.genai` import chain
> (`post_generator` → `gemini_client`) into the DB layer."*

Ten moduł **istnieje między innymi po to, żeby `google.genai` nie wjechał do warstwy danych.**
Efektem jest krawędź `db.bigquery → src.post_selection` (`db/bigquery.py:49`), czyli jedno
z czterech naruszeń warstw wpisanych do `tach.toml:4-14` jako `KNOWN VIOLATION` z adnotacją
*„Fix: move … to a shared leaf package (future change)"* i komentarzem `# SMELL` (`tach.toml:45`).
`artifact-2-structure.md:53-57` wylicza te cztery krawędzie.

**(D) Poza grafem — 2 artefakty w `.claude/`, katalogu gitignorowanym (`.gitignore:22`)**

- `.claude/rules/gemini-ai.md` — reguła *„Every `json.loads(response.text)` from Gemini must use
  a trailing-comma-tolerant parser"*. **Jedyny egzekutor niezmiennika z (B) to plik, którego
  nie ma w repozytorium.** Potwierdzone: `git check-ignore -v .claude/rules/gemini-ai.md`
  → `.gitignore:22`.
- `.claude/skills/portfolio-xpost/SKILL.md` — jedyny konsument
  `src/gemini_client.extract_portfolio_snapshot` (`src/gemini_client.py:122`). W kodzie
  produkcyjnym funkcja nie jest wołana **ani razu**; poza testami woła ją wyłącznie skill.
  Ósmy konsument SDK, niewidoczny dla `tach` i dla grafu z artefaktu 2.

**(E) Ślad w module, który importują wszyscy**

`src/exceptions.py:25` — `AnalysisError`: *„Raised when **Gemini** analysis fails…"*. Moduł
o fan-inie 11, nazwany w `artifact-2-structure.md:32` *„prawdziwym fundamentem"*, nosi nazwę
dostawcy w docstringu wspólnego słownika błędów.

**(F) Warstwa testów — 3 niezależne rekonstrukcje kształtu SDK**

| `plik:linia` | Co robi |
| -- | -- |
| `tests/test_analyzer.py:24` | `def _mock_client(*response_dicts)` — ręcznie odtwarza `client.models.generate_content(...).text` |
| `tests/test_gemini_client.py:10` | `def _mock_client(response_text)` — to samo, druga kopia |
| `tests/test_post_generator.py:54` | `def _mock_client(response_text)` — to samo, trzecia kopia |

Trzy pliki testowe **niezależnie reprodukują kształt obiektu biblioteki**
(`client.models.generate_content(...)` → obiekt z atrybutem `.text`). To jest podręcznikowy
objaw z listy sygnałów: *zduplikowana rekonstrukcja obiektów/typów biblioteki w kilku miejscach*.

### Przecieki drugorzędne — krótko, żeby nie zginęły

- **P-2 Firebase.** SDK żyje w jednym pliku, ale ten plik jest jednocześnie **routerem HTTP**
  (`src/auth.py:351`). Typy wyjątków SDK są łapane wprost w handlerach:
  `firebase_auth.EmailAlreadyExistsError` (`:451`), `UserNotFoundError` (`:561`, `:588`),
  `TooManyAttemptsTryLaterError` (`:421`, `:530`), a `firebase_auth.ActionCodeSettings`
  jest konstruowane w ciele endpointu (`:418`, `:525`). Do tego druga ścieżka do tego samego
  dostawcy — surowy REST przez `httpx` do Identity Toolkit (`:297`, `:317-322`) z ręcznym
  słownikiem kodów błędów (`:300-305`). Moduł **ma** już zalążek ACL: trzy własne wyjątki
  domenowe (`:262-271`) z opisanym mapowaniem na kody HTTP. Brakuje tylko rozdzielenia portu
  od routera. To dobry kandydat #2, nie #1 — bo dotyka jednego pliku i jednej warstwy.
- **P-4 SMTP.** `src/notifier.py:4,8,146,150` to właściwy adapter, ale `_run_generate_post.py:10-14`
  i `:207-213` **duplikuje cały wysył** (własne `smtplib.SMTP` + `MIMEMultipart`), a w `:199`
  sięga po **prywatną** funkcję `src.notifier._smtp_creds`. To jedyny przeciek w projekcie,
  któremu towarzyszy jawna deklaracja wymienialności (`prd.md:94`) — ale skala jest mała
  i dotyczy ręcznego punktu wejścia.
- **P-3 httpx.** `src/http_client.py` jest wspólnym klientem (fan-in 7, `artifact-2-structure.md:75`)
  i to on obchodzi blokadę `gpw.pl`. `src/auth.py:18,317` **omija go** własnym `httpx.post`.
  Nie jest to korupcja domeny, tylko obejście własnej konwencji — warto naprawić przy okazji P-2.
- **P-6 openpyxl.** Adapter jest wzorowy (`src/brokers/xlsx_reader.py`), ale `src/api.py:396`
  uzasadnia limit `_IMPORT_MAX_BYTES` **charakterystyką pamięciową openpyxl**. Warstwa API
  zna wewnętrzne zachowanie biblioteki, której nie importuje. Jedna linia komentarza — odnotowuję,
  nie planuję.

---

## KROK 2 — Klasyfikacja i wybór #1

### Ocena na trzech osiach

| Oś | `google-genai` (P-1) | `firebase-admin` (P-2) | `google-cloud-bigquery` (P-7) | `smtplib` (P-4) |
| -- | -- | -- | -- | -- |
| **(a) warstwy / pliki produkcyjne** | **4 warstwy, 6 plików** | 1 warstwa, 1 plik | 1 warstwa, 1 plik | 2 warstwy, 2 pliki |
| **(b) koszt wymiany dziś** | **4 call-site'y, 4 rekonstrukcje configu, 2 zduplikowane pętle 429, 2 zmienne modelu, 7 miejsc `json5`, 3 mocki w testach** | 1 plik, ale wyjątki SDK wplecione w 6 handlerów HTTP | 1 plik, 69 `.result()` — ale nie wychodzi poza warstwę | 2 pliki, w tym jedna kopia przez prywatny symbol |
| **(c) udokumentowana intencja ograniczenia** | **tak, dwukrotnie** — `src/post_selection.py:1-15` (moduł powstał, żeby SDK nie wjechał do `db/`) i `tach.toml:4-14` (`KNOWN VIOLATION` + „Fix: … (future change)") | nie | nie | tak, jako wymienialność dostawcy — `prd.md:94` |
| **(d) egzekwowanie** | **żadne w repo** — reguła leży w `.claude/rules/gemini-ai.md`, katalog gitignorowany (`.gitignore:22`) | konwencja w docstringach (`src/auth.py:262-271`) | `tach.toml` + granica katalogu | brak |

### Werdykt: #1 to `google-genai`

**Trzy powody, każdy sprawdzalny.**

1. **To jedyna zależność zewnętrzna, która przekracza granice warstw — i to w trzech kierunkach
   naraz.** Wychodzi w górę do L2 (`src/api.py:238`), w bok do L4 — modułu, którego cały sens
   to bycie „dependency-light leaf" (`src/post_selection.py:1-15`, `:38`), i w dół do L5, bo
   `db/bigquery.py:49` importuje ten liść, płacąc za to jednym z czterech naruszeń warstw
   z `tach.toml`. Żadna inna zależność nie robi więcej niż jeden krok.

2. **Projekt już dwa razy zapłacił za brak ACL i oba rachunki są wystawione na piśmie.**
   `src/post_selection.py` **istnieje** dlatego, że nie ma warstwy izolującej SDK — jego
   docstring mówi to wprost. `tach.toml:4-14` rejestruje wynikowe naruszenie jako znane
   i odkłada naprawę. Dodanie ACL nie jest tu pomysłem z zewnątrz; jest **spłatą długu,
   który projekt sam u siebie zaksięgował**.

3. **Niezmiennik jest realny, zmierzony i egzekwowany przez plik spoza repozytorium.**
   ~14% odpowiedzi Gemini było niepoprawnym JSON-em (`lessons.md:27-30`, pomiar z 2026-06-07).
   Reguła „zawsze parser tolerujący" istnieje wyłącznie w `.claude/rules/gemini-ai.md`, a
   `git check-ignore` potwierdza, że tego pliku nie ma w repo. Nowy `json5.loads` bez tolerancji
   przejdzie code review, testy i CI. **Ochroną jest dziś dyscyplina, nie kod.**

**Czego nie twierdzę.** Nie twierdzę, że projekt zamierza zmienić dostawcę LLM — dowody mówią
raczej odwrotnie (`2026-06-15-ci-cd-code-review/plan.md:39`, *„Gemini-only constraint"*).
Wartość tego ACL nie leży w hipotetycznej migracji na innego dostawcę, tylko w tym, że dziś
**cztery warstwy znają defekt jednego dostawcy**, a jego jedyny strażnik jest poza gitem.

---

## KROK 3 — Diagnoza

### D-1. Cztery rekonstrukcje tego samego wywołania

| Miejsce | model | `system_instruction` | `response_mime_type` | retry 429 | parser | walidacja |
| -- | -- | -- | -- | -- | -- | -- |
| `src/analyzer.py:167-176` | `_GEMINI_MODEL` | `_ANALYSIS_SYSTEM_PROMPT` | `application/json` | **tak** (`:180-184`) | `json5` | `_AnalysisResponse` |
| `src/analyzer.py:198-207` | `_GEMINI_MODEL` | `_GATE_SYSTEM_PROMPT` | `application/json` | **tak** (`:208-212`) | `json5` | **brak** — `data["approved"]` gołym indeksem (`:207`) |
| `src/post_generator.py:459-468` | `GEMINI_MODEL` | `_SYSTEM_PROMPT` | `application/json` | **nie** | `json5` | `_PostResponse` |
| `src/gemini_client.py:142-162` | `GEMINI_VISION_MODEL` | `_EXTRACTION_SYSTEM_PROMPT` | `application/json` | **nie** | `json5` | `_PortfolioExtractionResponse` |

Cztery kopie tej samej struktury, **cztery różne poziomy odporności**. Dwie mają backoff na 429,
dwie nie. Trzy walidują schematem, jedna czyta `data["approved"]` bez modelu Pydantic — a to
akurat ten call, który decyduje, czy komunikat przejdzie bramkę antyhalucynacyjną
(`prd.md:87`, FR-008). Rozjazd nie wynika z decyzji; wynika z tego, że **kolejne kopie powstawały
w różnych miesiącach i nikt nie miał jednego miejsca, w którym mógłby ulepszyć wszystkie naraz**.

### D-2. Typ SDK w sygnaturze publicznej

```python
# src/gemini_client.py:25
def get_client() -> genai.Client:
```

To jest formalna definicja przecieku: **funkcja eksportowana z modułu zwraca typ biblioteki**.
Dwa moduły ją importują (`src/analyzer.py:13`, `src/post_generator.py:13`) i muszą znać
protokół tego typu (`.models.generate_content(model=…, contents=…, config=…)`), żeby cokolwiek
z nim zrobić. Dlatego trzy pliki testowe reprodukują ten protokół ręcznie
(`tests/test_analyzer.py:24`, `tests/test_gemini_client.py:10`, `tests/test_post_generator.py:54`).

### D-3. Typ domenowy mieszka w module dostawcy

```
src/portfolio_thread_composer.py:10:  from src.gemini_client import PortfolioPosition
tests/test_portfolio_thread_composer.py:3: from src.gemini_client import PortfolioPosition
```

`PortfolioPosition` i `PortfolioExtraction` (`src/gemini_client.py:103-119`) to **czyste
dataclassy domenowe** — ticker, wartość, zwrot procentowy, waluta. Nie mają w sobie ani jednego
bajtu Gemini. Mimo to mieszkają w module, którego nazwa to nazwa dostawcy, i kompozytor wątku
X musi zaimportować „klienta Gemini", żeby dostać typ pozycji portfela. To przeciek **w drugą
stronę**: nie biblioteka wychodzi do domeny, tylko domena wchodzi do modułu biblioteki.

Warto zauważyć, gdzie w tym samym pliku siedzi wiedza domenowa najwyższej próby:
`src/gemini_client.py:71-72` — *„pozycję »Syn2bio« ZAWSZE pomiń całkowicie — jej cena zakupu
wynosi 0"* — i `:83` `_EXCLUDED_TICKERS`. To jest reguła o dywidendzie rzeczowej S2B, opisana
w `01-domain-distillation.md:377-382` jako **stała właściwość, nie przypadek brzegowy**.
Ta reguła jest dziś zaszyta w prompcie i w filtrze modułu SDK.

### D-4. Najostrzejszy pojedynczy fakt: `json5` chroni przed defektem, który tam nie dociera

Ścieżka zapisu analizy:

```
src/analyzer.py:175   data = json5.loads(response.text)                 # ← surowy Gemini, json5 SŁUSZNE
src/analyzer.py:176   _AnalysisResponse.model_validate(data).model_dump()
src/analyzer.py:272   structured_analysis = json.dumps(analysis_dict, ensure_ascii=False)   # ← stdlib json
                      → kolumna `structured_analysis` STRING (db/bigquery.py:69)
```

To, co ląduje w BigQuery, jest produktem **`json.dumps` ze standardowej biblioteki**
(`src/analyzer.py:272`). Nie może zawierać trailing comma — `json.dumps` fizycznie takiego
wyjścia nie wyprodukuje.

A mimo to trzy miejsca czytają tę kolumnę parserem tolerancyjnym:

```
src/api.py:238             return json5.loads(raw)        # L2 — API
src/post_selection.py:38   structured = json5.loads(raw)  # L4 — domena czysta
src/post_generator.py:387  structured = json5.loads(raw)  # L3
```

**Warstwa API i warstwa domeny czystej stosują obejście defektu Gemini do stringa, którego
Gemini nigdy nie dotknął.** To nie jest błąd funkcjonalny — `json5` czyta poprawny JSON bez
problemu. To jest **dokładna miara korupcji**: dziwactwo dostawcy rozlało się na miejsca,
do których nie ma jak dotrzeć, bo nikt nie miał typu, który powiedziałby *„tu kończy się
odpowiedź modelu, a zaczyna zapisany fakt domenowy"*.

> **`unknown`, który trzeba domknąć przed fazą 6.** Nie odpytywałem produkcyjnego BigQuery.
> Nie wiem, czy w tabeli `announcements` nie ma **starych** wierszy zapisanych zanim
> `_AnalysisResponse.model_validate` weszło do ścieżki — takie wiersze mogłyby zawierać surowy
> tekst modelu. Zanim `json5` zniknie ze ścieżek odczytu, trzeba to sprawdzić zapytaniem
> (`SAFE.PARSE_JSON(structured_analysis) IS NULL`). To jedyna twarda przesłanka blokująca fazę 6.

### D-5. Podsumowanie przecieków przez granice

```
                      ┌──────────────────────────── static/index.html (L6)
                      │  dostaje sparsowany obiekt — CZYSTO ✔
   src/api.py:238 ────┘  json5 — wie o defekcie Gemini            ✘ L2
   src/post_selection.py:38  json5 — „dependency-light leaf"      ✘ L4
        ▲
        │ db/bigquery.py:49  import → jedno z 4 naruszeń tach.toml ✘ L5
        │
   src/analyzer.py ─┐
   src/post_generator.py ─┼─ import google.genai + genai.types    ✘ L3 ×3
   src/gemini_client.py ─┘
        │
        └─ get_client() -> genai.Client  — typ SDK w sygnaturze   ✘
           PortfolioPosition             — typ domeny w module SDK ✘
```

Jedyna granica, która dziś trzyma, to ta ostatnia: **UI dostaje gotowy obiekt domenowy**
(`static/index.html:3480`, `const a = it.structured_analysis || {}` → `a.summary_pl` na `:3485`),
bo `src/api.py:234-240` parsuje po stronie serwera. Ta własność **musi przetrwać refaktor** —
i przetrwa, bo ACL nic w kontrakcie wire nie zmienia.

---

## KROK 4 — Projekt ACL

Trzy artefakty, w kolejności zależności: **value object** (wiedza o kształcie), **port**
(wąski interfejs domenowy), **adapter** (jedyne miejsce z `import google.genai`).

### 4.1. Value object — `StructuredAnalysis`

Jedyne miejsce w systemie, które wie, jak wygląda analiza komunikatu: jak przychodzi od modelu,
jak leży w BigQuery i jakie ma operacje domenowe.

Plik: **`src/model_output.py`** (nowy, liść — importuje wyłącznie stdlib + Pydantic).

```python
"""Analiza komunikatu jako byt domenowy — jedyne miejsce znające jej kształt.

Trzy wejścia, celowo rozdzielone:
  from_model_payload()  — słownik już zdekodowany przez adapter (adapter zna json5)
  from_stored()         — string z kolumny `structured_analysis` (zawsze stdlib-JSON,
                          bo powstał z json.dumps — patrz D-4)
  to_stored()           — kanoniczna serializacja do BigQuery
"""

_VALID_EVENT_TYPES: frozenset[str]   # 15 wartości, przeniesione z src/analyzer.py:21-26
_VALID_SENTIMENTS:  frozenset[str]   # 3 wartości,  przeniesione z src/analyzer.py:45
FALLBACK_EVENT_TYPE = "inne"         # src/analyzer.py:270
FALLBACK_SENTIMENT  = "neutralny"    # src/analyzer.py:58

@dataclass(frozen=True, slots=True)
class StructuredAnalysis:
    event_type: EventType        # Literal[...] — zamknięty słownik, nie goły str
    sentiment: Sentiment         # Literal["pozytywny","negatywny","neutralny"]
    key_numbers: tuple[str, ...]
    summary_pl: str

    # ── z/do dostawcy ─────────────────────────────────────────────────────────
    @classmethod
    def from_model_payload(cls, payload: Mapping[str, Any]) -> "StructuredAnalysis":
        """Walidacja + koercja. Nieznany event_type → 'inne' z WARNING (analyzer.py:265-270);
        nieznany sentiment → 'neutralny' (analyzer.py:55-58). Nigdy nie rzuca — brak pola
        jest faktem domenowym, nie awarią."""

    # ── z/do persystencji ─────────────────────────────────────────────────────
    @classmethod
    def from_stored(cls, raw: str | None) -> "StructuredAnalysis | None":
        """Odczyt kolumny `structured_analysis`. `None`/niepoprawny JSON → None,
        tak jak dziś src/api.py:234-240. Używa stdlib `json` — patrz D-4."""

    def to_stored(self) -> str:
        """json.dumps(..., ensure_ascii=False) — kanoniczna postać zapisu (analyzer.py:272)."""

    # ── operacje domenowe ─────────────────────────────────────────────────────
    def has_numbers(self) -> bool:
        """Czy analiza niesie choć jedną liczbę. Zastępuje src/post_selection.py:28-45."""

    def requires_numbers(self) -> bool:
        """True dla event_type z NUMBER_DEPENDENT_EVENT_TYPES (post_selection.py:25)."""

    def qualifies_for_post(self) -> bool:
        """requires_numbers() → has_numbers(). Reguła PUL-40, dziś rozsmarowana
        między post_selection.py:25-45 a post_generator.py:387."""
```

**Dlaczego to jest właściwy poziom.** `StructuredAnalysis` nie wie nic o Gemini — nie wie nawet,
że istnieje jakiś model. Wie natomiast **wszystko o kształcie**: dopuszczalne wartości, fallbacki,
postać zapisu i trzy reguły selekcji. Dziś ta wiedza leży w pięciu plikach w czterech warstwach.

### 4.2. Port — `JsonModelPort`

Plik: **`src/ports/json_model.py`** (nowy; tylko `typing` + `src.exceptions`).

```python
class ModelTier(StrEnum):
    """Klasa zadania, nie nazwa modelu. Mapowanie tier → model żyje w adapterze.
    BULK_TEXT  — analiza, bramka, generator postu (dziś GEMINI_MODEL)
    VISION     — odczyt zrzutów portfela; wyższa półka, bo pomyłka jest pieniężna
                 (uzasadnienie: src/gemini_client.py:17-18)"""
    BULK_TEXT = "bulk_text"
    VISION    = "vision"

@dataclass(frozen=True, slots=True)
class ImagePart:
    """Obraz jako dane domenowe — bajty + mime. Bez genai.types.Part."""
    data: bytes
    mime_type: str

class JsonModelPort(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str | Sequence[ImagePart],
        tier: ModelTier = ModelTier.BULK_TEXT,
        retry_on_rate_limit: bool = True,
    ) -> Mapping[str, Any]:
        """Zwraca ZDEKODOWANY słownik. Nigdy nie zwraca stringa, nigdy nie zwraca
        obiektu biblioteki. Rzuca wyłącznie wyjątkami z src/exceptions.py."""
```

Nowe wyjątki dopisane do **istniejącej** hierarchii (`src/exceptions.py:24`), żeby nie tworzyć
drugiego słownika błędów — moduł ma fan-in 11 (`artifact-2-structure.md:32`) i to jego rola:

```python
class ModelUnavailableError(AnalysisError):   """Dostawca nieosiągalny lub 5xx."""
class ModelRateLimitedError(AnalysisError):   """429 po wyczerpaniu backoffu."""
class ModelEmptyOutputError(AnalysisError):   """Model nie zwrócił tekstu — patrz KROK 5, R-1."""
class ModelMalformedOutputError(AnalysisError): """Zwrócił tekst, którego nie da się zdekodować."""
```

Przy okazji zniknie z `src/exceptions.py:25` nazwa dostawcy — `AnalysisError` przestanie
być *„Raised when **Gemini** analysis fails"*, a stanie się błędem etapu analizy.

**Dlaczego port jest tak wąski.** Bo cały ten produkt robi z LLM-em dokładnie jedną rzecz:
wysyła instrukcję systemową + treść i oczekuje JSON-a. Wszystkie cztery call-site'y z D-1 mają
identyczny kształt. Port szerszy (streaming, function calling, embeddings) byłby projektowaniem
pod przyszłość, której nic w archiwum nie zapowiada.

### 4.3. Adapter — `GeminiJsonModel`

Plik: **`src/adapters/gemini_json_model.py`** — **jedyny plik w repo z `import google.genai`.**

```python
import google.genai as genai           # ← jedyne wystąpienie w całym repozytorium
import json5                           # ← jedyne wystąpienie w całym repozytorium
from google.genai import errors as genai_errors

_TIER_MODELS = {                       # dziś: src/gemini_client.py:16 i :19
    ModelTier.BULK_TEXT: os.environ.get("GEMINI_MODEL",        "gemini-2.5-flash-lite"),
    ModelTier.VISION:    os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash"),
}
_RETRY_DELAY_S = 30                    # dziś: src/analyzer.py:17

class GeminiJsonModel:                 # spełnia JsonModelPort strukturalnie (Protocol)

    def complete_json(self, *, system, user, tier=ModelTier.BULK_TEXT,
                      retry_on_rate_limit=True) -> Mapping[str, Any]:
        contents = self._to_contents(user)          # ImagePart → genai.types.Part.from_bytes
        config   = genai.types.GenerateContentConfig(     # JEDYNA rekonstrukcja w repo
            system_instruction=system,
            response_mime_type="application/json",
        )
        for attempt in range(2 if retry_on_rate_limit else 1):
            try:
                resp = self._client().models.generate_content(
                    model=_TIER_MODELS[tier], contents=contents, config=config)
            except genai_errors.ClientError as exc:
                if exc.code == 429 and attempt == 0 and retry_on_rate_limit:
                    time.sleep(_RETRY_DELAY_S); continue      # analyzer.py:181-184
                raise ModelRateLimitedError(...) if exc.code == 429 else ModelUnavailableError(...)
            except genai_errors.ServerError as exc:            # DZIŚ NIEOBSŁUŻONE — patrz R-3
                raise ModelUnavailableError(...) from exc
            return self._decode(resp)
        raise ModelRateLimitedError(...)

    def _decode(self, resp) -> Mapping[str, Any]:
        text = resp.text                       # str | None — patrz R-1
        if not text:
            raise ModelEmptyOutputError(...)   # DZIŚ: TypeError → generic except → cicha None
        try:
            data = json5.loads(text)           # obejście defektu ~14% (lessons.md:23-36)
        except Exception as exc:
            raise ModelMalformedOutputError(...) from exc
        if isinstance(data, list):             # dziś: src/gemini_client.py:151-161
            data = data[-1]                    # decyzja przeniesiona z warstwy wizji do ACL
        if not isinstance(data, Mapping):
            raise ModelMalformedOutputError(...)
        return data

    def _client(self) -> "genai.Client":       # prywatne — typ SDK nie opuszcza pliku
        ...                                    # singleton z src/gemini_client.py:25-36
```

Adapter jest jedynym miejscem, które zna: nazwę dostawcy, nazwy modeli, `vertexai=True`,
`GenerateContentConfig`, `Part.from_bytes`, `response.text`, taksonomię błędów SDK, obejście
`json5` i regułę „lista zamiast obiektu". Reszta systemu zna wyłącznie `JsonModelPort`.

### 4.4. Jak wołający się zmieniają

```python
# src/analyzer.py — po refaktorze: BEZ import google.genai, BEZ json5
def _call_analysis(parsed_content: str, model: JsonModelPort) -> StructuredAnalysis | None:
    try:
        payload = model.complete_json(system=_ANALYSIS_SYSTEM_PROMPT, user=parsed_content)
    except AnalysisError:
        logger.warning("Analyzer: analysis call failed", exc_info=True)
        return None
    return StructuredAnalysis.from_model_payload(payload)
```

Wstrzyknięcie z domyślną wartością — bez kontenera DI, w konwencji projektu:

```python
def analyze_announcement(announcement_id, parsed_content, ticker, priority,
                         model: JsonModelPort | None = None) -> AnalysisResult:
    model = model or default_model()      # src/adapters/__init__.py — jedyna fabryka
```

`default_model()` jest jedynym miejscem, w którym pada nazwa klasy adaptera. Wymiana dostawcy
= podmiana jednej linii w tej fabryce.

---

## KROK 5 — Dowód izolacji i before/after

### 5.1. Co dotyka wymiana biblioteki — lista wyczerpująca

Hipoteza: zamiana `google-genai` na dowolnego innego dostawcę JSON-owego LLM.

**DOTYKA (3 pliki):**

| Plik | Zmiana |
| -- | -- |
| `src/adapters/gemini_json_model.py` | usunięty |
| `src/adapters/<nowy>_json_model.py` | dodany — implementuje ten sam `JsonModelPort` |
| `src/adapters/__init__.py` | jedna linia w `default_model()` |
| `pyproject.toml` | `uv remove google-genai && uv add <nowy>` — zgodnie z regułą projektu, nigdy ręczną edycją |

**NIE DOTYKA — i to jest teza do udowodnienia:**

| Warstwa | Artefakt | Dlaczego nie |
| -- | -- | -- |
| L5 tabele | schemat `announcements` (`db/bigquery.py:56-75`), kolumna `structured_analysis` `:69` | kontrakt zapisu należy do `StructuredAnalysis.to_stored()`, nie do dostawcy |
| L5 kod | `db/bigquery.py:2377-2379` (próg jakości X-posta w SQL) | operuje na `analysis_score` i `event_type` — polach domenowych |
| L4 | `src/post_selection.py` | dostaje `StructuredAnalysis`, nie string; `json5` znika stąd |
| L3 | `src/analyzer.py`, `src/post_generator.py` | znają wyłącznie `JsonModelPort` |
| L2 | `src/api.py` — modele `AnnouncementAdmin` i pozostałe 32 endpointy | kształt odpowiedzi HTTP jest niezmieniony |
| L6 | `static/index.html` | `it.structured_analysis.summary_pl` (`:3480`, `:3485`) działa bez zmiany bajtu |
| L7 | `tests/e2e/conftest.py` (62 łatki) | żadna z nich nie dotyczy Gemini |
| infra | `GEMINI_API_KEY` w Secret Manager, `deploy.yml` | nazwa sekretu przechodzi przez adapter |

**Dowód, że L6 rzeczywiście nie jest dotknięty**: front nigdy nie widzi surowej odpowiedzi
modelu. `src/api.py:234-240` parsuje po stronie serwera i wysyła obiekt; `static/index.html:3480`
robi `const a = it.structured_analysis || {}` i czyta `a.summary_pl`. Po refaktorze parsowaniem
zajmie się `StructuredAnalysis.from_stored()` — **ta sama struktura JSON na drucie, ten sam kod
w UI**. Warstwa UI dostaje gotowe dane domenowe i przed refaktorem, i po nim; refaktor tylko
przestaje o tym decydować przypadkiem.

### 5.2. Before/after dla zduplikowanych miejsc

| # | Dziś | Po refaktorze |
| -- | -- | -- |
| 1 | `genai.types.GenerateContentConfig(...)` w **4 miejscach**: `src/analyzer.py:170`, `:201`, `src/post_generator.py:462`, `src/gemini_client.py:145` | **1** — `src/adapters/gemini_json_model.py` |
| 2 | `client.models.generate_content(...)` w **4 miejscach** (`src/analyzer.py:167`, `:198`, `src/post_generator.py:459`, `src/gemini_client.py:142`) | **1** |
| 3 | Pętla backoff 429 w **2 miejscach** (`src/analyzer.py:180-184`, `:208-212`); brak w pozostałych dwóch | **1**, dostępna dla wszystkich czterech wołających |
| 4 | `json5.loads(response.text)` w **4 miejscach** | **1** — w `_decode()` |
| 5 | `json5.loads(raw)` na danych z BQ w **3 miejscach**, w tym L2 i L4 (D-4) | **0** — `StructuredAnalysis.from_stored()` używa stdlib `json` |
| 6 | Słownik `_VALID_EVENT_TYPES` + fallback `"inne"` w `src/analyzer.py:21-26`, `:265-270`; goły `str` w modelu `:50` | `Literal` w `StructuredAnalysis` (domyka ranking #6 z `01-domain-distillation.md:486`) |
| 7 | `def _mock_client(...)` **3 razy** w testach (`tests/test_analyzer.py:24`, `tests/test_gemini_client.py:10`, `tests/test_post_generator.py:54`) | **1** wspólny `FakeJsonModel` implementujący port; kształt SDK znika z testów |
| 8 | `PortfolioPosition` importowany z modułu dostawcy (`src/portfolio_thread_composer.py:10`) | z `src/portfolio_snapshot.py` — typ domenowy w module domenowym |
| 9 | `get_client() -> genai.Client` — typ SDK w sygnaturze publicznej (`src/gemini_client.py:25`) | `default_model() -> JsonModelPort` |
| 10 | `AnalysisError`: *„Raised when **Gemini** analysis fails"* (`src/exceptions.py:25`) | bez nazwy dostawcy + 4 nowe wyjątki portu |

### 5.3. Rozstrzygnięcia zależne od kontraktu biblioteki

Trzy pytania, na które dziś kod odpowiada milcząco. **Wszystkie trzy należą do adaptera —
żadne do `src/api.py` ani do `src/analyzer.py`.**

**R-1. `response.text` może być `None` — i dziś kończy się to cichym zerem.**
W google-genai `.text` zwraca tekst pierwszej części tekstowej pierwszego kandydata i **zwraca
`None`, gdy tekstu nie ma**; przy strukturalnym wyjściu przekroczenie `max_output_tokens` daje
`None` zarówno w `.text`, jak i w `.parsed`
([DeepWiki — Response Structure](https://deepwiki.com/googleapis/python-genai/3.1.2-response-structure-and-quick-accessors),
[python-genai#1039](https://github.com/googleapis/python-genai/issues/1039)).
Dziś `json5.loads(None)` rzuca `TypeError`, który wpada w szerokie `except Exception`
(`src/analyzer.py:187-189`, `src/post_generator.py:488-490`) i zamienia się w `None`
nieodróżnialne od „model odpowiedział bzdurą".
**Decyzja: `ModelEmptyOutputError` osobno od `ModelMalformedOutputError`, oba rzucane w
`_decode()`.** Ucięta odpowiedź to problem limitu tokenów; zły JSON to problem promptu.
Log musi je rozróżniać, bo naprawa jest inna.

**R-2. Natywne wyjście strukturalne (`response_schema`) może zdjąć obejście `json5` — ale
decyzja i tak zostaje w ACL.** SDK przyjmuje `response_schema` (m.in. model Pydantic) razem
z `response_mime_type="application/json"` i udostępnia `response.parsed`.
**Decyzja: nie w tym refaktorze.** Powód jest w danych: pomiar z `lessons.md:27-30` mówi, że
`response_mime_type="application/json"` **sam w sobie nie wystarczył** (~14% trailing comma),
więc obietnica `response_schema` wymaga własnego pomiaru, a nie wiary. Ale gdy ktoś ten pomiar
zrobi, zmiana będzie dotyczyła **jednej metody `_decode()`** i nie ruszy ani jednego wołającego.
To jest dokładnie ta własność, dla której ACL powstaje. **Zakodować jako komentarz-decyzję
w adapterze**, nie jako TODO w `src/api.py`.

**R-3. Taksonomia błędów SDK jest szersza niż to, co kod obsługuje.**
`google.genai.errors` ma bazę `APIError` z atrybutami `code`/`message`/`status`, a `raise_error()`
kieruje 4xx → `ClientError`, 5xx → `ServerError`, resztę → `APIError`
([errors.py](https://github.com/googleapis/python-genai/blob/main/google/genai/errors.py)).
Kod łapie dziś **wyłącznie `ClientError`** (`src/analyzer.py:180`, `:208`); `ServerError` (5xx —
przeciążenie, awaria regionu) trafia do generycznego `except Exception` i jest logowany tym
samym zdaniem co błąd schematu.
**Decyzja: mapowanie 4xx/429 → `ModelRateLimitedError`, pozostałe 4xx → `ModelUnavailableError`,
5xx → `ModelUnavailableError` z osobnym logiem.** Dodatkowo — z pamięci projektu i
`context/foundation/` — endpoint `global` nie ma dynamic quota regionalnej
(`src/gemini_client.py:33` już ustawia `location="global"` domyślnie); to również własność
dostawcy i również należy do adaptera.

---

## KROK 6 — Weryfikacja i plan

### 6.1. Kryterium sukcesu — mierzalne, policzone dziś

**Polecenie (jedno, powtarzalne, do wklejenia do testu):**

```
rg --glob '*.py' --glob '!.venv/**' -l -e 'google\.genai' -e 'json5'
```

**Wynik DZISIAJ (`e4b2066`) — 8 plików:**

```
src\analyzer.py
src\api.py
src\gemini_client.py
src\post_generator.py
src\post_selection.py
tests\test_analyzer.py
tests\test_gemini_client.py
tests\test_post_generator.py
```

**Wynik oczekiwany PO refaktorze — 1 plik:**

```
src\adapters\gemini_json_model.py
```

Kontrola strukturalna (`ast-grep`, wynik parsowany z `--json=compact`, nie z `-l`):

```
$ ast-grep run -l python -p 'import google.genai as $A' --json=compact | jq length
3       # src/gemini_client.py:8, src/analyzer.py:7, src/post_generator.py:10
$ ast-grep run -l python -p 'from google.genai import $$$A' --json=compact | jq length
1       # src/analyzer.py:9
```

Suma 4 trafień w 3 plikach; kontrolny `rg 'google\.genai' -l` daje **4** pliki — czwarty to
`src/post_selection.py`, gdzie nazwa pada w docstringu (`:5`), nie w imporcie. Rozbieżność
jest zamierzona i pokazuje, dlaczego liczę oba: `ast-grep` mierzy **importy**, `rg` mierzy
**wiedzę**. Cel refaktoru jest wyrażony w tej drugiej mierze.

### 6.2. Stan dzisiejszy w liczbach

| Miara | Dziś | Cel |
| -- | -- | -- |
| Pliki produkcyjne znające `google-genai` (import lub defekt) | **6** | **1** |
| Warstwy dotknięte (L2 API, L3 serwisy, L4 domena, L5 persystencja) | **4** | **1** (L3, katalog `src/adapters/`) |
| Pliki testowe reprodukujące kształt SDK | 3 | 0 |
| Wywołania `generate_content` | 4 | 1 |
| Rekonstrukcje `GenerateContentConfig` | 4 | 1 |
| Wystąpienia `json5` (produkcja) | 5 plików / 7 miejsc | 1 plik / 1 miejsce |
| Konsumenci spoza grafu (`.claude/`) | 2 | 1 (skill — przez port) |

**Pliki, które po refaktorze przestaną znać `google-genai` (6):**

1. `src/analyzer.py` — traci `import google.genai` (`:7`), `from google.genai import errors` (`:9`), 2× `genai.types.GenerateContentConfig`, 2× `json5.loads`
2. `src/post_generator.py` — traci `import google.genai` (`:10`), `genai.types.GenerateContentConfig` (`:462`), 2× `json5.loads` (`:387`, `:467`)
3. `src/gemini_client.py` — **przestaje istnieć**; rozpada się na `src/adapters/gemini_json_model.py` (SDK), `src/portfolio_vision.py` (ekstrakcja) i `src/portfolio_snapshot.py` (typy domenowe)
4. `src/post_selection.py` — traci `json5` (`:18`, `:38`); jego docstring `:1-15` traci powód istnienia w obecnym brzmieniu
5. `src/api.py` — traci `import json5` (`:11`) i `json5.loads` (`:238`)
6. `src/portfolio_thread_composer.py` — przestaje importować z modułu dostawcy (`:10`)

Plus 3 pliki testowe i `src/exceptions.py:25` (nazwa dostawcy z docstringa).

**Czego ten refaktor NIE naprawia i trzeba to powiedzieć wprost:** krawędź
`db.bigquery → src.post_selection` (`db/bigquery.py:49`, `tach.toml:45`) **zostaje**.
Znika jej *uzasadnienie* — po refaktorze `post_selection` nie ma już czego trzymać z dala
od warstwy danych, więc naruszenie da się wreszcie ocenić na czysto, bez argumentu
„inaczej wciągniemy SDK". To warunek konieczny naprawy z `tach.toml:12-13`, nie sama naprawa.

### 6.3. Bramka — jak to utrzymać

Projekt ma już wzorzec: *„dwa configi, które muszą się zgadzać → test"* — `pyproject.toml:36-40`
opisuje `tests/test_deploy_workflow_filter.py`, który czyta `deploy.yml` **jako dane**, bo
komentarz nie wystarcza. Ta sama technika:

```python
# tests/test_acl_boundary.py
def test_gemini_sdk_lives_only_in_the_adapter():
    """Reguła z .claude/rules/gemini-ai.md jako test — bo .claude/ jest gitignored
    (.gitignore:22), więc dziś nic jej nie egzekwuje w repozytorium."""
    offenders = files_matching(r"google\.genai|json5", root=".", exclude={".venv", "tests"})
    assert offenders == {"src/adapters/gemini_json_model.py"}
```

To jedyny element, który zamienia dzisiejszą dyscyplinę w egzekwowanie — i sam w sobie
uzasadnia refaktor nawet gdyby nic innego się nie zmieniło.

### 6.4. Plan faz

Zgodnie z konwencją projektu: `/10x-new` → `/10x-research` → `/10x-plan` → `/10x-plan-review`
→ `/10x-tdd` → `/10x-implement` → `/10x-impl-review` → `/10x-archive`
(`MEMORY.md`, „Kanoniczny workflow"). Praca **na feature branchu**, PR, zielony
`ai-code-review/verdict`. Każda faza to **osobny, odwracalny commit**, zgodnie z
`lessons.md:148-152` — i z Branch by Abstraction (`lessons.md:154-160`), którego kroki 2–5
odwzorowują fazy 1–8.

| Faza | Zakres | Odwracalna? | Zielona po? |
| -- | -- | -- | -- |
| **0** | **Charakteryzacja.** Testy pinujące dzisiejsze zachowanie **przed** dotknięciem kodu (`lessons.md:149`): tolerancja trailing comma, backoff 429 w obu call-site'ach analizatora, fallback `event_type → "inne"`, koercja sentymentu, `data[-1]` przy liście w ścieżce wizyjnej, ścieżka „pusta odpowiedź" (dziś: ciche `None`). | tak | tak |
| **1** | `src/model_output.py` (`StructuredAnalysis`), `src/ports/json_model.py`, 4 wyjątki w `src/exceptions.py`. Nikt tego jeszcze nie woła. | tak | tak |
| **2** | `src/adapters/gemini_json_model.py` + `src/adapters/__init__.py` (`default_model()`). `src/gemini_client.py` zostaje **cienką fasadą** re-eksportującą — zero zmian u wołających. (BbA krok 2) | tak | tak |
| **3** | `src/analyzer.py` na port. Znikają `import google.genai`, `from google.genai import errors`, 2× config, 2× `json5`. Testy z fazy 0 muszą przejść bez zmian. (BbA krok 3) | tak | tak |
| **4** | `src/post_generator.py` na port. | tak | tak |
| **5** | Ścieżka wizyjna: `extract_portfolio_snapshot` → `src/portfolio_vision.py`; `PortfolioPosition`/`PortfolioExtraction` → `src/portfolio_snapshot.py`; naprawa `src/portfolio_thread_composer.py:10` i `.claude/skills/portfolio-xpost/SKILL.md`. Reguła S2B (`src/gemini_client.py:71-72`, `:83`) przenosi się razem z typami — **to wiedza domenowa, nie prompt**. | tak | tak |
| **6** | **Warunkowana pomiarem** (`unknown` z D-4): najpierw zapytanie do BQ o wiersze `structured_analysis`, których stdlib `json` nie zjada. Jeśli zero → `StructuredAnalysis.from_stored()` w `src/api.py:238` i `src/post_selection.py:38`, `json5` znika z L2 i L4. Jeśli niezero → osobny ticket na backfill, faza wstrzymana. | tak | tak |
| **7** | `tests/test_acl_boundary.py` — bramka z §6.3. **Mechanizm ląduje na zielono, egzekwowanie włącza się osobno** (`lessons.md:151`). | tak | tak |
| **8** | Usunięcie fasady `src/gemini_client.py`; aktualizacja `tach.toml` (ponowna ocena krawędzi `db → src.post_selection`) i `.claude/rules/gemini-ai.md` (reguła → wskazanie na test). (BbA krok 5) | tak | tak |

**Czego ten refaktor NIE robi — jawnie, zgodnie z `lessons.md:152`:**

- **Nie zmienia promptów.** Ani jednego znaku w `_ANALYSIS_SYSTEM_PROMPT`, `_GATE_SYSTEM_PROMPT`,
  `_SYSTEM_PROMPT`, `_EXTRACTION_SYSTEM_PROMPT`. Prompt to wiedza produktowa; przeniesienie go
  i zmiana to dwie różne zmiany.
- **Nie zmienia dostawcy ani nazw modeli.** `GEMINI_MODEL` i `GEMINI_VISION_MODEL` zachowują
  dzisiejsze wartości domyślne i semantykę.
- **Nie włącza `response_schema`** (R-2) — osobna decyzja, po pomiarze.
- **Nie dotyka schematu BigQuery.** Kolumna `structured_analysis` (`db/bigquery.py:69`) i jej
  zawartość zostają bit w bit.
- **Nie naprawia braku walidacji w bramce** (`src/analyzer.py:207`, `data["approved"]` gołym
  indeksem). To realny dług — ale to zmiana **zachowania**, a nie granicy, i musi iść osobnym
  ticketem, żeby ewentualna regresja bramki antyhalucynacyjnej nie schowała się w diffie ACL.
- **Nie rozbija `db/bigquery.py`** ani nie rusza 51 importów w `src/api.py`. To dług
  testowalności z `artifact-2-structure.md:97-105`, inny problem, inny refaktor.
- **Nie dotyka P-2…P-6.** Firebase, SMTP, httpx, openpyxl zostają jak są — z inwentarzem
  z KROKU 1 jako gotowym wejściem do następnego ACL.

### 6.5. `unknowns`

- **Nie odpytywałem produkcyjnego BigQuery.** Czy istnieją wiersze `structured_analysis`
  niezjadalne przez stdlib `json` — `unknown`. Blokuje fazę 6 i tylko ją.
- **Nie mierzyłem pokrycia testami.** `pytest-cov` nie jest zainstalowany
  (`01-domain-distillation.md:65-66`); „testy przechodzą" nie znaczy „zachowanie jest pokryte".
- **Nie zweryfikowałem empirycznie `response_schema`** na tym modelu i tych promptach.
  R-2 opiera się na dokumentacji SDK i na pomiarze z `lessons.md:27-30`, nie na własnym teście.
- **`.claude/skills/portfolio-xpost/SKILL.md` przeczytałem jako fakt istnienia**, nie jako kod —
  faza 5 musi zacząć się od przeczytania go w całości, bo to jedyny konsument ścieżki wizyjnej.
- **Liczby dotyczą commita `e4b2066`.** Polecenie z §6.1 jest powtarzalne — jeśli wynik jest
  inny niż 8 plików, ten dokument jest nieaktualny, nie repozytorium.

---

## Podsumowanie w jednym zdaniu

Jedyna zależność zewnętrzna, która w tym repozytorium przekracza granice warstw, to SDK Gemini —
żyje dziś w **6 plikach produkcyjnych i 4 warstwach**, jego jedyny strażnik leży w katalogu
wykluczonym z gita, a najostrzejszym dowodem korupcji jest to, że **warstwa API i warstwa domeny
czystej stosują obejście defektu dostawcy do stringa, którego ten dostawca nigdy nie dotknął**.

**Źródła zewnętrzne (KROK 5):**
[Response Structure and Quick Accessors — googleapis/python-genai](https://deepwiki.com/googleapis/python-genai/3.1.2-response-structure-and-quick-accessors) ·
[Structured Output Returns None When max_output_tokens Exceeded (#1039)](https://github.com/googleapis/python-genai/issues/1039) ·
[google/genai/errors.py](https://github.com/googleapis/python-genai/blob/main/google/genai/errors.py) ·
[Google Gen AI SDK documentation](https://googleapis.github.io/python-genai/)
