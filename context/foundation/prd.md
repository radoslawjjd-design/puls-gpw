---
project: ESPI/EBI Analyzer
version: 1
status: draft
created: 2026-05-18
context_type: greenfield
product_type: data-pipeline
target_scale:
  users: small
  qps: low
  data_volume: small
timeline_budget:
  mvp_weeks: 3
  hard_deadline: null
  after_hours_only: true
---

## Vision & Problem Statement

Komunikaty ESPI/EBI spółek notowanych na GPW i NewConnect są długie (dziesiątki stron PDF), publikowane dziesiątki razy dziennie i wymagają specjalistycznej wiedzy, by ocenić ich znaczenie. Potencjalni inwestorzy indywidualni rezygnują z śledzenia rynku, bo koszt czasowy jest zbyt wysoki.

Właściciel produktu prowadzi już konto X z podobnymi analizami robionymi ręcznie — istnieje dowód popytu i istniejąca publiczność. Produkt automatyzuje to, co dziś kosztuje czas: pobiera wszystkie komunikaty ESPI/EBI, analizuje je przez AI, wyciąga najważniejsze wnioski i dostarcza je w formie krótkich postów — eliminując barierę informacyjną bez wymagania od odbiorcy czytania dziesiątek stron dokumentów.

## User & Persona

**Primary persona:** Właściciel produktu — inwestor/analityk prowadzący konto X poświęcone analizie polskiego rynku kapitałowego. Czyta ESPI/EBI ręcznie kilka razy dziennie; każdy cykl kosztuje go czas, który mógłby być zautomatyzowany.

**Secondary persona (przyszłość, poza MVP):** Inwestor indywidualny bez czasu na śledzenie rynku — potencjalny płatny subskrybent spersonalizowanych alertów dotyczących wybranych spółek.

**Primary persona scope:** Pojedynczy użytkownik (właściciel) w MVP. Publiczność konta X jako pośredni odbiorca treści generowanej przez system.

**Kanały wyjściowe MVP:**
- Email w formacie X-style post (automatyczny alert przy każdym nowym komunikacie)
- Treść posta do ręcznej publikacji na koncie X właściciela

**Zakres danych:** Wszystkie spółki GPW + NewConnect, wszystkie komunikaty ESPI/EBI.

## Success Criteria

### Primary
Nowy komunikat ESPI/EBI pojawia się na stronie GPW/NewConnect → system wykrywa go automatycznie → przetwarza (parser PDF lub HTML, analiza AI, ocena supervisora) → właściciel otrzymuje X-style email z pigułką liczbową — bez żadnej ręcznej ingerencji.

### Secondary
Supervisor agent zatwierdza output w ≤ 3 próbach dla >90% analizowanych komunikatów (mało rund retry = dobra jakość promptów).

### Guardrails
1. **No duplicates** — ten sam komunikat ESPI/EBI nie może wygenerować więcej niż jednego emaila/posta. Duplikat check jest warunkiem koniecznym przed analizą.
2. **No hallucinations** — podsumowanie nie może zawierać liczb ani faktów, których nie ma w źródle. Supervisor weryfikuje spójność z treścią komunikatu.
3. **Hard supervisor gate** — email/post nie wychodzi bez zatwierdzenia supervisora. Jeśli po 3 próbach output nie przejdzie oceny → alert do właściciela, komunikat odkładany.
4. **Format liczbowy** — podsumowanie musi zawierać kluczowe dane numeryczne (zysk/przychód w mln PLN + % zmiana r/r, q/q, vs konsensus tam gdzie dostępne). Post musi być krótki i przykuwający uwagę — nie narracja, ale pigułka liczb.

## User Stories

### US-01: Automatyczne przetworzenie nowego komunikatu ESPI/EBI

- **Given** pojawia się nowy komunikat ESPI/EBI na stronie GPW/NewConnect,
- **When** system wykrywa nowy (nie-duplikat) komunikat,
- **Then** pipeline przetwarza komunikat (PDF lub HTML), analizuje przez AI, supervisor zatwierdza output, i właściciel otrzymuje X-style email z pigułką liczbową — bez żadnej ręcznej ingerencji.

