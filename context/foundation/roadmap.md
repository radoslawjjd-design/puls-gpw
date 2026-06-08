---
project: ESPI/EBI Analyzer
version: 1
status: draft
created: 2026-05-25
updated: 2026-06-08
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

> "Gwiazda przewodnia" to w tym dokumencie pierwsza historyjka, która udowadnia, że produkt działa — najmniejszy pełny przebieg od wejścia (nowy komunikat na Bankier.pl) do wyjścia (email do właściciela), sekwencjonowany możliwie wcześnie, bo wszystko inne ma wartość tylko wtedy gdy to zadziała.

## At a glance

| ID   | Change ID               | Outcome (pipeline/właściciel może …)                                          | Prerequisites | PRD refs                               | Status   |
| ---- | ----------------------- | ----------------------------------------------------------------------------- | ------------- | -------------------------------------- | -------- |
| F-01 | scraper-parser-research | (foundation) HTML Bankier.pl zmapowany, PDF-y zbadane, decyzja OCR podjęta   | —             | FR-001, FR-004, OQ-1, OQ-2             | done     |
| F-02 | bigquery-schema         | (foundation) tabela `announcements` w BQ, klient Python skonfigurowany        | —             | FR-002, FR-007                         | done     |
| F-03 | observability-baseline  | (foundation) structured logging i email alert na błąd pipeline'u              | —             | NFR (failure alerting), FR-008         | done     |
| S-01 | scraper-dedup           | pobrać listę nowych (nie-duplikat) komunikatów ESPI/EBI z Bankier.pl          | F-01, F-02    | FR-001, FR-002, FR-003, US-01          | done     |
| S-02 | content-parser          | wyciągnąć treść tekstową z komunikatu (PDF lub HTML fallback)                 | F-01, S-01    | FR-004, FR-005, US-01                  | done     |
| S-03 | ai-analysis-supervisor  | przeanalizować komunikat Gemini, ocenić halucynacje i nadać score każdemu ogłoszeniu | F-02, S-02 | FR-006, FR-007, FR-008, US-01 | done     |
| S-04 | xpost-generation        | wygenerować zatwierdzony X-style post (nitka) z top-N analiz okna czasowego         | S-03       | FR-009, US-01                 | done     |
| S-05 | email-orchestration     | automatycznie otrzymać X-style email z postem                                        | F-03, S-04 | FR-010, FR-011, US-01         | done     |

## Streams

Tabela nawigacyjna — grupuje elementy o wspólnym łańcuchu zależności. Kanoniczne porządkowanie żyje w sekcjach Foundations i Slices; ta tabela to proponowany podział na równoległe ścieżki pracy.

| Stream | Temat                        | Łańcuch                              | Uwaga                                                                              |
| ------ | ---------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------- |
| A      | Badania → Scraper → Parser   | `F-01` → `S-01` → `S-02`            | Krytyczna ścieżka do gwiazdy przewodniej; tu zaczynamy                             |
| B      | Dane → Analiza → Post → Email | `F-02` → `S-03` → `S-04` → `S-05`  | S-03 czeka też na S-02 z A; S-05 czeka też na F-03 z C                             |
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

- **Outcome:** (foundation) struktura HTML Bankier.pl (https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek) zmapowana (selektory, paginacja, URL-e komunikatów), 5–10 próbek PDF ESPI/EBI zbadanych (text-selectable vs skan), decyzja OCR podjęta, schemat URL-ów i dostępnych metadanych udokumentowany
- **Change ID:** `scraper-parser-research`
- **PRD refs:** FR-001 (pobieranie listy komunikatów), FR-004 (parser PDF — wymaga wiedzy o formacie), OQ-1 (dostępność oficjalnego API — resolved: brak, scraping HTML konieczny), OQ-2 (OCR — unresolved, rozstrzyga ta faza)
- **Unlocks:** S-01 (scraper wie jakie selektory HTML parsować), S-02 (parser wie czego spodziewać się w PDF-ach i czy potrzeba OCR)
- **Prerequisites:** —
- **Parallel with:** F-02, F-03
- **Blockers:** —
- **Unknowns:** —
- **Risk:** bez tej fazy scraper i parser muszą być przepisane po odkryciu struktury HTML lub formatu PDF; to najważniejszy bloker z kategorii `decisions` — rozwiązany tu, przed startem jakiejkolwiek implementacji
- **Status:** done

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
- **Status:** done

