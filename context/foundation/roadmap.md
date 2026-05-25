---
project: ESPI/EBI Analyzer
version: 1
status: draft
created: 2026-05-25
updated: 2026-05-25
prd_version: 1
main_goal: quality
top_blocker: decisions
---

# Roadmap: ESPI/EBI Analyzer

> Derived from `context/foundation/prd.md` (v1) + auto-researched codebase baseline.
> Edit-in-place; archive when superseded.
> Slices below are listed in dependency order. The "At a glance" table is the index.

## Vision recap

Komunikaty ESPI/EBI spółek notowanych na GPW i NewConnect są publikowane dziesiątki razy dziennie w formie długich PDF-ów, których analiza wymaga specjalistycznej wiedzy. Właściciel produktu prowadzi konto X z analizami robionymi ręcznie — istnieje dowód popytu i publiczność. Produkt automatyzuje ten proces: scraper wykrywa nowe komunikaty, AI analizuje treść, supervisor weryfikuje jakość, a właściciel otrzymuje gotowy do publikacji X-style post emailem — bez żadnej ręcznej ingerencji od pojawienia się komunikatu do emaila.

## North star

**S-04: właściciel może automatycznie otrzymać X-style email z analizą nowego komunikatu ESPI/EBI** — pierwsza kompletna automatyczna iteracja pipeline'u (scraper → parser → AI → supervisor → email) udowadnia, że produkt zastępuje ręczną pracę i jest gotowy do codziennego użytku.

> "Gwiazda przewodnia" to w tym dokumencie pierwsza historyjka, która udowadnia, że produkt działa — najmniejszy pełny przebieg od wejścia (nowy komunikat na gpw.pl) do wyjścia (email do właściciela), sekwencjonowany możliwie wcześnie, bo wszystko inne ma wartość tylko wtedy gdy to zadziała.

## At a glance

| ID   | Change ID               | Outcome (pipeline/właściciel może …)                                          | Prerequisites | PRD refs                               | Status   |
| ---- | ----------------------- | ----------------------------------------------------------------------------- | ------------- | -------------------------------------- | -------- |
| F-01 | scraper-parser-research | (foundation) HTML gpw.pl zmapowany, PDF-y zbadane, decyzja OCR podjęta       | —             | FR-001, FR-004, OQ-1, OQ-2             | ready    |
| F-02 | bigquery-schema         | (foundation) tabela `announcements` w BQ, klient Python skonfigurowany        | —             | FR-002, FR-007                         | ready    |
| F-03 | observability-baseline  | (foundation) structured logging i email alert na błąd pipeline'u              | —             | NFR (failure alerting), FR-008         | ready    |
| S-01 | scraper-dedup           | pobrać listę nowych (nie-duplikat) komunikatów ESPI/EBI z gpw.pl              | F-01, F-02    | FR-001, FR-002, FR-003, US-01          | proposed |
| S-02 | content-parser          | wyciągnąć treść tekstową z komunikatu (PDF lub HTML fallback)                 | F-01, S-01    | FR-004, FR-005, US-01                  | proposed |
| S-03 | ai-analysis-supervisor  | wygenerować zatwierdzony przez supervisora X-style post                       | F-02, S-02    | FR-006, FR-007, FR-008, FR-009, US-01  | proposed |
| S-04 | email-orchestration     | automatycznie otrzymać X-style email z analizą nowego komunikatu ESPI/EBI     | F-03, S-03    | FR-010, FR-011, US-01                  | proposed |

## Streams

Tabela nawigacyjna — grupuje elementy o wspólnym łańcuchu zależności. Kanoniczne porządkowanie żyje w sekcjach Foundations i Slices; ta tabela to proponowany podział na równoległe ścieżki pracy.

| Stream | Temat                        | Łańcuch                              | Uwaga                                                                              |
| ------ | ---------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------- |
| A      | Badania → Scraper → Parser   | `F-01` → `S-01` → `S-02`            | Krytyczna ścieżka do gwiazdy przewodniej; tu zaczynamy                             |
| B      | Dane → Analiza → Dystrybucja | `F-02` → `S-03` → `S-04`            | Równolegle z A; S-03 czeka też na S-02 z A; S-04 czeka też na F-03 z C            |
| C      | Observability                | `F-03` → łączy się z B przy `S-04`  | Równolegle z A i B; NFR "cicha awaria jest niedopuszczalna" wymaga tego przed live |

