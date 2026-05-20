---
project: ESPI/EBI Analyzer
context_type: greenfield
updated: 2026-05-18
timeline_budget:
  mvp_weeks: 3
  after_hours_only: true
  hard_deadline: null
  delivery_target: "~2026-06-18"
product_type: backend-pipeline
target_scale:
  users: small
  note: "Start: właściciel; furtka na dziesiątki/setki subskrybentów"
checkpoint:
  current_phase: 8
  phases_completed: [1, 2, 3, 4, 5, 6, 7]
  frs_drafted: 11
  quality_check_status: accepted
---

## Vision & Problem Statement

Komunikaty ESPI/EBI spółek notowanych na GPW i NewConnect są długie (dziesiątki stron PDF), publikowane dziesiątki razy dziennie i wymagają specjalistycznej wiedzy, by ocenić ich znaczenie. Potencjalni inwestorzy indywidualni rezygnują z śledzenia rynku, bo koszt czasowy jest zbyt wysoki. Produkt automatycznie pobiera wszystkie komunikaty ESPI/EBI, analizuje je przez AI, wyciąga najważniejsze wnioski i dostarcza je w formie krótkich postów (format X/Twitter) — eliminując barierę informacyjną bez wymagania od odbiorcy czytania dziesiątek stron dokumentów.

**Pain category:** Information overload — dane są publicznie dostępne, ale praktycznie niedostępne dla niespecjalistów ze względu na objętość i tempo publikacji.

**Insight:** Właściciel produktu prowadzi już konto X z podobnymi analizami robionymi ręcznie — istnieje dowód popytu i istniejąca publiczność. Produkt automatyzuje to, co dziś kosztuje go czas.

## User & Persona

**Primary persona:** Właściciel produktu — inwestor/analityk prowadzący konto X, który chce zastąpić ręczną pracę automatycznym pipeline'em.

**Secondary persona (przyszłość, poza MVP):** Inwestor indywidualny bez czasu na śledzenie rynku — potencjalny płatny subskrybent spersonalizowanych alertów dotyczących wybranych spółek.

**Primary persona scope:** Pojedynczy użytkownik (właściciel) w MVP. Publiczność konta X jako pośredni odbiorca treści generowanej przez system.

**Kanały wyjściowe MVP:**
- Email w formacie X-style post (automatyczny alert przy ważnym komunikacie)
- Post na konto X (te same treści, ten sam trigger)

**Zakres danych:** Wszystkie spółki GPW + NewConnect, wszystkie komunikaty ESPI/EBI.

## Access Control

N/A — brak logowania użytkownika. System działa jako autonomiczny scheduled pipeline w chmurze (cykliczne joby uruchamiane przez scheduler). Dostęp do outputu odbywa się przez:
- Email (automatyczny alert do właściciela)
- X/Twitter (automatyczny post na konto właściciela)

Brak panelu webowego, brak UI w MVP. Konfiguracja na poziomie infrastruktury (zmienne środowiskowe, cloud scheduler).

## Success Criteria

### Primary
Nowy komunikat ESPI/EBI pojawia się na stronie GPW/NewConnect → system wykrywa go automatycznie (scheduler) → przetwarza (filtr tytułu, PDF/HTML parser, analiza Gemini, ocena supervisora) → wysyła X-style email do właściciela. Zero ręcznej ingerencji od pojawienia się komunikatu do emaila.

### Secondary
Supervisor agent zatwierdza output w ≤ 3 próbach dla >90% analizowanych komunikatów (mało rund retry = dobra jakość promptów).

### Guardrails
1. **No duplicates** — ten sam komunikat ESPI/EBI nie może wygenerować więcej niż jednego emaila/posta. Duplikat check jest warunkiem koniecznym przed analizą.
2. **No hallucinations** — podsumowanie nie może zawierać liczb ani faktów, których nie ma w źródle. Supervisor weryfikuje spójność z treścią komunikatu.
3. **Hard supervisor gate** — email/post nie wychodzi bez zatwierdzenia supervisora. Jeśli po N próbach output nie przejdzie oceny → alert do właściciela, komunikat odkładany.
4. **Format liczbowy** — podsumowanie musi zawierać kluczowe dane numeryczne (zysk/przychód w mln PLN + % zmiana r/r, q/q, vs konsensus tam gdzie dostępne). Post musi być krótki i przykuwający uwagę — nie narracja, ale pigułka liczb.

**timeline_budget:**
  mvp_weeks: 3
  after_hours_only: true
  hard_deadline: null

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
- FR-006: System może przesłać treść komunikatu do AI (Gemini) i otrzymać analizę w zdefiniowanym formacie. Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.
- FR-007: System może zapisać analizę do bazy danych. Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.
- FR-008: Supervisor może ocenić jakość outputu AI i odrzucić go z uwagami do kolejnej próby (max 3 iteracje). Jeśli wszystkie 3 próby failed — komunikat nie jest wysyłany. Priority: must-have
  > Socrates: Kontr-argument rozważony: supervisor (LLM) może sam hallucynować podczas oceny. Rezolucja: ustalamy sztywne, obiektywne reguły oceny (obecność liczb, długość, zgodność z szablonem) zamiast oceny semantycznej — zmniejsza ryzyko LLM-as-judge bias. Hard limit 3 próby.

### Output i dystrybucja
- FR-009: System może wygenerować X-style post z zatwierdzonej analizy (wg zdefiniowanych reguł formatu: pigułka liczbowa, krótki, przykuwający). Priority: must-have
  > Socrates: Ryzyko: bez zdefiniowanych reguł formatu generacja będzie niespójna. Rezolucja: reguły formatu X posta są osobnym artefaktem do zdefiniowania przed implementacją (wchodzi do Business Logic, Faza 5).