### F-03: Structured logging i error alerting

- **Outcome:** (foundation) structured logging (JSON) skonfigurowany we wszystkich modułach pipeline'u, email alert wysyłany do właściciela przy każdym nieobsłużonym wyjątku lub błędzie etapu, logi widoczne w Cloud Logging
- **Change ID:** `observability-baseline`
- **PRD refs:** NFR (failure alerting — "cicha awaria jest niedopuszczalna"), FR-008 (po 3 failed próbach supervisora → alert do właściciela zamiast wysłania posta)
- **Unlocks:** S-05 (email-orchestration może bezpiecznie failować z alertem, nie milcząco); weryfikacja każdego etapu pipeline'u w Cloud Logging
- **Prerequisites:** —
- **Parallel with:** F-01, F-02
- **Blockers:** —
- **Unknowns:** —
- **Risk:** bez tej foundation pipeline może failować cicho w Cloud Run bez wiedzy właściciela; sekwencja przed S-04 gwarantuje że nawet pierwsza produkcyjna awaria jest natychmiast widoczna
- **Status:** done

## Slices

### S-01: Scraper i dedup

- **Outcome:** pipeline może pobrać listę nowych (nie-duplikat) komunikatów ESPI/EBI z Bankier.pl i zwrócić je do dalszego przetworzenia
- **Change ID:** `scraper-dedup`
- **PRD refs:** FR-001 (pobieranie listy komunikatów), FR-002 (dedup — ten sam komunikat nie może być przetworzony dwa razy), FR-003 (wszystkie nie-duplikaty, bez filtra tytułu), US-01
- **Prerequisites:** F-01 (HTML struktura Bankier.pl znana — selektory, paginacja), F-02 (tabela BQ gotowa do dedup check)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:** —
- **Risk:** struktura HTML Bankier.pl jest nieudokumentowana i może się zmienić; mitygacja: F-01 research + właściciel ma referencyjne implementacje scraperów które przyspieszą pracę; NFR freshness (15 min) wymaga że scraper jest niezawodny
- **Status:** done

### S-02: Content parser (PDF i HTML)

- **Outcome:** pipeline może wyciągnąć treść tekstową z komunikatu ESPI/EBI — z załączonego PDF-u (primary) lub z treści HTML strony komunikatu (fallback gdy brak PDF)
- **Change ID:** `content-parser`
- **PRD refs:** FR-004 (parser PDF — primary path), FR-005 (ekstrakcja HTML — fallback), US-01
- **Prerequisites:** F-01 (analiza próbek PDF zakończona — decyzja OCR podjęta, format PDF znany), S-01 (lista komunikatów z URL-ami i informacją o dostępności PDF)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:** —
- **Risk:** jeśli F-01 wykaże że PDFy są skanami — potrzebna biblioteka OCR (np. pytesseract lub zewnętrzny serwis); czas implementacji może wzrosnąć; właściciel ma referencyjne implementacje parserów
- **Status:** done

### S-03: Analiza AI + scoring komunikatów

- **Outcome:** pipeline może przeanalizować każde nowe ogłoszenie Gemini Flash, zweryfikować halucynacje i nadać score — wynik zapisany do BQ gotowy do agregacji przez S-04
- **Change ID:** `ai-analysis-supervisor`
- **PRD refs:** FR-006 (analiza Gemini — treść komunikatu → ustrukturyzowana analiza), FR-007 (zapis analizy do BQ), FR-008 (hallucination gate), US-01
- **Prerequisites:** F-02 (BQ klient gotowy), S-02 (parsed_content gotowy do analizy)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:** —
- **Risk:** Gemini może źle klasyfikować event_type dla niestandardowych ogłoszeń — mitygacja: iteracyjna kalibracja promptów; scoring można ręcznie korygować przez aktualizację tier listy lub wag event_type bez zmiany kodu
- **Status:** done

#### Kluczowe decyzje architektoniczne (ustalone 2026-06-06)

