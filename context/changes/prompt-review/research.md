---
date: 2026-06-09T00:00:00+02:00
researcher: Radek
git_commit: e6f20f9599efe4527fabcd9f5463a9695f44c922
branch: master
repository: puls-gpw
topic: "Prompt review — analysis, gate, post generator, and supervisor"
tags: [research, prompts, analyzer, post-generator, post-supervisor, gemini, gate, hallucination]
status: complete
last_updated: 2026-06-09
last_updated_by: Radek
---

# Research: Prompt Review — Analysis, Gate, Post Generator, Supervisor

**Date**: 2026-06-09  
**Researcher**: Radek  
**Git Commit**: e6f20f9599efe4527fabcd9f5463a9695f44c922  
**Branch**: master  
**Repository**: puls-gpw

---

## Research Question

Pełny audit wszystkich 4 komponentów Gemini w pipeline (analyzer ANALYSIS, analyzer GATE, post_generator, post_supervisor): obecny stan promptów, znane failure modes, luki w walidacji, okazje do poprawy.

---

## Summary

Pipeline ma solidne fundamenty — `json5`, `_PostResponse(BaseModel)` w post_generator, deterministyczny supervisor, event-type-aware key_numbers. Cztery obszary wymagają uwagi przed dalszym rozwojem:

1. **Brak Pydantic na wyjściu analizy** — `analyzer.py` nie waliduje schematu przez model, mimo że reguła w `.claude/rules/gemini-ai.md:12` tego wymaga.
2. **Ciche failure modes** — `event_type` spada do `"inne"` bez logowania; gate failure → `analysis_approved=NULL` (stan ambiwalentny, nie odrzucenie).
3. **Brak pętli feedback** — supervisor odrzuca post ale przyczyny nigdy nie trafiają z powrotem do Gemini; 3 próby są identyczne.
4. **Niespójność formatu cashtaga** — prompt wymaga `( $TICKER )` (ze spacjami), supervisor weryfikuje tylko `$TICKER` (bez nawiasów).

Pole `sentiment` jest martwym polem (zdefiniowane w prompcie, zignorowane przez cały downstream) — do usunięcia.

---

## Detailed Findings

### 1. `_ANALYSIS_SYSTEM_PROMPT` — `src/analyzer.py:38–93`

#### Struktura i inputs

- **Długość**: ~55 linii, statyczny string constant w całości po polsku.
- **Input**: wyłącznie `parsed_content: str` jako `contents=` parameter Gemini (line 130). Zero kontekstu o tickerze/spółce w momencie analizy — celowa decyzja z commitu `799fb03`.
- **Model**: `gemini-2.5-flash-lite` (stała `_GEMINI_MODEL`).

#### Oczekiwany output — JSON schema

```json
{
  "event_type": "string (1 z 15 dozwolonych)",
  "key_numbers": ["string", "..."],
  "sentiment":   "positive | negative | neutral",
  "summary_pl":  "string (max 2 zdania, PL)"
}
```

#### Output parsing

- [`json5.loads(response.text)`](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L136) ✅ — tolerancja na trailing comma.
- Bare `except Exception` → `return None` przy dowolnym błędzie parsowania.
- `event_type` walidowany przeciwko `_VALID_EVENT_TYPES` (15 wartości). Jeśli nie pasuje → **cichy fallback do `"inne"` bez logu** (line 201). ❌

#### Failure modes

| # | Problem | Plik:linia | Dotkliwość |
|---|---------|-----------|------------|
| A1 | Brak Pydantic na wyjściu — `dict` trafia wprost do gate bez schema validation | [analyzer.py:136](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L136) | Wysoka — wymóg z `gemini-ai.md:12` |
| A2 | `event_type` fallback do `"inne"` bez logowania — nie ma sygnału regresji promptu | [analyzer.py:201](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L201) | Średnia |
| A3 | `sentiment` field: zdefiniowany w prompcie, wyciągany, przechowywany w BQ, ale nigdy nikt nie czyta — martwe pole | analyzer.py:47, downstream | Niska (czyszczenie) |
| A4 | `priority` bonus hardcodowany jako string `"Ważny"` — zmiana scrapera = cicha utrata wszystkich +20 bonusów | [analyzer.py:168](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L168) | Niska |