## Baseline

Stan kodu na 2026-05-25 (auto-zbadany + potwierdzony przez właściciela).
Foundations poniżej zakładają, że warstwy oznaczone jako `present` już istnieją i nie wymagają ponownej konfiguracji.

- **Frontend:** absent — brak UI, brak szablonów; to pipeline CLI
- **Backend / API:** partial — `main.py` to stub (`print("Hello from test-projekt!")`); FastAPI zainstalowane ale nieużywane; brak logiki pipeline'u
- **Data:** absent — dataset `espi_ebi` w BigQuery istnieje (region: `europe-central2`), ale brak schematów tabel i brak klienta Python (`google-cloud-bigquery`) w `pyproject.toml`
- **Auth:** absent — single-user pipeline; per PRD Access Control: N/A
- **Deploy / infra:** present — `Dockerfile` (poprawna kolejność warstw uv), `.github/workflows/deploy.yml` (zweryfikowany — push na master buduje i deployuje), Cloud Run Job `puls-gpw` w `europe-central2`, Cloud Scheduler `puls-gpw-trigger` (wstrzymany)
- **Observability:** absent — brak structured logging, brak error alertingu; tylko `print()` w `main.py`

## Foundations

### F-01: Badanie scrapera i parsera

- **Outcome:** (foundation) struktura HTML gpw.pl/komunikaty zmapowana (selektory, paginacja, URL-e komunikatów), 5–10 próbek PDF ESPI/EBI zbadanych (text-selectable vs skan), decyzja OCR podjęta, schemat URL-ów i dostępnych metadanych udokumentowany
- **Change ID:** `scraper-parser-research`
- **PRD refs:** FR-001 (pobieranie listy komunikatów), FR-004 (parser PDF — wymaga wiedzy o formacie), OQ-1 (dostępność oficjalnego API — resolved: brak, scraping HTML konieczny), OQ-2 (OCR — unresolved, rozstrzyga ta faza)
- **Unlocks:** S-01 (scraper wie jakie selektory HTML parsować), S-02 (parser wie czego spodziewać się w PDF-ach i czy potrzeba OCR)
- **Prerequisites:** —
- **Parallel with:** F-02, F-03
- **Blockers:** —
- **Unknowns:** —
- **Risk:** bez tej fazy scraper i parser muszą być przepisane po odkryciu struktury HTML lub formatu PDF; to najważniejszy bloker z kategorii `decisions` — rozwiązany tu, przed startem jakiejkolwiek implementacji
- **Status:** ready

### F-02: BigQuery schema i klient Python

- **Outcome:** (foundation) tabela `announcements` w BigQuery (dataset `espi_ebi`, region `europe-central2`) ze schematem zawierającym pola dla dedup (ID komunikatu, URL, data) i archiwum analiz, `google-cloud-bigquery` dodany do `pyproject.toml`, wrapper read/write napisany i przetestowany
- **Change ID:** `bigquery-schema`
- **PRD refs:** FR-002 (dedup check wymaga persystencji ID komunikatów), FR-007 (zapis analizy do bazy)
- **Unlocks:** S-01 (dedup read — sprawdzenie czy komunikat był już przetworzony), S-03 (save analysis — zapis zatwierdzonego posta do archiwum)
- **Prerequisites:** —
- **Parallel with:** F-01, F-03
- **Blockers:** —
- **Unknowns:**
  - Jaki jest minimalny schemat tabeli wystarczający dla dedup i archiwum analiz? — Owner: właściciel/implementator. Block: no (rozsądny default: `announcement_id`, `url`, `published_at`, `title`, `company`, `ticker`, `post_text`, `processed_at`, `supervisor_attempts`).
- **Risk:** zły schemat na starcie wymaga migracji BigQuery; cel `quality` — zaprojektować raz porządnie zamiast iterować; dataset już istnieje w poprawnym regionie, unikamy cross-region egress
- **Status:** ready

### F-03: Structured logging i error alerting