**Phase 0 — Scraper enhancement:** dodanie `priority` badge (`a-quotes-badge -orange-500 -priority`) do `Announcement` dataclass i BQ.

**Phase 1 — Gemini structured analysis:** `parsed_content` → JSON `{company, ticker, event_type, key_numbers, sentiment, summary_pl}`.

**Phase 2 — Hallucination gate (Gemini-as-judge):** drugie wywołanie Gemini porównuje `parsed_content` vs `structured_analysis`. BQ: `analysis_approved BOOL`, `analysis_reject_reason STRING`.

**Phase 3 — Scoring:** `final_score = tier_bonus + event_type_score + priority_bonus`

Tier bonuses: T1 portfel (DGN ELT SNT TOA VOT XTB PAS KRU LBW APT) = +40 · T2 WIG20 (PKO KGH PKN PGE PZU CDR KTY LPP DNP ZAB PEO ASB CBF DVL CRI DEK) = +25 · T3 mid-caps (MDV ALR TPE MBK ALE PCO BDX) = +10 · T4 = +0

Event type scores: wyniki=100 · upadłość=95 · przejęcie/fuzja=90 · dywidenda=85 · emisja=80 · kontrakt_znaczący=75 · insider=65 · sprzedaż_operacyjna=60 · skup=55 · zarząd=50 · inne=20

Priority badge bonus: `"Ważny"` = +20 · reszta = +0

BQ nowe kolumny: `priority STRING`, `structured_analysis STRING`, `analysis_approved BOOL`, `analysis_reject_reason STRING`, `event_type STRING`, `analysis_score FLOAT64`

### S-04: X-post generation + post supervisor

- **Outcome:** pipeline może wygenerować zatwierdzony X-style post (nitka: hook + per-spółka + summary) z top-N (4–5) najwyżej scorowanych ogłoszeń z danego okna czasowego
- **Change ID:** `xpost-generation`
- **PRD refs:** FR-009 (X-style post — nitka, format ustabilizowany na przykładach), US-01
- **Prerequisites:** S-03 (scoring gotowy, BQ ma `analysis_score` i `analysis_approved`)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Dokładny prompt dla generacji nitki X — do ustalenia przed `/10x-plan xpost-generation`; wymaga przykładów postów jako few-shot.
- **Risk:** Post supervisor może być zbyt restrykcyjny → pętla bez końca; mitygacja: max 3 próby, potem zapis z flagą `post_supervisor_failed`
- **Status:** done

#### Kluczowe decyzje architektoniczne (ustalone 2026-06-06)

**Okna czasowe:** 00:01–8:30 (pre-market) · 8:31–12:00 (poranek) · 12:01–15:00 (południe) · 15:01–17:00 (popołudnie)

