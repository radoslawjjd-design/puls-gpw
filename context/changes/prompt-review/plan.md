# Prompt Review Implementation Plan

## Overview

Poprawiamy cztery Gemini-adjacent komponenty pipeline'u: prompty analizatora (`_ANALYSIS_SYSTEM_PROMPT`, `_GATE_SYSTEM_PROMPT`), kod analizatora oraz post_supervisor. Cel: obniżenie rejection rate gate'u i zgodność z `gemini-ai.md:12`.

## Current State Analysis

- Pipeline analizuje ~240 ogłoszeń; rejection rate gate 44.6% (107/240), z czego większość to czerwiec 8 (ticker-based, naprawione przez `799fb03`).
- Po fixie `799fb03` gate sprawdza: key_numbers + summary_pl. BQ pokazuje false negatives przez nadprecyzję check #2 (SPH: "rynek pozagiełdowy" vs "BSE AD").
- `zmiana_zarzadu` ma 65% rejection rate; część to pre-fix, ale AIN/YBS/AWM potwierdzają misklasyfikację zmian RN jako Zarząd.
- `_AnalysisResponse(BaseModel)` brakuje — wymagany przez `gemini-ai.md:12`.
- `sentiment` pole: zdefiniowane w prompcie, przechowywane w BQ, nigdy nie czytane downstream.
- `NULL ticker` rows (~13 w BQ) to ETF/TFI fundusze przechodzące przez pełny pipeline Gemini.

## Desired End State

Gate weryfikuje wyłącznie key_numbers. Analiza nie klasyfikuje zmian RN jako `zmiana_zarzadu`. Kod spełnia `gemini-ai.md:12`. Regresje event_type widoczne w logach.

### Key Discoveries

- `_GATE_SYSTEM_PROMPT` linia 105: check #2 `"Czy summary_pl jest spójne z treścią?"` → usuń.
- `_ANALYSIS_SYSTEM_PROMPT` linia 55: brak clarification dla zmiana_zarzadu vs RN → dodaj.
- `analyzer.py:136`: `json5.loads()` → nie ma Pydantic validation przed gate (wymóg `gemini-ai.md:12`).
- `analyzer.py:201`: `event_type = raw if raw in _VALID_EVENT_TYPES else "inne"` → brak logu.
- `analyzer.py:191`: guard `if not parsed_content` → dodaj analogiczny guard dla `if not ticker`.

## What We're NOT Doing

- Nie dodajemy `zmiana_rn` jako nowego event_type (wymagałoby BQ schema migration).
- Nie dodajemy few-shot examples do gate (zbyt wcześnie; zbierzmy dane post-fix najpierw).
- Nie naprawiamy gate failure semantyki `(None, None)` → ten bug nigdy nie wystąpił w produkcji (Q3: 0 wierszy).
- Nie ruszamy post_generator ani post_supervisor — supervisor działa perfekcyjnie (100% first-attempt).
- Nie dodajemy nowego event_type scoringu — `zmiana_rn` to przyszła decyzja.

## Implementation Approach

Trzy fazy niezależne, każda deployowalna osobno. Fazy 1–2 to wyłącznie zmiany tekstowe w promptach; faza 3 to additive code changes. Kolejność: gate prompt (największy wpływ) → analysis prompt → code quality.

## Critical Implementation Details

- `_AnalysisResponse` musi używać `model_config = ConfigDict(extra='ignore')` — Gemini może nadal zwracać `sentiment` przez chwilę po zmianie promptu; ignorujemy, nie failujemy.
- Ticker guard (`if not ticker`) dodaj przed `if not parsed_content`, żeby oba early returns były razem na górze funkcji.

---

## Phase 1: Gate Prompt — Usuń Check Summary_PL

### Overview

Gate weryfikuje teraz wyłącznie `key_numbers`. Usuwamy check #2 (`summary_pl`) i dodajemy eksplicytną listę pól których NIE weryfikujemy, żeby zapobiec "helpful" zachowaniu modelu (BQ: AIN odrzucone za `event_type` mimo że gate nie miał takiego check'u).

### Changes Required

#### 1. `_GATE_SYSTEM_PROMPT` w `src/analyzer.py`

**File**: `src/analyzer.py`

