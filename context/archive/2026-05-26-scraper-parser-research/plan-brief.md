# F-01: Bankier.pl HTML & PDF Research — Plan Brief

> Full plan: `context/changes/scraper-parser-research/plan.md`

## What & Why

Zweryfikuj aktualność selektorów CSS z referencyjnego `oldProjectData/bankier.py` i oceń format PDF-ów ESPI/EBI za pomocą pypdf. Bez tego S-01 (scraper) i S-02 (parser) nie mogą być planowane — F-01 jest krytyczną ścieżką do North Star.

## Starting Point

W `oldProjectData/` istnieje produkcyjny scraper Bankier.pl (`bankier.py`) i parser PDF/HTML (`content_parser.py`) z poprzedniego projektu. Selektory CSS działały w produkcji ~2026-04-23 ale nie były testowane z obecnym HTML Bankier.pl. Pyproject.toml nie ma zależności do scrapowania/parsowania.

## Desired End State

`research.md` w folderze change zawiera: zweryfikowane selektory CSS, wyniki pypdf dla ≥5 PDF-ów, decyzję OCR (HTML fallback), schemat metadanych ogłoszenia. OQ-2 z roadmapy jest zamknięte. `/10x-plan scraper-dedup` i `/10x-plan content-parser` mogą ruszyć bez dodatkowych badań.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Weryfikacja selektorów | Live fetch (httpx) | Daje powtarzalny dowód że selektory są aktualne | Plan |
| OCR fallback gdy PDFy są skanami | HTML fallback (bez pytesseract) | content_parser.py już to implementuje; zero dodatkowych zależności | Plan (user choice) |
| Źródło próbek PDF | Live scrape podczas F-01 | Aktualne dane; bankier.py już umie pobierać listę ogłoszeń | Plan (user choice) |
| Output location | `context/changes/scraper-parser-research/research.md` | Standard 10x-toolkit; automatycznie dostępny dla `/10x-plan` S-01/S-02 | Plan (user choice) |

## Scope

**In scope:**
- Weryfikacja selektorów CSS z bankier.py via live fetch
- Analiza formatu PDF (text-selectable vs scan) dla ≥5 próbek
- Dodanie `httpx`, `beautifulsoup4`, `html5lib`, `pypdf` do pyproject.toml
- Dwa throwaway skrypty eksploracyjne w `scripts/research/`
- research.md z zamknięciem OQ-2

**Out of scope:**
- Produkcyjny scraper (S-01)
- Produkcyjny parser (S-02)
- Implementacja OCR
- Dedup logic, BigQuery

## Architecture / Approach

Dwa standalone skrypty eksploracyjne (nie importują z oldProjectData — unikają zależności konfiguracyjnych):
1. `bankier_html_check.py` — httpx GET → BeautifulSoup → test selektorów → print diagnostics
2. `pdf_sampler.py` — httpx GET lista → find PDF links → download binary → pypdf test → print tabela klasyfikacji

Wyniki obu skryptów przepisywane ręcznie do `research.md`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Add deps & verify HTML | pyproject.toml z 4 deps + potwierdzenie które selektory są aktywne | Selektory mogły się zmienić — fallbacks w bankier.py powinny złapać zmiany |
| 2. PDF analysis | Tabela ≥5 PDF-ów z klasyfikacją TEXT/SCAN/ENCRYPTED | Wysoki % skanów wymusiłby rewizję decyzji OCR |
| 3. Write research.md | Kompletny dokument badawczy + change.md status=researched | Brak — faza jest agregacją wyników |

**Prerequisites:** Python 3.13, uv, dostęp sieciowy do bankier.pl podczas implementacji
**Estimated effort:** ~1 sesja (2-3 godziny)

## Open Risks & Assumptions

- Bankier.pl może blokować automatyczne requesty (rate limiting / User-Agent check) — bankier.py używa custom User-Agent i REQUEST_DELAY, skrypty eksploracyjne powinny to naśladować
- Jeśli > 20% PDF-ów okaże się skanami — decyzja HTML fallback pozostaje ale ryzyko dla S-02 powinno być odnotowane w research.md jako potencjalny scope creep w przyszłości

## Success Criteria (Summary)

- `research.md` zawiera wszystkie 4 deliverables z roadmapy F-01
- OQ-2 (OCR decision) zamknięte
- `/10x-plan scraper-dedup` (S-01) może być uruchomiony bez dodatkowych pytań