#### Acceptance Criteria
- Właściciel otrzymuje email w ciągu 15 minut od publikacji komunikatu na GPW/NewConnect.
- Ten sam komunikat nie generuje więcej niż jednego emaila.
- Email zawiera: nazwę spółki, ticker, kluczowe dane (liczbowe lub zdarzenie korporacyjne), hashtagi.
- Jeśli supervisor nie zatwierdzi outputu w 3 próbach — właściciel dostaje alert zamiast posta z analizą.

## Functional Requirements

### Pobieranie i filtrowanie
- FR-001: System może pobrać listę nowych komunikatów ESPI/EBI ze strony GPW/NewConnect. Priority: must-have
  > Socrates: Kontr-argument rozważony: "GPW/NewConnect mają oficjalne API/feed." Rezolucja: scraping to jedyna realna droga w MVP — sprawdzić dostępność feedów przy implementacji.
- FR-002: System może wykryć i pominąć duplikaty (już przetworzone komunikaty). Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.
- FR-003: System analizuje wszystkie nowe (nie-duplikat) komunikaty ESPI/EBI — bez filtra tytułu. Priority: must-have
  > Socrates: Kontr-argument przyjęty: filtr tytułu generuje false negative (ważny komunikat ze słabym tytułem pomijany). Rewizja: analizuj wszystko co nie jest duplikatem. Koszt API Gemini przy darmowych kredytach akceptowalny.

### Ekstrakcja treści
- FR-004: System może pobrać i sparsować załącznik PDF z komunikatu. Priority: must-have
  > Socrates: Ryzyko: PDFy ESPI/EBI mogą być skanami wymagającymi OCR lub mieć niestandardowy layout. Rezolucja: sprawdzić przy implementacji; OCR jako fallback jeśli potrzebny.
- FR-005: System może wyciągnąć treść tekstową z HTML komunikatu (gdy brak PDF). Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.

### Analiza i ocena
- FR-006: System może przesłać treść komunikatu do zewnętrznego modelu AI i otrzymać analizę w zdefiniowanym formacie. Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.
- FR-007: System może zapisać analizę do bazy danych. Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.
- FR-008: Supervisor może ocenić jakość outputu AI i odrzucić go z uwagami do kolejnej próby (max 3 iteracje). Jeśli wszystkie 3 próby failed — komunikat nie jest wysyłany, właściciel dostaje alert. Priority: must-have
  > Socrates: Kontr-argument rozważony: supervisor (LLM) może sam hallucynować podczas oceny. Rezolucja: ustalamy sztywne, obiektywne reguły oceny (obecność liczb, długość, zgodność z szablonem) zamiast oceny semantycznej — zmniejsza ryzyko LLM-as-judge bias. Hard limit 3 próby.

### Output i dystrybucja
- FR-009: System może wygenerować X-style post z zatwierdzonej analizy (wg zdefiniowanych reguł formatu: pigułka liczbowa, krótki, przykuwający). Priority: must-have
  > Socrates: Ryzyko: bez zdefiniowanych reguł formatu generacja będzie niespójna. Rezolucja: reguły formatu X posta są osobnym artefaktem do zdefiniowania przed implementacją (wchodzi do Business Logic).
- FR-010: System może wysłać X-style post jako email do właściciela. Priority: must-have
  > Socrates: Brak kontr-argumentu. Wybór dostawcy email (SMTP/SendGrid/etc.) to decyzja implementacyjna.

### Orchestracja
- FR-011: System może uruchomić cały pipeline automatycznie wg harmonogramu (scheduler). Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.

## Non-Functional Requirements

- **Freshness:** właściciel otrzymuje email z analizą nowego komunikatu ESPI/EBI w ciągu 15 minut od jego publikacji na stronie GPW/NewConnect.
- **Failure alerting:** właściciel jest powiadamiany emailem o każdej awarii systemu. Cicha awaria jest niedopuszczalna — system musi raportować błędy.
- **No duplicates:** ten sam komunikat nie może wygenerować więcej niż jednego outputu, niezależnie od liczby uruchomień schedulera.
- **Integrity:** post nie może zawierać liczb ani faktów nieobecnych w źródłowym komunikacie.

## Business Logic

System klasyfikuje każdy nowy komunikat ESPI/EBI jako finansowy lub korporacyjny, wyciąga odpowiednie dane i generuje zwięzły, przykuwający post — zamiast narracji.