- **Outcome:** (foundation) structured logging (JSON) skonfigurowany we wszystkich modułach pipeline'u, email alert wysyłany do właściciela przy każdym nieobsłużonym wyjątku lub błędzie etapu, logi widoczne w Cloud Logging
- **Change ID:** `observability-baseline`
- **PRD refs:** NFR (failure alerting — "cicha awaria jest niedopuszczalna"), FR-008 (po 3 failed próbach supervisora → alert do właściciela zamiast wysłania posta)
- **Unlocks:** S-04 (email-orchestration może bezpiecznie failować z alertem, nie milcząco); weryfikacja każdego etapu pipeline'u w Cloud Logging
- **Prerequisites:** —
- **Parallel with:** F-01, F-02
- **Blockers:** —
- **Unknowns:** —
- **Risk:** bez tej foundation pipeline może failować cicho w Cloud Run bez wiedzy właściciela; sekwencja przed S-04 gwarantuje że nawet pierwsza produkcyjna awaria jest natychmiast widoczna
- **Status:** ready

## Slices

### S-01: Scraper i dedup

- **Outcome:** pipeline może pobrać listę nowych (nie-duplikat) komunikatów ESPI/EBI z gpw.pl i zwrócić je do dalszego przetworzenia
- **Change ID:** `scraper-dedup`
- **PRD refs:** FR-001 (pobieranie listy komunikatów), FR-002 (dedup — ten sam komunikat nie może być przetworzony dwa razy), FR-003 (wszystkie nie-duplikaty, bez filtra tytułu), US-01
- **Prerequisites:** F-01 (HTML struktura gpw.pl znana — selektory, paginacja), F-02 (tabela BQ gotowa do dedup check)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:** —
- **Risk:** struktura HTML gpw.pl jest nieudokumentowana i może się zmienić; mitygacja: F-01 research + właściciel ma referencyjne implementacje scraperów które przyspieszą pracę; NFR freshness (15 min) wymaga że scraper jest niezawodny
- **Status:** proposed

### S-02: Content parser (PDF i HTML)

- **Outcome:** pipeline może wyciągnąć treść tekstową z komunikatu ESPI/EBI — z załączonego PDF-u (primary) lub z treści HTML strony komunikatu (fallback gdy brak PDF)
- **Change ID:** `content-parser`
- **PRD refs:** FR-004 (parser PDF — primary path), FR-005 (ekstrakcja HTML — fallback), US-01
- **Prerequisites:** F-01 (analiza próbek PDF zakończona — decyzja OCR podjęta, format PDF znany), S-01 (lista komunikatów z URL-ami i informacją o dostępności PDF)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:** —
- **Risk:** jeśli F-01 wykaże że PDFy są skanami — potrzebna biblioteka OCR (np. pytesseract lub zewnętrzny serwis); czas implementacji może wzrosnąć; właściciel ma referencyjne implementacje parserów
- **Status:** proposed

### S-03: Analiza AI i supervisor gate

- **Outcome:** pipeline może wygenerować X-style post z komunikatu ESPI/EBI, zwalidowany przez supervisora (max 3 próby; po 3 failed — komunikat odkładany, alert do właściciela)
- **Change ID:** `ai-analysis-supervisor`
- **PRD refs:** FR-006 (analiza Gemini — treść komunikatu → ustrukturyzowana analiza), FR-007 (zapis analizy i posta do BQ), FR-008 (supervisor gate — obiektywne kryteria: spółka+ticker, liczby, hashtagi, długość, brak urwanych zdań, zgodność ze źródłem), FR-009 (X-style post — dwa tryby: finansowy pigułka liczbowa / korporacyjny zdarzenie), US-01
- **Prerequisites:** F-02 (BQ klient gotowy do zapisu analizy), S-02 (tekst komunikatu gotowy do analizy)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Czy właściciel ma gotowy prompt dla Gemini z poprzedniego projektu? — Owner: właściciel. Block: no (można zacząć od zera, ale gotowy prompt skróci iteracje supervisora).
  - Jaki jest docelowy przedział długości X posta (w znakach)? — Owner: właściciel. Block: no (default 150–600 znaków z PRD wystarczy; kalibracja przy pierwszych próbkach generacji).
- **Risk:** LLM-as-judge bias w supervisorze — mitygacja per PRD: obiektywne kryteria (obecność liczb, format, długość, brak urwanych zdań), nie ocena semantyczna; prompt engineering to iteracyjna praca, zakładamy kilka rund kalibracji
- **Status:** proposed

### S-04: Email i orchestracja