**Intent**: Zastąpić cały `_GATE_SYSTEM_PROMPT` nową wersją z jednym check'iem (key_numbers) zamiast dwóch.

**Contract**: Nowy prompt (zastępuje linie 95–113):

```
Jesteś audytorem analiz finansowych. Weryfikujesz czy liczby z analizy
komunikatu giełdowego są zgodne z jego oryginalną treścią.

Sprawdź TYLKO:
Czy liczby i kwoty w polu key_numbers mają odpowiedniki w oryginalnej treści?
  WAŻNE: Liczby mogą być sformatowane inaczej (np. "120 100 000 PLN" w tekście
  = "120,1 mln PLN" w analizie) — to jest POPRAWNE. Odrzuć tylko jeśli liczba
  z analizy nie ma żadnego odpowiednika w źródle lub rząd wielkości jest wyraźnie
  błędny (np. "120 mln" zamiast "12 mln").

Jeśli key_numbers jest pustą listą — zatwierdź (nie ma liczb do weryfikacji).

NIE weryfikuj: summary_pl, event_type, tickera, nazwy spółki.

Zwróć JSON:
{"approved": true, "reason": null}
lub
{"approved": false, "reason": "która liczba jest niezgodna i co jest prawidłową wartością"}
```

### Success Criteria

#### Automated Verification

- `uv run python -m pytest tests/ -x` — brak regresji

#### Manual Verification

- Uruchom analyzer na kilku ogłoszeniach z ostatnich dni; sprawdź w BQ że nie ma nowych odrzuceń z powodu "summary"
- SPH-style rejection (semantyczna różnica w summary) nie pojawia się już jako powód odrzucenia

---

## Phase 2: Analysis Prompt — Zmiana_Zarzadu + Usuń Sentiment

### Overview

Dwie niezależne zmiany w `_ANALYSIS_SYSTEM_PROMPT`:
1. Clarification dla `zmiana_zarzadu` — model ma przestać klasyfikować zmiany w Radzie Nadzorczej pod tym typem.
2. Usunięcie `sentiment` z pól output — martwe pole, oszczędność tokenów.

### Changes Required

#### 1. Usuń `sentiment` z listy pól w `_ANALYSIS_SYSTEM_PROMPT`

**File**: `src/analyzer.py`

**Intent**: Usunąć linię definiującą pole `sentiment` z output schema promptu.

**Contract**: Usuń linię 47 (`- sentiment: ocena wydźwięku (string: "positive", "negative", "neutral")`). Żadnych innych zmian w tym bloku.

#### 2. Dodaj clarification dla `zmiana_zarzadu` vs Rada Nadzorcza

**File**: `src/analyzer.py`

**Intent**: Doprecyzować znaczenie `zmiana_zarzadu` zaraz po liście dozwolonych wartości event_type.

**Contract**: Po linii `Jeśli nie możesz określić event_type — użyj "inne".` (linia 55) dodaj:

```
UWAGA zmiana_zarzadu: dotyczy WYŁĄCZNIE zmian w Zarządzie (Management Board).
Zmiany w Radzie Nadzorczej (Supervisory Board) → użyj "inne".
```

#### 3. Dodaj anti-hallucination instruction do `summary_pl`

**File**: `src/analyzer.py`

**Intent**: Zapobiec dodawaniu interpretacji i kontekstu których nie ma w komunikacie — gate po Phase 1 nie weryfikuje już `summary_pl`, więc prompt jest jedyną barierą.

**Contract**: Rozszerzyć definicję pola `summary_pl` w bloku JSON schema (obecnie linia 48):

```
- summary_pl: krótkie podsumowanie komunikatu po polsku, max 2 zdania (string).
  WAŻNE: opieraj się WYŁĄCZNIE na treści komunikatu — nie dodawaj kontekstu
  ani ocen których nie ma w tekście.
```

#### 4. Dodaj reguły `key_numbers` dla `zmiana_zarzadu` i `compliance`

**File**: `src/analyzer.py`

**Intent**: Zapobiec wyciąganiu bezwartościowych liczb (numery uchwał, % głosów na ZWZ) gdy komunikat nie zawiera istotnych danych finansowych. Bez tej reguły model wpada do fallbacku "max 3 kwoty" i hallucynuje lub wyciąga irrelevantne wartości.

**Contract**: W sekcji `=== ZASADY key_numbers ===`, przed linią `Dla pozostałych event_type:`, dodaj:

```
zmiana_zarzadu / compliance:
  key_numbers = [] — te komunikaty rzadko zawierają istotne liczby finansowe.
  Wyjątek: wymierne kwoty explicite podane w tekście (np. odprawa, kara regulacyjna).
```

#### 5. Rozdziel `wezwanie` od `kontrakt_znaczacy`

**File**: `src/analyzer.py`

**Intent**: Tender offer (wezwanie) ma inne priorytety niż kontrakt — cena za akcję i % pakietu są kluczowe dla oceny atrakcyjności wezwania, nie harmonogram płatności.

**Contract**: Istniejącą linię `kontrakt_znaczacy / przejecie / fuzja / wezwanie:` zastąpić dwoma osobnymi blokami:

```
kontrakt_znaczacy / przejecie / fuzja:
  Priorytet 1: Wartość transakcji/kontraktu
  Priorytet 2: Okres obowiązywania lub harmonogram płatności (jeśli istotny)

wezwanie:
  Priorytet 1: Cena za akcję w wezwaniu
  Priorytet 2: Łączna wartość wezwania lub % pakietu docelowego
```

### Success Criteria

#### Automated Verification

- `uv run python -m pytest tests/ -x` — brak regresji
- `grep "sentiment" src/analyzer.py` — nie zwraca linii w `_ANALYSIS_SYSTEM_PROMPT`

#### Manual Verification

- Przetestuj na ogłoszeniu dotyczącym zmiany w Radzie Nadzorczej (np. poprzednie odrzucone AIN/YBS): `event_type` = `"inne"`, nie `"zmiana_zarzadu"`
- Nowe analizy nie mają pola `sentiment` w `structured_analysis` JSON w BQ
- Ogłoszenie o wezwaniu: `key_numbers` zawiera cenę za akcję, nie harmonogram płatności
- Ogłoszenie zmiana_zarzadu bez kwot finansowych: `key_numbers = []`

---

## Phase 3: Code Quality — Pydantic, Logging, Ticker Filter

### Overview

Trzy additive code changes w `analyzer.py`:
1. `_AnalysisResponse(BaseModel)` — schema validation po `json5.loads`, zgodność z `gemini-ai.md:12`.
2. `logger.warning` gdy `event_type` nie jest w `_VALID_EVENT_TYPES` — widoczność regresji.
3. `if not ticker` guard — skip analizy dla ETF/TFI rows z NULL tickerem.

### Changes Required

#### 1. Dodaj `_AnalysisResponse(BaseModel)` i waliduj w `_call_analysis`

**File**: `src/analyzer.py`

**Intent**: Dodać Pydantic model który waliduje schema wyjścia Gemini przed przekazaniem do gate. Ignoruje dodatkowe pola (np. `sentiment` jeśli model nadal je zwraca).

**Contract**:

```python
from pydantic import BaseModel, ConfigDict, ValidationError

class _AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_type: str
    key_numbers: list[str]
    summary_pl: str
```

W `_call_analysis` zastąp `return json5.loads(response.text)` przez:
```python
data = json5.loads(response.text)
return _AnalysisResponse.model_validate(data).model_dump()
```
Dodaj `except ValidationError as exc:` jako osobny `except` przed ogólnym `except Exception`, logując `"Gemini analysis schema invalid: %s", exc`.

#### 2. Dodaj `logger.warning` przy event_type fallback

**File**: `src/analyzer.py`

**Intent**: Logować gdy `raw_event_type` z analizy nie jest w `_VALID_EVENT_TYPES` — sygnał regresji promptu widoczny w logach bez zaglądania w BQ.

**Contract**: Linia 201 — przed `event_type = raw_event_type if raw_event_type in _VALID_EVENT_TYPES else "inne"` dodaj:
```python
if raw_event_type not in _VALID_EVENT_TYPES:
    logger.warning(
        "Analyzer: unknown event_type %r for %s — falling back to 'inne'",
        raw_event_type, announcement_id,
    )
```

#### 3. Dodaj `if not ticker` guard na początku `analyze_announcement`

**File**: `src/analyzer.py`

**Intent**: Pominąć analizę Gemini dla ogłoszeń bez tickera (ETF/TFI, fundusze) — ~13 wierszy dziennie wg BQ, żadnej wartości analitycznej.