**Dwa tryby klasyfikacji:**

1. **Komunikat finansowy** (zawiera dane liczbowe: wyniki, przychody, zyski, zmiany %)
   - Wyciągane dane: zysk/przychód w mln PLN, % zmiana r/r, q/q, odniesienie do konsensusu rynkowego (jeśli dostępne)
   - Output: pigułka liczbowa — dane w formacie `X mln PLN (+Y% r/r)`

2. **Komunikat korporacyjny** (brak danych liczbowych: zmiana zarządu, umowa, zmiana adresu itp.)
   - Wyciągane dane: kto, co, potencjalny wpływ na spółkę
   - Output: krótki opis zdarzenia — bez wymyślania liczb

**Reguły formatu X posta:**
- Zaczyna się od nazwy spółki i tickera (np. `PKN Orlen $PKN`)
- Dane finansowe z emoji jako visual anchor (np. `📈 +12% r/r`, `💰 450 mln PLN`)
- Kończy się hashtagami (`#ESPI #GPW #[ticker]`)
- Długość: nie ucinać w połowie zdania; złoty środek (orientacyjnie 150–600 znaków — do skalibrowania przy implementacji)
- Ton: zachęca do interakcji (reakcje, komentarze) — naturalny, nie brzmi sztucznie

**Kryteria oceny supervisora (obiektywne, nie semantyczne):**
- [ ] Post zaczyna się od nazwy spółki i tickera
- [ ] Zawiera co najmniej jedną liczbę (dla komunikatów finansowych) lub jasne zdarzenie (dla korporacyjnych)
- [ ] Zawiera hashtagi
- [ ] Długość mieści się w zdefiniowanym przedziale (do skalibrowania przy implementacji)
- [ ] Brak zdań urwanych w połowie
- [ ] Dane liczbowe zgodne ze źródłem (nie wymyślone)

## Access Control

System działa jako autonomiczny scheduled pipeline. Brak logowania użytkownika, brak UI webowego w MVP.

Dostęp do outputu przez:
- Email (automatyczny alert/post do właściciela)
- Treść posta do ręcznej publikacji na koncie X właściciela

Konfiguracja systemu na poziomie infrastruktury (zmienne środowiskowe, harmonogram). Brak panelu administracyjnego w MVP.

## Non-Goals

- **Automatyczne postowanie na X przez API** — MVP generuje X-style post i wysyła go emailem; właściciel ręcznie decyduje kiedy i czy opublikować na X. Automatyzacja publikacji na X → v2. Rationale: pełna kontrola nad publicznym kontem w MVP.
- **Panel webowy do przeglądania analiz** — baza danych jako archiwum; brak UI webowego w MVP. → v2. Rationale: scope down, oszczędność czasu budowy.
- **Integracja z systemem AI brokerów (portfele, sygnały)** — przyszły downstream consumer analiz. → v3+. Rationale: istniejący system wymaga osobnej integracji projektowej.
- **Rekomendacje inwestycyjne (buy/sell)** — system analizuje i podsumowuje, nie rekomenduje. Rationale: rekomendacja inwestycyjna wymaga odpowiedniej licencji regulacyjnej (KNF).
- **Obsługa języka angielskiego** — posty i analizy w języku polskim w MVP. → future.

## Open Questions

1. **Czy GPW i NewConnect udostępniają oficjalny API lub feed (RSS/XML) do komunikatów ESPI/EBI?** — Owner: właściciel/implementator. Sprawdzić przed implementacją FR-001. Jeśli feed istnieje, scraping HTML jest zbędny i mniej stabilny. Block: tak (determinuje implementację FR-001).
2. **Czy PDFy ESPI/EBI bywają skanami wymagającymi OCR?** — Owner: właściciel/implementator. Sprawdzić przy pierwszych próbkach PDF. Jeśli tak — OCR jako fallback do FR-004. Block: częściowy (wpływa na zakres FR-004).
3. **Jaki jest docelowy przedział długości X posta (w znakach)?** — Orientacyjnie 150–600 znaków; do skalibrowania po pierwszych próbkach generacji. Owner: właściciel. Block: nie (default wystarczy na start).
