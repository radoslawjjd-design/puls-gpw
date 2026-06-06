---
created: 2026-06-07
updated: 2026-06-07
source: Grok conversation (Radek) + X algorithm research 2026
applies_to: S-04 xpost-generation
---

# X Post Strategy — ESPI/EBI dla GPW

Materiał referencyjny dla S-04 (xpost-generation). Wyciągnięty z rozmowy z Grokiem
na temat algorytmu X w 2026 i optymalnego formatu postów o analizach ESPI/EBI.

---

## Algorytm X (2026) — kluczowe sygnały

Ranking (od najważniejszego):

1. **Replies i konwersacje** — ważą 13–27× więcej niż like'i. Odpowiadanie na komentarze w pierwszych 15–30 min jest krytyczne.
2. **Bookmarks** — silny sygnał, trudny do zmanipulowania. Warto prosić wprost.
3. **Wczesna prędkość** — pierwsze 30–60 minut decyduje o dalszym pushu algorytmu.
4. **Native media** — zdjęcia i krótkie wideo natywne (nie linki) dają +150% engagement.
5. **Threads (wątki)** — 3–7 tweetów biją pojedyncze posty (do 3× więcej impressions).
6. **Oryginalność** — własne opinie, dane, historia. Unikaj retweetów jako głównej treści.

---

## Czego UNIKAĆ (kary algorytmu)

- **Linki w głównym poście** → spadek zasięgu o 50–90%. Wrzucaj w pierwszym reply.
- Więcej niż 1–2 hashtagi (docelowo 0–2).
- Engagement bait: „Like jeśli zgadzasz się", „Taguj znajomego".
- Duplikaty, spam, czyste kopiuj-wklej z ESPI bez komentarza.
- Zewnętrzne linki w pierwszym tweecie.

---

## Struktura posta — Hook + Value + CTA

```
[HOOK — 1-2 linie przyciągające uwagę: liczba, zaskoczenie, pytanie, "curiosity gap"]

[TREŚĆ — skanowalna, białe spacje, max 1 główna myśl]

[CTA — pytanie lub prośba o bookmark prowokująca replies]
```

Długość: tekstowe posty często biją multimedia (+30% engagement), ale dobry obraz zwiększa watch time.

---

## Format dla 4 spółek jednocześnie (zalecany)

```
🚨 4 kluczowe ESPI/EBI ze spółek finansowych z GPW [dziś/rano]:

1️⃣ [TICKER] [Nazwa] – [kluczowy fakt w 1 zdaniu]
   → [kontekst / potencjalny wpływ na kurs]?

2️⃣ [TICKER] [Nazwa] – [kluczowy fakt]
   → [kontekst]?

3️⃣ [TICKER] [Nazwa] – [kluczowy fakt]
   → [kontekst]?

4️⃣ [TICKER] [Nazwa] – [kluczowy fakt]
   → [kontekst]?

Która spółka najbardziej Was interesuje? Komentuj 👇
#GPW #ESPI
```

**Dlaczego to działa:** hook + liczby + konkret + potencjalny wpływ na kurs (inwestorzy to kochają) + pytanie = replies.

---

## Format thread (lepsza na dłuższe analizy)

- **Tweet 1 (główny):** hook + zapowiedź: „Podsumowanie wieczorne: N ważnych ESPI z sektora finansowego #GPW"
- **Tweet 2–N:** po jednej spółce, screenshot + 2–3 zdania analizy
- **Ostatni tweet:** pytanie angażujące: „Która z nich ma największy potencjał? Daj znać w komentarzu."

---

## Zasady specyficzne dla ESPI/EBI na GPW

- Zawsze dodaj **własny krótki komentarz** — algorytm karze czyste kopiuj-wklej z GPW.
- Podawaj **kluczowe liczby** z komunikatu (kwoty, procenty, zmiany r/r).
- Daj **kontekst wpływu** — czy to dobra czy zła wiadomość, jaki potencjalny wpływ na kurs.
- **Hashtagi max:** `#GPW #ESPI` (2 wystarczą; więcej = kara).
- Linki do pełnych raportów → **wyłącznie w pierwszym reply**, nigdy w głównym poście.
- **Czas publikacji:** 16:30–18:00 po sesji (wieczorne podsumowanie) lub 7:30–9:00 przed sesją (poranne przeglądy).
- Opcjonalnie seria: codziennie o stałej godzinie „Daily ESPI Finansowe" — buduje nawyk followersów.

---

## Scoring → selekcja do posta

Spółki do posta wybierać na podstawie `analysis_score` z BQ (pole już dostępne po S-03):
- Preferuj `analysis_approved = TRUE` + najwyższy score.
- Podawaj `event_type` jako kontekst kategorii ogłoszenia.
- Ticket i `company` dostępne bezpośrednio z BQ.

---

## Przykład gotowego posta (wzorzec do testowania generatora)

```
🚨 4 kluczowe ESPI/EBI ze spółek finansowych z GPW dzisiaj:

1️⃣ XTB – Wzrost liczby klientów o 27% po wprowadzeniu nowego produktu
   → Najlepszy kwartał od 2023. Warto obserwować przed wynikami?

2️⃣ PKO – Podpisanie umowy kredytowej na 450 mln zł z funduszem unijnym
   → +12% wolumenu wczoraj. Największy kontrakt w historii spółki?

3️⃣ PZU – Wzrost składki o 18% r/r w Q1
   → Rekordowy zysk techniczny. Dobra prognoza na dywidendę?

4️⃣ CDR – Uruchomienie nowej platformy z AI
   → Partnerstwo z międzynarodowym graczem. Potencjał na +20–30%?

Która spółka najbardziej Was interesuje? Komentuj 👇
#GPW #ESPI
```