**Contract**: Zaraz po `if not parsed_content:` (linia 191) dodaj analogiczny guard:
```python
if not ticker:
    logger.info("Analyzer: skip %s — no ticker", announcement_id)
    return null_result
```

### Success Criteria

#### Automated Verification

- `uv run python -m pytest tests/ -x` — brak regresji
- `uv run python -c "from src.analyzer import _AnalysisResponse; _AnalysisResponse(event_type='inne', key_numbers=[], summary_pl='test')"` — nie rzuca wyjątku
- `grep "AnalysisResponse" src/analyzer.py` — zwraca definicję klasy i użycie w `_call_analysis`

#### Manual Verification

- Ogłoszenie ETF/TFI (NULL ticker) → log `"Analyzer: skip ... — no ticker"`, `structured_analysis=NULL` w BQ
- Ogłoszenie z nieznany `event_type` (wymuś sztucznie) → log `"unknown event_type ... falling back to 'inne'"`

---

## Testing Strategy

### Unit Tests

- `test_analysis_response_validation` — `_AnalysisResponse` przyjmuje valid dict, odrzuca brakujące pola, ignoruje extra fields (sentiment)
- `test_event_type_fallback_logs_warning` — `analyze_announcement` emituje WARNING gdy model zwraca nieznany event_type
- `test_skip_no_ticker` — `analyze_announcement` z `ticker=None` zwraca `null_result` bez wywołania Gemini

### Manual Testing

1. Uruchom `uv run python src/_run_analyze.py` (lub odpowiednik) na próbce 10 ogłoszeń z ostatnich dni
2. Sprawdź BQ: `SELECT analysis_reject_reason, COUNT(*) FROM ... GROUP BY analysis_reject_reason` — czy "summary" zniknęło z powodów odrzuceń
3. Sprawdź BQ: `SELECT event_type, COUNT(*) FROM ... WHERE structured_analysis IS NOT NULL AND published_at > CURRENT_DATE - 1 GROUP BY event_type ORDER BY 2 DESC` — czy zmiana_zarzadu rate spada

## References

- Research: `context/changes/prompt-review/research.md`
- Reguła parsowania JSON: `src/analyzer.py:136`, `.claude/rules/gemini-ai.md`
- Wzorzec Pydantic: `src/post_generator.py:100–101` (`_PostResponse`)
- BQ data: Q1–Q5 w `research.md` (sekcja "BQ Query Results")

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Gate Prompt — Usuń Check Summary_PL

#### Automated

- [x] 1.1 `uv run python -m pytest tests/ -x` — brak regresji — 3f861d3

#### Manual

- [x] 1.2 Odrzucenia z powodu "summary" zniknęły z nowych analiz w BQ — 3f861d3

### Phase 2: Analysis Prompt — Zmiana_Zarzadu + Usuń Sentiment + Anti-Halucynacja

#### Automated

- [x] 2.1 `uv run python -m pytest tests/ -x` — brak regresji — 189a834
- [x] 2.2 `grep "sentiment" src/analyzer.py` nie zwraca linii z _ANALYSIS_SYSTEM_PROMPT — 189a834

#### Manual

- [x] 2.3 Ogłoszenie o zmianie w RN klasyfikuje się jako "inne", nie "zmiana_zarzadu" — 189a834
- [x] 2.4 Nowe analizy w BQ nie mają pola `sentiment` w structured_analysis JSON — 189a834
- [x] 2.5 Ogłoszenie wezwania: `key_numbers` zawiera cenę za akcję, nie harmonogram płatności — 189a834
- [x] 2.6 Ogłoszenie zmiana_zarzadu bez kwot finansowych: `key_numbers = []` — 189a834

### Phase 3: Code Quality — Pydantic, Logging, Ticker Filter

#### Automated

- [x] 3.1 `uv run python -m pytest tests/ -x` — brak regresji
- [x] 3.2 `_AnalysisResponse` importuje się i waliduje poprawnie (quick smoke test)
- [x] 3.3 `grep "AnalysisResponse" src/analyzer.py` — definicja + użycie obecne

#### Manual

- [x] 3.4 NULL ticker ogłoszenie → skip log w Cloud Run + NULL w BQ
- [x] 3.5 Unknown event_type → WARNING log widoczny