#### key_numbers — event-type-aware (commit `e9202ed`)

Blok `=== ZASADY key_numbers — PRIORYTET KWOT ===` (prompt lines 55–90) definiuje priorytety per event_type:
- `wyniki_finansowe`: przychody + YoY, zysk netto + YoY, EBITDA, marża
- `dywidenda`: DPS, łączna kwota, yield%, payout ratio
- `kontrakt/przejecie/fuzja/wezwanie`: wartość transakcji, harmonogram płatności
- `emisja/skup`: liczba akcji + cena, łączna wartość, % rozwodnienia
- `transakcja_insiderow`: kwota transakcji, liczba akcji + cena jednostkowa
- Fallback: max 3 najważniejsze liczby

Branching jest wyłącznie model-side (prompt instructions), żaden Python code nie rozgałęzia się po `event_type`.

---

### 2. `_GATE_SYSTEM_PROMPT` — `src/analyzer.py:95–113`

#### Struktura i inputs

- **Długość**: ~19 linii, statyczny string.
- **Input** (line 142–143): `"TREŚĆ KOMUNIKATU:\n{parsed_content}\n\nANALIZA:\n{structured_analysis}"` — ticker explicite wykluczony.
- **Prompt instrukcja**: "NIE weryfikuj tickera ani nazwy spółki — są pobierane ze strony profilu, nie z tekstu komunikatu."

#### Oczekiwany output

```json
{"approved": true, "reason": null}
// lub
{"approved": false, "reason": "krótkie wyjaśnienie"}
```

#### Output parsing

- [`json5.loads(response.text)`](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L154) ✅
- `bool(data["approved"])` — **hard key access**, `KeyError` jeśli pole brak → wpadnie do `except Exception` → `(None, None)`. ❌

#### Gate failure flow — ambiguous NULL state

```
gate throws → (None, None)
→ analyze_announcement: analysis_approved=None, analysis_reject_reason=None, analysis_score=None
→ save_analysis_result() zapisuje do BQ z analysis_approved=NULL
→ BQ query behavior unknown (NULL ≠ FALSE ≠ TRUE)
```

To **nie jest odrzucenie** — to stan ambiwalentny, który może nieoczekiwanie przepuścić komunikat do post generatora w zależności od BQ query logic.

#### Failure modes

