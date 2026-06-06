# S-03: Analiza AI + scoring komunikatów ESPI/EBI — Plan Brief

> Full plan: `context/changes/ai-analysis-supervisor/plan.md`
> Change: `context/changes/ai-analysis-supervisor/change.md`

## What & Why

Rozszerzamy 15-minutowy pipeline o etap analizy AI dla każdego nowego ogłoszenia ESPI/EBI.
Gemini Flash analizuje `parsed_content` → strukturyzowany JSON, drugi call Gemini weryfikuje
czy analiza nie zawiera halucynacji, scoring łączy tier spółki + typ zdarzenia + badge priorytetu
w `analysis_score`. Wynik w BQ gotowy do agregacji przez S-04 (X-post generation).

## Starting Point

Pipeline po S-02 wykonuje: `scrape → insert_announcement → parse_announcement → update_parsed_content`.
Każde ogłoszenie ma `parsed_content` (string ≤15k znaków lub NULL gdy parse się nie udał).
Brak `google-genai` w zależnościach; brak pola `priority` w scraper/BQ.

## Desired End State

Po każdym 15-minutowym loopie każde nowe ogłoszenie ma w BQ:
`priority`, `structured_analysis` (JSON), `analysis_approved` (bool), `analysis_reject_reason`,
`event_type`, `analysis_score`. Ogłoszenia z `analysis_approved=TRUE` i najwyższym `analysis_score`
są gotowe do wybrania przez S-04 w oknach 8:30/12:00/15:00/17:00.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Gemini SDK | `google-genai` via Vertex AI | Brak nowego sekretu — używa service accounta GCP; widoczny w Cloud Console | Plan |
| Gemini model | `gemini-3.1-flash-lite` | GA bez daty deprecacji, lepsza hallucination resistance niż 2.5 Flash-Lite (które wychodzi Oct 2026) | Plan |
| JSON enforcement | `response_mime_type="application/json"` | SDK wymusza czysty JSON, eliminuje problem z markdown wrapping | Plan |
| Błąd API / bad JSON | Skip + WARNING + NULL pola | Jeden błąd nie blokuje reszty ogłoszeń w loopie | Plan |
| Rate limiting | Sekwencyjnie bez delay | X-post odczytuje BQ o stałych porach (8:30/12:00/15:00/17:00) — czas analizy nie krytyczny | Plan |
| Gate weryfikacja | Semantyczna równoważność numeryczna | `120 100 000 PLN` = `120,1 mln PLN` — dozwolone; unikamy false-positive przy formatowaniu | Plan |
| Liczby w analizie | Czytelny format (`120,1 mln PLN`) | Lepszy UX w X-postach — analiza Gemini już formatuje liczby | Plan |
| Score gdy odrzucone | `analysis_score=NULL` | Odrzucone ogłoszenia nie trafiają do top-N w S-04 | Change |
| Tier fallback | Tier 4 (+0) gdy ticker nieznany | Ticker=NULL praktycznie niemożliwy przy 2-hop; T4 jako bezpieczny fallback | Plan |
| Prompt styl | Zero-shot z explicit JSON schema | Brak potrzeby przygotowania few-shot examples teraz; kalibracja po obserwacji | Plan |

## Scope

**In scope:**
- Phase 0: `priority` badge w `Announcement` dataclass + BQ kolumna + `insert_announcement()`
- Phase 1: Nowy moduł `src/analyzer.py` (klient Gemini, analiza, gate, scoring) + 5 nowych kolumn BQ + `save_analysis_result()`
- Phase 2: Integracja w `main.py` (po `update_parsed_content`)
- Phase 3: 14 unit testów z mock SDK

**Out of scope:**
- Generowanie X-posta (S-04)
- Re-analiza istniejących wierszy BQ (batch backfill)
- Asyncio/parallel Gemini calls
- Few-shot examples w promptach (kalibracja post-launch)

## Architecture / Approach

```
main.py pętla (per ogłoszenie):
  insert_announcement(... priority) → BQ
  parse_announcement()              → parsed_content
  update_parsed_content()           → BQ
  analyze_announcement()            → AnalysisResult
    ├─ _call_analysis(parsed_content)     → Gemini (analysis prompt)
    ├─ _call_gate(content, analysis)      → Gemini (gate prompt)
    └─ _compute_score(event_type, ...)    → float (pure Python)
  save_analysis_result()            → BQ UPDATE
```

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 0. Priority badge | `priority` w scraper + BQ | Selektor CSS `-priority` może być inny na niektórych stronach |
| 1. src/analyzer.py | Pełny moduł analizy + BQ schema + save function | Gemini może słabo klasyfikować `event_type` bez few-shot — kalibracja post-launch |
| 2. main.py integration | Pipeline end-to-end z analizą | BigQueryError przy `save_analysis_result` propaguje do alertu |
| 3. Unit tests | 14 testów z mock SDK | Ścieżka patcha zależy od implementacji — może wymagać korekty |

**Prerequisites:** F-02 done (BQ klient), S-02 done (`parsed_content` w BQ); Vertex AI API włączone w projekcie GCP (`aiplatform.googleapis.com`) — brak nowego sekretu, używa istniejącego service accounta
**Estimated effort:** ~2 sesje (Phase 0+1 pierwsza, Phase 2+3 druga)

## Open Risks & Assumptions

- Gemini może źle klasyfikować `event_type` dla niszowych ogłoszeń (szczególnie zagraniczne spółki NC) — mitygacja: iteracyjna kalibracja promptów po pierwszych produkcyjnych uruchomieniach; tier list i event scores można poprawić bez zmiany kodu
- Gate może być zbyt restrykcyjny jeśli `parsed_content` ma niekompletne liczby (np. tabele z PDF były źle sparsowane) — monitoruj `analysis_reject_reason` po wdrożeniu

## Success Criteria (Summary)

- BQ: nowe ogłoszenia mają `analysis_score` i `event_type` wypełnione po każdym loopie
- Ogłoszenia z `analysis_approved=FALSE` mają `analysis_reject_reason` wskazujący powód
- `uv run pytest tests/ -v` — 100% pass (w tym 14 nowych testów analyzer)