- FR-010: System może wysłać X-style post jako email do właściciela. Priority: must-have
  > Socrates: Brak kontr-argumentu. Wybór dostawcy email (SMTP/SendGrid/etc.) to decyzja implementacyjna.

### Orchestracja
- FR-011: System może uruchomić cały pipeline automatycznie wg harmonogramu (scheduler). Priority: must-have
  > Socrates: Brak kontr-argumentu. FR stoi bez zmian.

## User Stories

### US-01
**Given** pojawia się nowy komunikat ESPI/EBI na stronie GPW/NewConnect,
**When** scheduler odpala job i wykrywa nowy (nie-duplikat) komunikat z tytułem wskazującym na analizę wartościową,
**Then** system przetwarza komunikat (PDF lub HTML), analizuje przez AI, supervisor zatwierdza output, i właściciel otrzymuje X-style email z pigułką liczbową — bez żadnej ręcznej ingerencji.

## Business Logic

**Reguła domenowa (jednozdaniowa):** System klasyfikuje każdy nowy komunikat ESPI/EBI jako finansowy lub korporacyjny, wyciąga odpowiednie dane i generuje zwięzły, przykuwający post — zamiast narracji.

**Dwa tryby klasyfikacji:**

1. **Komunikat finansowy** (zawiera dane liczbowe: wyniki, przychody, zyski, zmiany %)
   - System wyciąga: zysk/przychód w mln PLN, % zmiana r/r, q/q, odniesienie do konsensusu rynkowego (jeśli dostępne)
   - Output: pigułka liczbowa — dane w formacie `X mln PLN (+Y% r/r)`

2. **Komunikat korporacyjny** (brak danych liczbowych: zmiana zarządu, umowa, zmiana adresu, etc.)
   - System wyciąga: kto, co, potencjalny wpływ na spółkę
   - Output: krótki opis zdarzenia — bez wymyślania liczb

**Reguły formatu X posta (wchodzą do promptu AI + kryteria supervisora):**
- Zaczyna się od nazwy spółki i tickera: `PKN Orlen $PKN` lub `$PKN PKN Orlen`
- Dane finansowe z emoji jako visual anchor: `📈 +12% r/r`, `💰 450 mln PLN`
- Kończy się hashtagami: `#ESPI #GPW #[ticker]`
- Długość: złoty środek — nie ucinać w połowie zdania, nie tysiące znaków (konto premium, ale rozsądnie)
- Ton: zachęca do interakcji (reakcje, komentarze) — naturalny, nie brzmi sztucznie ani cringowo
- Hard limit supervisora: max 3 próby generacji; po 3 failed → nie wysyłamy, alert do właściciela

**Kryteria oceny supervisora (obiektywne, nie semantyczne):**
- [ ] Post zaczyna się od nazwy spółki i tickera
- [ ] Zawiera co najmniej jedną liczbę (dla finansowych) lub jasne zdarzenie (dla korporacyjnych)
- [ ] Zawiera hashtagi
- [ ] Długość mieści się w przedziale [150–600 znaków] (do ustalenia dokładnie przy implementacji)
- [ ] Brak zdań urwanych w połowie
- [ ] Dane liczbowe zgodne ze źródłem (nie wymyślone)

## Non-Functional Requirements

- **Freshness:** nowy ESPI/EBI → email do właściciela w ciągu 15 minut od publikacji. Scheduler odpala co 10–15 minut w godzinach sesji giełdowej.
- **Failure alerting:** jeśli jakikolwiek etap pipeline'u zawiedzie (scraper, Gemini, baza danych) → właściciel dostaje alert email. Cicha awaria jest niedopuszczalna.
- **No duplicates:** ten sam komunikat nie może wygenerować więcej niż jednego outputu — nawet przy wielokrotnym uruchomieniu schedulera.
- **Integrity:** post nie może zawierać liczb ani faktów nieobecnych w źródle. Supervisor weryfikuje spójność ze źródłem.

## Non-Goals

- **Automatyczne postowanie na X przez API** — MVP generuje X-style post i wysyła go emailem; właściciel ręcznie decyduje kiedy i czy opublikować na X. Automatyzacja X API → v2. Rationale: kontrola nad publicznym kontem w MVP.
- **Panel webowy do przeglądania analiz** — baza danych jako archiwum; brak UI webowego w MVP. → v2. Rationale: scope down, oszczędność czasu.
- **Integracja z AI broker system (portfele, sygnały buy/sell)** — przyszły downstream consumer analiz. → v3+. Rationale: istniejący system wymaga osobnej integracji.
- **Rekomendacje inwestycyjne (buy/sell)** — system analizuje i podsumowuje, nie rekomenduje. Rationale: odpowiedzialność prawna (rekomendacja inwestycyjna wymaga licencji KNF).
- **Obsługa języka angielskiego** — posty i analizy w języku polskim w MVP. → future.

## Forward: deployment-model
Właściciel wskazuje na model scheduled jobs / cloud scheduler jako preferowany kierunek wdrożenia. Stack do ustalenia w `/10x-infra-research`.

## Forward: future-integrations
Właściciel posiada istniejący system z AI brokerami (różne strategie inwestycyjne, konkurujące portfele z prawdziwymi środkami). ESPI/EBI Analyzer jest naturalnym źródłem sygnałów/danych dla tego systemu w v3+. Integracja poza zakresem MVP.

Właściciel posiada działające implementacje podobnych rozwiązań technicznych (scraping, AI pipeline, schedulery). Może udostępnić kod/architekturę jako referencję — znacząco przyspieszy implementację. Uwzględnić przy starcie `/10x-bootstrapper` lub implementacji.
