# Prompt Review — Plan Brief

> Full plan: `context/changes/prompt-review/plan.md`
> Research: `context/changes/prompt-review/research.md`

## What & Why

Poprawiamy prompty Gemini i kod analizatora w pipeline puls-gpw. Główny problem: gate odrzuca 44.6% analiz (107/240 w BQ), część to false negatives spowodowane sprawdzaniem `summary_pl` zamiast tylko liczb. Dodatkowe problemy: misklasyfikacja zmian w RN jako `zmiana_zarzadu`, brakująca Pydantic validation (wymagana przez `gemini-ai.md:12`) i ~10-15 Gemini calls/dzień marnowanych na ETF/TFI bez tickera.

## Starting Point

Commit `799fb03` usunął weryfikację tickera z gate'u — fix wdrożony wczoraj. Większość czerwcowych 8 odrzuceń była ticker-based i powinna zniknąć. Nadal: gate check #2 (`summary_pl`) powoduje false negatives; zmiana_zarzadu 65% rejection; brak Pydantic na wyjściu analizy.

## Desired End State

Gate weryfikuje wyłącznie `key_numbers` — zero false negatives od semantyki `summary_pl`. Zmiany w Radzie Nadzorczej klasyfikują się jako `"inne"`, nie `"zmiana_zarzadu"`. `_AnalysisResponse(BaseModel)` chroni pipeline przed schema drift. Regresje event_type widoczne w Cloud Run logs. NULL-ticker ETF/TFI ogłoszenia pomijane bez Gemini call.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Gate scope | Usuń summary_pl check | BQ: 1 false negative z powodu "BSE vs pozagiełdowy" — semantyczna precyzja nie jest warta false negatives | Plan |
| Gate instruction | Dodaj eksplicytną listę NIE weryfikuj | Gemini "z pomocą" sprawdzało event_type mimo braku takiego check'u (AIN) | Research |
| zmiana_zarzadu | Prompt clarification, nie nowy event_type | Zero zmian w BQ schema, wystarczy 2 linie tekstu | Plan |
| sentiment removal | Usuń z promptu | Martwe pole — zero użycia downstream po stronie kodu | Research |
| Pydantic | `extra="ignore"` w ConfigDict | Model może chwilowo nadal zwracać sentiment po zmianie promptu | Plan |
| Ticker filter | Guard w `analyze_announcement` | 1-liner, jedno miejsce, zero ryzyka | Plan |
| Gate failure semantics | Zostaw (None, None) | Nigdy nie wystąpiło w prod (Q3: 0 wierszy) | Research |
| post_supervisor | Nie ruszamy | 100% first-attempt success rate | Research (BQ Q5) |

## Scope

**In scope:**
- `_GATE_SYSTEM_PROMPT` — usuń check summary_pl, dodaj eksplicytne "NIE weryfikuj" list
- `_ANALYSIS_SYSTEM_PROMPT` — usuń `sentiment`, dodaj zmiana_zarzadu vs RN clarification
- `_AnalysisResponse(BaseModel)` z `extra="ignore"` w `_call_analysis`
- `logger.warning` przy event_type fallback do "inne"
- `if not ticker` guard w `analyze_announcement`

**Out of scope:**
- `post_generator.py` / `post_supervisor.py` — bez zmian
- Nowy event_type `zmiana_rn` — nie teraz
- Few-shot examples w gate
- Gate failure semantics `(None, None)` → explicit False
- BQ schema migrations

## Architecture / Approach

Trzy fazy niezależne, każda to osobny commit. Fazy 1–2 to wyłącznie string replacements w `analyzer.py`; faza 3 to additive code (nowa klasa, 3 małe zmiany). Żaden z commitów nie wymaga migracji danych.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Gate Prompt | Gate sprawdza tylko key_numbers | Model może nadal "pomagać" — eksplicytna lista NIE weryfikuj to mitygacja |
| 2. Analysis Prompt | zmiana_zarzadu clarification, usuń sentiment, anti-halucynacja summary_pl, wezwanie osobne priorytety, zmiana_zarzadu/compliance → `[]` | Gemini może ignorować krótkie notatki — do monitorowania w BQ |
| 3. Code Quality | Pydantic validation + logging + ticker skip | Additive — ryzyko minimalne |

**Prerequisites:** `799fb03` wdrożone (tak, od wczoraj)  
**Estimated effort:** ~1 sesja, 3 fazy w jednym PR

## Open Risks & Assumptions

- Po usunięciu check summary_pl gate może stać się zbyt permisywny — monitorować przez 1-2 dni czy halucynacje w summary_pl docierają do postów.
- zmiana_zarzadu prompt clarification może być niewystarczająca jeśli model silnie "wie" że RN to zarząd — fallback: few-shot examples w fazie 4 (not planned).
- Dane BQ z 2026-06-09 mają ~1 dzień od `799fb03` — pełny obraz rejection rate post-fix będzie widoczny za ~2-3 dni.