| # | Problem | Plik:linia | Dotkliwość |
|---|---------|-----------|------------|
| G1 | Gate failure → `analysis_approved=NULL` (ambiwalentny stan, nie odrzucenie) | [analyzer.py:156–158](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L156) | Wysoka |
| G2 | Hard `data["approved"]` access — `KeyError` → `(None, None)` zamiast explicit rejection | [analyzer.py:155](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/analyzer.py#L155) | Średnia |
| G3 | Brak retry na gate failure (network/quota transient) — 1 próba, potem NULL state | analyzer.py:142 | Średnia |
| G4 | Brak few-shot examples w gate prompcie — zdefiniowane jako deferred w S-03 | context/archive S-03 plan | Niska (opcjonalne) |

---

### 3. `_SYSTEM_PROMPT` — `src/post_generator.py:20–97`

#### Struktura i inputs

- **Długość**: ~78 linii, statyczny string.
- **Sekcje**: zakaz rekomendacji (9 zabronionych fraz PL), formuła tweet count, format hooka, format body tweetów, format closingu, format output.
- **Dynamic hook phrase**: `fraza_hooka` injektowany przez user message (line 147–153), mapowany przez `_HOOK_PHRASES` dict (lines 14–18).

**Assemblacja user message** (lines 149–153):
```
fraza_hooka: "<hook phrase>"
Dane: <JSON enriched list: [{ticker, company, event_type, key_numbers, summary_pl}]>
Wygeneruj wątek: DOKŁADNIE N tweetów (1 hook + M spółek + 1 closing).
```

Pola `url` i `title` NIE są przekazywane do Gemini — celowe (tweety bez linków per X strategy).

#### Oczekiwany output

```json
{"tweets": ["tweet1", "tweet2", ..., "tweetN"]}
```

Tweet count formula: `N+2` gdzie N = liczba spółek (1→3, 2→4, 3→5, 4→6).

#### Output parsing i walidacja

- [`json5.loads(response.text)`](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_generator.py#L165) ✅
- [`_PostResponse.model_validate(data)`](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_generator.py#L166) ✅ — Pydantic model na `{"tweets": list[str]}`.
- Empty list → `None` + warning.
- `ValidationError` → `None`.

#### Failure modes

| # | Problem | Plik:linia | Dotkliwość |
|---|---------|-----------|------------|
| P1 | Brak feedback loop — `issues` z supervisora nie trafiają z powrotem do Gemini na próbach 2 i 3 | [post_main.py:105–115](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_main.py#L105) | Wysoka |
| P2 | Silent `structured_analysis` parse failure → puste `key_numbers`/`summary_pl` → Gemini może hallucynować bez danych | [post_generator.py:129–136](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_generator.py#L129) | Średnia |
| P3 | Window default fallback — `_HOOK_PHRASES.get(window or "", ...)` bez log na unknown window | [post_generator.py:147](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_generator.py#L147) | Niska |
| P4 | Body tweet 140–180 char guidance — tylko w prompcie, nie weryfikowane przez supervisora | post_generator.py:56 | Niska |

---

### 4. `post_supervisor.py` — rule-based, bez Gemini

#### Checklist (lines 43–76)

| # | Check | Linia |
|---|------|-------|
| 1 | Dokładna liczba tweetów (expected_tweets) lub `n >= 3` | 45–49 |
| 2 | Każdy tweet ≤ 280 znaków (Python `len()`) | 51–53 |
| 3 | Każdy ticker z `tickers` jako `$TICKER` w body tweets (tweets[1:-1]) | 55–58 |
| 4 | `#GPW` w ostatnim tweecie | 60–61 |
| 5 | `rekomendacj` (case-insensitive) w ostatnim tweecie | 63–65 |
| 6 | Żaden tweet nie kończy się na `...` / `…` | 67–69 |
| 7 | Brak match inwestycyjnych wzorców regex (12 patterns) | 71–74 |

**3 retries** w `post_main.py:105` (`_MAX_ATTEMPTS = 3`). Każda próba to identyczne wywołanie Gemini — brak eskalacji instrukcji.

#### Failure modes

| # | Problem | Plik:linia | Dotkliwość |
|---|---------|-----------|------------|
| S1 | Check #3 weryfikuje `$TICKER` (bez nawiasów), ale prompt wymaga `( $TICKER )` ze spacjami — niespójność, format może dryfować niezauważony | [post_supervisor.py:55–58](https://github.com/radoslawjjd-design/puls-gpw/blob/e6f20f9599efe4527fabcd9f5463a9695f44c922/src/post_supervisor.py#L55) | Średnia |
| S2 | Brak weryfikacji: 🚨 w pierwszym tweecie, 📊 w body tweetach — emoji mogą zniknąć po cichu | post_supervisor.py | Niska |
| S3 | `len()` w check #2 — emoji liczą się jako 1 char w Pythonie, X liczy inaczej dla niektórych emoji | post_supervisor.py:52 | Niska |

---

## Code References

- `src/analyzer.py:38–93` — `_ANALYSIS_SYSTEM_PROMPT`
- `src/analyzer.py:95–113` — `_GATE_SYSTEM_PROMPT`
- `src/analyzer.py:125–139` — `_call_analysis()` z `json5.loads`
- `src/analyzer.py:142–158` — `_call_gate()` z hard key access + (None,None) fallback
- `src/analyzer.py:161–169` — `_compute_score()` formuła
- `src/analyzer.py:200–203` — `event_type` validation + fallback
- `src/post_generator.py:14–18` — `_HOOK_PHRASES` dict
- `src/post_generator.py:20–97` — `_SYSTEM_PROMPT`
- `src/post_generator.py:100–101` — `_PostResponse(BaseModel)`
- `src/post_generator.py:116–153` — input assembly (dedup, parse, enrich)
- `src/post_generator.py:165–176` — output parsing + validation
- `src/post_supervisor.py:8–23` — `_INVESTMENT_ADVICE_PATTERNS`
- `src/post_supervisor.py:43–76` — `validate_post()` — 7 checks
- `src/post_main.py:105–115` — 3-attempt retry loop (bez feedback)

---

## Architecture Insights

### Co działa dobrze (nie ruszać)

- `json5.loads` we wszystkich 4 callsitach — zaimplementowane zgodnie z lessons.md.
- `_PostResponse(BaseModel)` w post_generator — Pydantic na wyjściu generatora.
- Deterministyczny supervisor (bez Gemini) — zgodnie z decyzją z PRD: "sztywne, obiektywne reguły" aby uniknąć LLM-as-judge bias.
- Usunięcie tickera/company z analizy Gemini — `799fb03` clean design.
- Event-type-aware key_numbers blok — `e9202ed` znacząca poprawa jakości.
- `fraza_hooka` per window — `3eed975`.
- `( $TICKER )` format w przykładach promptu — `5c514a4`.

### Wzorce wymagające standaryzacji

- **Pydantic validation**: post_generator ma `_PostResponse`, analyzer nie ma odpowiednika `AnalysisResponse`. Niespójność.
- **Gate failure semantyka**: `(None, None)` → `analysis_approved=NULL` vs `(False, reason)` → `analysis_approved=False`. Różne kody drogi — BQ musi obsługiwać oba.
- **Feedback loop architektura**: supervisor wie dlaczego odrzuca, ale ta wiedza nie wraca do Gemini. Klasyczny pattern do naprawy w LLM pipelinach.

---

## Historical Context (from prior changes)

### S-03 (ai-analysis-supervisor) — context/archive/2026-06-06-ai-analysis-supervisor/

- Oryginalne prompty miały `company` i `ticker` w output schema (plan.md:242–262) — usunięte w `799fb03` jako czystsza architektura.
- Gate oryginalne sprawdzało ticker against raw ESPI text (plan.md:285–287) — zmienione bo ESPI często nie zawiera symbolu giełdowego.
- **Deferred**: few-shot examples w promptach ("calibrate post-launch") — nadal nie zaimplementowane.
- **Deferred**: `event_type` misclassification monitoring przez `analysis_reject_reason` patterns.

### S-04 (xpost-generation) — context/archive/2026-06-08-xpost-generation/

- Supervisor celowo rule-based, nie LLM — "sztywne, obiektywne reguły oceny" (PRD:58).
- Plan-review F1: window boundaries inclusive `<=` na przejściach.
- **Cloud Run max-retries=0** + supervisor 3 retries — to jest deliberate design (infrastructure.md:47), zapobiega 9x collision.
- `_run_generate_post.py` ma hard guard `len(announcements) < 2: sys.exit(0)`, ale `post_main.py` nie — single-company thread (3 tweety) jest możliwy w produkcji.

### Settled decisions (nie kwestionować)

| Decyzja | Źródło |
|---------|--------|
| key_numbers jako readable strings (`120,1 mln PLN`) | S-03 plan.md:261 |
| Gate: tolerancja formatowania liczb, strictness na summary | S-03 plan.md:285–287 |
| analysis_score=NULL gdy gate odrzuca | S-03 impl-review F6 |
| json5 zamiast json | lessons.md + S-04 Phase 0 |
| Supervisor deterministyczny (bez Gemini) | PRD:58, PRD:64 |
| Max retries: supervisor=3, Cloud Run=0 | infrastructure.md:47 |
| Top-N = 4 companies per thread | roadmap.md:104 |

---

## BQ Queries — do uruchomienia ręcznie

### Q1: Gate false negatives — ile dobrych analiz jest odrzucanych

```sql
SELECT
  ticker,
  event_type,
  analysis_reject_reason,
  structured_analysis,
  published_at
FROM `puls-gpw.espi_ebi.announcements`
WHERE analysis_approved = false
  AND analysis_reject_reason IS NOT NULL
ORDER BY published_at DESC
LIMIT 20
```

**Interpretacja**: Szukaj wzorców w `analysis_reject_reason` — czy gate odrzuca za powody które są fałszywe (np. formatting numbers)? Czy reject reasons są sensowne?

### Q2: Jakość zatwierdzonych analiz — key_numbers

```sql
SELECT
  ticker,
  event_type,
  structured_analysis,
  analysis_score,
  published_at
FROM `puls-gpw.espi_ebi.announcements`
WHERE analysis_approved = true
ORDER BY published_at DESC
LIMIT 20
```

**Interpretacja**: Otwórz `structured_analysis` JSON, sprawdź `key_numbers` array. Czy liczby są dobrze sformatowane? Czy event_type-aware prompt działa (wyniki finansowe = revenue+YoY)?

### Q3: Ambiguous NULL state — gate failures

```sql
SELECT
  ticker,
  event_type,
  analysis_approved,
  analysis_reject_reason,
  analysis_score,
  published_at
FROM `puls-gpw.espi_ebi.announcements`
WHERE analysis_approved IS NULL
ORDER BY published_at DESC
LIMIT 20
```

**Interpretacja**: Znalezienie wierszy z `analysis_approved IS NULL` potwierdza, że gate failure → NULL state się zdarza. Sprawdź czy te wiersze trafiają do generatora postów.

### Q4: Rozkład event_type — czy "inne" jest nadreprezentowane

```sql
SELECT
  event_type,
  COUNT(*) as cnt,
  COUNTIF(analysis_approved = true) as approved,
  COUNTIF(analysis_approved = false) as rejected,
  ROUND(AVG(analysis_score), 1) as avg_score
FROM `puls-gpw.espi_ebi.announcements`
WHERE structured_analysis IS NOT NULL
GROUP BY event_type
ORDER BY cnt DESC
```

**Interpretacja**: Jeśli `inne` jest >30% przy dobrych komunikatach, sugeruje że model źle klasyfikuje. Spójrz na `analysis_approved` ratio per event_type — czy niektóre typy są bardziej odrzucane niż inne?

### Q5: Supervisor failure rate — ile postów nie dochodzi do wysłania

```sql
SELECT
  supervisor_attempts,
  COUNT(*) as cnt,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) as pct
FROM `puls-gpw.espi_ebi.announcements`
WHERE supervisor_attempts IS NOT NULL
GROUP BY supervisor_attempts
ORDER BY supervisor_attempts
```

**Interpretacja**: Czy `supervisor_attempts=3 AND post_text IS NULL` (pełna porażka) się zdarza? Jeśli tak, `issues` z logów pokaże który check pada.

---

## BQ Query Results — 2026-06-09

### Ogólne statystyki (z `structured_analysis IS NOT NULL`)

| Łącznie | Approved | Rejected | NULL state |
|---------|----------|----------|------------|
| 240 | 133 (55.4%) | 107 (44.6%) | **0** |

**NULL state = 0** — bug G1 (gate failure → NULL) nie zdarzyło się jeszcze w produkcji. Ryzyko teoretyczne.

---

### Q1: Wzorce odrzuceń gate

Analiza 20 ostatnich odrzuconych analiz ujawnia dwa odrębne okresy:

**Czerwiec 8 (pre-`799fb03`)** — dominuje wzorzec: **ticker nie w dokumencie** (~75% odrzuceń):
- `BRO/wyniki_finansowe`: "Ticker 'BCZ' is not mentioned in the document"
- `AWM/zmiana_zarzadu`: "Ticker 'AWX' is not mentioned in the provided text"
- `OML/inne`, `EBX/zmiana_zarzadu`, `LKD/transakcja_insiderow`, `PLI/zmiana_zarzadu` — identyczny wzorzec
- Wniosek: fix `799fb03` powinien wyeliminować tę kategorię od dziś

**Czerwiec 9 (post-`799fb03`)** — odrzucenia contentowe:
- `NULL/wyniki_finansowe (11:13)`: "key_number 'WAN: Brak danych' nie ma odpowiednika w oryginalnej treści" — gate prawidłowo złapał zmyśloną wartość
- `VIV/inne (10:52)`: "nieprawidłowe udziały procentowe — 57,93% zamiast 3,51%" — poprawne odrzucenie (hallucynacja liczb)
- `SPH/transakcja_insiderow (08:52)`: gate odrzucił za "rynek pozagiełdowy" vs "BSE AD" — **false negative**: semantyczna nadprecyzyjność gate'u w podsumowaniu

---

### Q4: Rozkład event_type z rejection rates

| event_type | cnt | approved | rejected | rej% | avg_score |
|---|---|---|---|---|---|
| inne | 115 | 69 | 46 | 40% | 23.3 |
| transakcja_insiderow | 36 | 15 | 21 | **58%** | 70.3 |
| zmiana_zarzadu | 23 | 8 | 15 | **65%** | 53.1 |
| wyniki_finansowe | 19 | 11 | 8 | 42% | 109.1 |
| kontrakt_znaczacy | 10 | 6 | 4 | 40% | 90.0 |
| emisja_akcji | 9 | 7 | 2 | 22% | 95.0 |
| compliance | 8 | 4 | 4 | 50% | 20.0 |
| skup_akcji | 7 | 5 | 2 | 29% | 55.0 |
| wyniki_sprzedazowe | 6 | 3 | 3 | 50% | 73.3 |
| dywidenda | 6 | 4 | 2 | 33% | 96.3 |

**Kluczowe obserwacje**:
1. `inne` = **47.9% wszystkich analiz** — prawie połowa. Część to legitymne "inne" (ETF wyceny, sprostowania), ale część to misklasyfikacje (np. zmiana RN zamiast `zmiana_zarzadu`).
2. **transakcja_insiderow 58% rejection** — najwyższy rate po zmiana_zarzadu. Przyczyna (z Q1): insider transactions mają złożone formaty (multi-waluta: BGN, EUR, warrants + akcje), gate często odrzuca za semantic precyzję w podsumowaniu.
3. **zmiana_zarzadu 65% rejection** — gate prawidłowo łapie misklasyfikację (zmiana Rady Nadzorczej zaklasyfikowana jako `zmiana_zarzadu`). Jeden z najczęstszych błędów analizy.

---

### Q2: Jakość zatwierdzonych analiz

Próbka zatwierdzonych (najnowsze 20) — ogólnie **dobra jakość** po wprowadzeniu event-type-aware key_numbers:
- `PKN/dywidenda`: "Dywidenda na akcję: 8,00 PLN, Łączna kwota: 9,29 mld PLN" ✅
- `ACT/wyniki_sprzedazowe`: "Obrót: 240 mln PLN (+5,26% r/r), Marża: ok. 8,5%" ✅
- `BDX/kontrakt`: pełny zestaw (wartość, termin, gwarancja, kary) ✅
- `MOC/emisja_akcji`: tylko "4.120.631 akcji serii K" — brak ceny (prawdopodobnie rejestracja warunkowa bez ceny w dokumencie) ⚠️

Problemowe: wiersze z `ticker=NULL` (ETF/TFI: AgioFunds, PZU ETF) — przechodzą przez cały pipeline Gemini, generują `inne/empty key_numbers`. Powinny być filtrowane wcześniej.

---

### Q5: Supervisor failure rate

**100% first-attempt success** (25/25). Supervisor loop nie jest problemem — wszystkie posty przeszły za 1. razem.

**Główny problem jest upstream w gate, nie w post_generator/supervisor.**

---

## Open Questions

1. **Jak BQ query w post generatorze filtruje `analysis_approved IS NULL`?** Jeśli zapytanie robi `WHERE analysis_approved = true`, NULL rows są bezpiecznie pomijane. Jeśli `WHERE analysis_approved != false` lub brak filtra, NULL rows mogą przeciec do generatora. Sprawdzić `db/bigquery.py:fetch_top_n_for_window`.

2. **Jaka jest aktualna false-negative rate gate'u po usunięciu ticker verification (`799fb03`)?** Przed fixem gate odrzucał przez ticker; po fixie powinno być lepiej — Q1 pokaże.

3. **Czy few-shot examples w promptach (deferred z S-03) byłyby warte kosztu tokenów?** Lessons.md wzmiankuje że zbędne wymagania zwiększają koszty i degenerują wyniki. Decyzja: few-shot tylko jeśli Q1 pokaże systematyczne false negatives z konkretnym wzorcem.

4. **Czy `sentiment` field powinien być usunięty czy repurposed?** Jeśli nie ma planów jego użycia, czystsza opcja to usunięcie z promptu (oszczędność tokenów + mniej pól do walidacji).