**Format nitki:** tweet 1 (hook: top 2–3 spółki + emoji + cashtag + pytanie + 🧵) → tweety 2–N (per spółka: emoji + $TICKER + 3 bullety z liczbami) → tweet ostatni (summary: lista wszystkich, nazwa okna, #GPW, disclaimer)

**Post supervisor sprawdza:** długość per tweet (≤280 znaków), obecność cashtag $TICKER, obecność #GPW, brak uciętych zdań, obecność disclaimer w ostatnim tweecie.

**Cashtag format:** `$TICKER` (nie `#TICKER`)

**Deployment:** S-04 to osobny Cloud Run Job — niezależny od 15-min scraper job. Wyzwalany przez Cloud Scheduler o 8:30, 12:00, 15:00, 17:00. S-05 (email) może być częścią tego samego joba lub osobnym — do ustalenia przy `/10x-plan xpost-generation`.

---

### S-05: Email i orchestracja

- **Outcome:** właściciel może automatycznie otrzymać X-style email z zatwierdzonym postem (nitką), wyzwolony przez Cloud Scheduler przy końcu każdego okna czasowego, bez ręcznej ingerencji
- **Change ID:** `email-orchestration`
- **PRD refs:** FR-010 (email do właściciela — X-style post jako treść), FR-011 (automatyczny scheduler — Cloud Run Job + Cloud Scheduler), US-01
- **Prerequisites:** F-03 (observability — alerty przy błędach przed live), S-04 (zatwierdzony X-style post dostępny do wysłania)
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Czy używamy wbudowanego `smtplib` czy zewnętrznego dostawcy SMTP? — Owner: właściciel. Block: no (dane SMTP już w Secret Manager — `smtplib` wystarczy na MVP).
- **Risk:** first production run musi być widoczna — dlatego F-03 jest prerequisitem; Cloud Scheduler już skonfigurowany (wstrzymany), wymagana aktywacja i ewentualne dostrojenie harmonogramu po testach integracyjnych
- **Status:** done

## Backlog Handoff

| Roadmap ID | Change ID               | Sugerowany tytuł issue                                      | Gotowy na `/10x-plan` | Uwagi                                                   |
| ---------- | ----------------------- | ----------------------------------------------------------- | --------------------- | ------------------------------------------------------- |
| F-01       | scraper-parser-research | Zbadaj HTML Bankier.pl i próbki PDF ESPI/EBI (research)     | yes                   | Zacznij tu — krytyczna ścieżka; `/10x-plan scraper-parser-research` |
| F-02       | bigquery-schema         | BigQuery schema `announcements` + klient Python             | yes                   | Równolegle z F-01; `/10x-plan bigquery-schema`          |
| F-03       | observability-baseline  | Structured logging + email alert na błąd pipeline'u        | yes                   | Równolegle z F-01, F-02; `/10x-plan observability-baseline` |
| S-01       | scraper-dedup           | Scraper Bankier.pl + dedup check BigQuery                   | no                    | Czeka na F-01 + F-02                                    |
| S-02       | content-parser          | Parser PDF (+ OCR fallback) i HTML                          | no                    | Czeka na F-01 + S-01                                    |
| S-03       | ai-analysis-supervisor  | Gemini analysis + hallucination gate + scoring per ogłoszenie | no                  | Czeka na F-02 + S-02                                    |
| S-04       | xpost-generation        | Top-N aggregation + X-post nitka + post supervisor            | no                  | Czeka na S-03                                           |
| S-05       | email-orchestration     | Email notifier + orchestracja Cloud Run Job / Scheduler       | no                  | Czeka na F-03 + S-04                                    |

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

- **F-01: (foundation) HTML Bankier.pl zmapowany, PDF-y zbadane, decyzja OCR podjęta** — Archived 2026-06-02 → `context/archive/2026-05-26-scraper-parser-research/`. Lesson: —.
- **F-02: (foundation) tabela `announcements` w BQ, klient Python skonfigurowany** — Archived 2026-06-04 → `context/archive/2026-06-02-bigquery-schema/`. Lesson: GCP client init — load_dotenv + ADC quota project.
- **F-03: (foundation) structured logging i email alert na błąd pipeline'u** — Archived 2026-06-04 → `context/archive/2026-06-04-observability-baseline/`. Lesson: —.
- **S-01: pipeline może pobrać listę nowych (nie-duplikat) komunikatów ESPI/EBI z Bankier.pl** — Archived 2026-06-05 → `context/archive/2026-06-05-scraper-dedup/`. Lesson: —.
- **S-02: pipeline może wyciągnąć treść tekstową z komunikatu ESPI/EBI — z załączonego PDF-u (primary) lub z treści HTML strony komunikatu (fallback gdy brak PDF)** — Archived 2026-06-06 → `context/archive/2026-06-06-content-parser/`. Lesson: —.
- **S-03: pipeline może przeanalizować każde nowe ogłoszenie Gemini Flash, zweryfikować halucynacje i nadać score — wynik zapisany do BQ gotowy do agregacji przez S-04** — Archived 2026-06-07 → `context/archive/2026-06-06-ai-analysis-supervisor/`. Lesson: Gemini trailing-comma JSON fix.
- **S-04: pipeline może wygenerować zatwierdzony X-style post (nitka) z top-N analiz okna czasowego** — Archived 2026-06-08 → `context/archive/2026-06-08-xpost-generation/`. Lesson: —.
- **S-05: właściciel może automatycznie otrzymać X-style email z zatwierdzonym postem (nitką), wyzwolony przez Cloud Scheduler** — Delivered as part of S-04 (smtplib + Secret Manager + Cloud Scheduler 08:30/13:00/17:30). No separate change needed.