- **Outcome:** właściciel może automatycznie otrzymać X-style email z analizą nowego komunikatu ESPI/EBI, wyzwolony przez Cloud Scheduler co 15 minut w godzinach sesji giełdowej, bez ręcznej ingerencji
- **Change ID:** `email-orchestration`
- **PRD refs:** FR-010 (email do właściciela — X-style post jako treść), FR-011 (automatyczny scheduler — Cloud Run Job + Cloud Scheduler), US-01
- **Prerequisites:** F-03 (observability — alerty przy błędach przed live), S-03 (zatwierdzony X-style post dostępny do wysłania)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Czy używamy wbudowanego `smtplib` czy zewnętrznego dostawcy SMTP? — Owner: właściciel. Block: no (dane SMTP już w Secret Manager — `smtplib` wystarczy na MVP).
- **Risk:** first production run musi być widoczna — dlatego F-03 jest prerequisitem; Cloud Scheduler już skonfigurowany (wstrzymany), wymagana aktywacja i ewentualne dostrojenie harmonogramu po testach integracyjnych
- **Status:** proposed

## Backlog Handoff

| Roadmap ID | Change ID               | Sugerowany tytuł issue                                      | Gotowy na `/10x-plan` | Uwagi                                                   |
| ---------- | ----------------------- | ----------------------------------------------------------- | --------------------- | ------------------------------------------------------- |
| F-01       | scraper-parser-research | Zbadaj HTML gpw.pl i próbki PDF ESPI/EBI (research)         | yes                   | Zacznij tu — krytyczna ścieżka; `/10x-plan scraper-parser-research` |
| F-02       | bigquery-schema         | BigQuery schema `announcements` + klient Python             | yes                   | Równolegle z F-01; `/10x-plan bigquery-schema`          |
| F-03       | observability-baseline  | Structured logging + email alert na błąd pipeline'u        | yes                   | Równolegle z F-01, F-02; `/10x-plan observability-baseline` |
| S-01       | scraper-dedup           | Scraper gpw.pl + dedup check BigQuery                       | no                    | Czeka na F-01 + F-02                                    |
| S-02       | content-parser          | Parser PDF (+ OCR fallback) i HTML                          | no                    | Czeka na F-01 + S-01                                    |
| S-03       | ai-analysis-supervisor  | Analiza Gemini + supervisor gate (max 3 próby)              | no                    | Czeka na F-02 + S-02                                    |
| S-04       | email-orchestration     | Email notifier + orchestracja Cloud Run Job / Scheduler     | no                    | Czeka na F-03 + S-03                                    |

## Open Roadmap Questions

1. **Czy PDFy ESPI/EBI bywają skanami (raster) wymagającymi OCR?** — Owner: implementator. Block: S-02 (parser). Rozwiązanie: analiza 5–10 próbek PDF w ramach F-01 musi dać jednoznaczną odpowiedź przed startem S-02.

2. **Czy właściciel udostępni kod referencyjny (scraper, AI pipeline) przed startem implementacji?** — Owner: właściciel. Block: no (można zacząć bez, ale referencja skróci F-01 i S-01 znacznie). Odpowiedź przed startem F-01.

3. **Jaki jest docelowy przedział długości X posta (w znakach)?** — Owner: właściciel. Block: no (default 150–600 znaków; kalibracja po pierwszych próbkach generacji). Odpowiedź przed S-03.

*(OQ-1 z PRD — "Czy GPW/NewConnect mają oficjalny API/feed?" — rozwiązane przed generowaniem roadmapy: brak oficjalnego API, scraping HTML konieczny.)*

## Parked

- **Automatyczne postowanie na X przez API** — PRD §Non-Goals: właściciel ręcznie decyduje o publikacji w MVP; pełna kontrola nad publicznym kontem → v2.
- **Panel webowy do przeglądania analiz** — PRD §Non-Goals: baza danych jako archiwum, brak UI w MVP → v2.
- **Integracja z systemem AI brokerów** — PRD §Non-Goals: downstream consumer analiz w v3+; właściciel ma istniejący system z AI brokerami.
- **Rekomendacje inwestycyjne (buy/sell)** — PRD §Non-Goals: wymaga licencji KNF; poza zakresem na zawsze.
- **Obsługa języka angielskiego** — PRD §Non-Goals: posty i analizy w języku polskim w MVP → future.
- **Scheduler poza godzinami sesji giełdowej** — NFR mówi "w godzinach sesji"; uruchamianie poza sesją to zbędny koszt API i puste przebiegi.

## Done

*(Puste przy pierwszym generowaniu. `/10x-archive` dopisuje tu wpis i ustawia Status: done gdy change folder zostanie zarchiwizowany.)*
