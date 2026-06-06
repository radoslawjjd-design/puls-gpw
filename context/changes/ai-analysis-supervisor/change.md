---
id: ai-analysis-supervisor
title: "S-03: Analiza AI + scoring komunikatów ESPI/EBI"
status: plan_reviewed
created: 2026-06-06
updated: 2026-06-06
roadmap_id: S-03
tracking:
  linear: PUL-15
  github: 11
---

# S-03: Analiza AI + scoring komunikatów ESPI/EBI

Per-announcement pipeline uruchamiany w każdym loopie 15-min. Gemini Flash analizuje
`parsed_content` → structured JSON, hallucination gate weryfikuje fakty, scoring
łączy tier spółki + typ zdarzenia + badge priorytetu w jeden `analysis_score`.

## Prerequisites

- F-02 done — BQ klient gotowy
- S-02 done — `parsed_content` dostępny w BQ dla każdego ogłoszenia

## Scope

- `src/scraper.py` + `src/scraper.py Announcement` — dodanie `priority` badge (Phase 0)
- `db/bigquery.py` — nowe kolumny BQ (Phase 0 + Phase 1–3)
- `src/analyzer.py` — nowy moduł: Gemini analysis + hallucination gate + scoring (Phase 1–3)
- `main.py` — integracja analyzera po parse_announcement
- `tests/test_analyzer.py` — unit testy (Phase 4)

## Kluczowe decyzje (ustalone 2026-06-06)

### Phase 0 — Scraper enhancement (prerequisite)

Bankier.pl pokazuje badge priorytetu ogłoszenia w HTML:
`<span class="a-quotes-badge -orange-500 -priority">Ważny</span>`

Dodajemy do `Announcement` dataclass: `priority: str | None`
Wartości: `"Ważny"` / `"Średni"` / `None` (brak badge)
Nowa kolumna BQ: `priority STRING NULLABLE`

### Phase 1 — Gemini structured analysis

Gemini Flash: `parsed_content` → JSON:
```json
{
  "company": "Nazwa spółki",
  "ticker": "XYZ",
  "event_type": "wyniki_finansowe",
  "key_numbers": ["przychody 123M PLN", "zysk netto 45M PLN"],
  "sentiment": "positive",
  "summary_pl": "Krótkie podsumowanie po polsku"
}
```
Nowa kolumna BQ: `structured_analysis STRING NULLABLE`

### Phase 2 — Hallucination gate (Gemini-as-judge)

Drugie wywołanie Gemini: dostaje `parsed_content` + `structured_analysis`,
weryfikuje czy fakty/liczby/kwoty w analizie są zgodne ze źródłem.

Wynik BQ:
- `analysis_approved BOOL` — true = zatwierdzone, false = halucynacja
- `analysis_reject_reason STRING NULLABLE` — powód odrzucenia (gdy false)

Odrzucone ogłoszenia: nie trafiają do scoringu ani top-N w S-04.
Monitorowanie: pole `analysis_reject_reason` pozwala śledzić wzorce halucynacji.

### Phase 3 — Scoring

Formuła: `final_score = tier_bonus + event_type_score + priority_bonus`

#### Tier bonus

| Tier | Spółki | Bonus |
|---|---|---|
| 1 — portfel własny | DGN, ELT, SNT, TOA, VOT, XTB, PAS, KRU, LBW, APT | +40 |
| 2 — WIG20/blue chips | PKO, KGH, PKN, PGE, PZU, CDR, KTY, LPP, DNP, ZAB, PEO, ASB, CBF, DVL, CRI, DEK | +25 |
| 3 — mid-caps | MDV, ALR, TPE, MBK, ALE, PCO, BDX | +10 |
| 4 — pozostałe | fallback | +0 |

#### Event type score

| Score | Event type (wartość `event_type` z Gemini) |
|---|---|
| 100 | wyniki_finansowe |
| 95 | upadlosc / restrukturyzacja |
| 90 | przejecie / fuzja / wezwanie |
| 85 | dywidenda |
| 80 | emisja_akcji |
| 75 | kontrakt_znaczacy |
| 65 | transakcja_insiderow |
| 60 | wyniki_sprzedazowe |
| 55 | skup_akcji |
| 50 | zmiana_zarzadu |
| 20 | inne / compliance |

#### Priority badge bonus

| Badge | Bonus |
|---|---|
| "Ważny" | +20 |
| "Średni" / None | +0 |

Nowe kolumny BQ: `event_type STRING`, `analysis_score FLOAT64`

### Otwarte kwestie do `/10x-plan`

- Dokładny prompt dla Gemini analysis (few-shot examples z prawdziwych ogłoszeń)
- Dokładny prompt dla hallucination gate
- Obsługa `event_type` gdy Gemini zwróci nieznany typ (mapowanie do `inne`)
- Rate limiting Gemini API (30-50 ogłoszeń × 2 wywołania = 60-100 calls per run)
- Koszt Gemini Flash na run (szacunkowy)

## Architektura jobów (ustalone 2026-06-06)

- **Job 1** (istniejący, 15-min loop): scrape → parse → analyze → score (S-01 + S-02 + S-03)
- **Job 2** (nowy, dedykowany X-post): generacja nitki + post supervisor (S-04), trigger: 8:30/12:00/15:00/17:00
- **Job 3** (nowy lub połączony z Job 2): email delivery (S-05)

Czy S-04+S-05 to jeden job czy dwa — otwarte, do ustalenia przy `/10x-plan xpost-generation`.

## Następny krok

```
/10x-plan ai-analysis-supervisor
```
