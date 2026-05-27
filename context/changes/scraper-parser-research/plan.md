# F-01: Bankier.pl HTML & PDF Research — Implementation Plan

## Overview

Zweryfikuj aktualność selektorów CSS z `oldProjectData/bankier.py` i oceń format PDF-ów ESPI/EBI (text-selectable vs skan). Wynik: `research.md` zamykający OQ-2 (OCR) i dokumentujący schemat metadanych — unblockuje S-01 i S-02.

## Current State Analysis

- `oldProjectData/bankier.py` — kompletny scraper Bankier.pl z produkcyjnymi selektorami (ostatnio uruchamiany ~2026-04-23 per commit history). Nie testowany z obecnym HTML Bankier.pl w nowym repo.
- `oldProjectData/content_parser.py` — parser PDF (binary download + pypdf) i HTML fallback (BeautifulSoup). Fast-path KNF: `table.seauid2` dla raportów okresowych.
- `pyproject.toml` — brak zależności potrzebnych do scrapera i parsera: `beautifulsoup4`, `html5lib`, `pypdf`, `httpx`.
- OQ-2 (OCR decision) — unresolved; rozstrzyga ta faza.

## Desired End State

`research.md` zawiera: (1) zweryfikowane selektory CSS + schemat metadanych z Bankier.pl, (2) wyniki analizy ≥5 PDF-ów ESPI/EBI, (3) decyzję OCR (HTML fallback, bez pytesseract), (4) notatki dla S-01 i S-02. Zarówno OQ-2 jak i deliverables roadmapowe F-01 są zamknięte.

### Key Discoveries

- `oldProjectData/bankier.py:66-71` — primary item selector: `.m-quotes-announcements-item`; fallbacks: `.listingItem`, `li[class*='item']`
- `oldProjectData/bankier.py:81-88` — date selectors: `.m-quotes-announcements-item__date, .date, time, [class*='date']`; formats: `%d-%m-%Y %H:%M`, `%d.%m.%Y %H:%M`, `%d-%m-%Y`, `%d.%m.%Y`
- `oldProjectData/bankier.py:129-169` — metadata returned: `title, url (ESPI), bankier_url, company, date, pub_time, source`
- `oldProjectData/bankier.py:244-253` — ticker extraction: `a[href*="profile/quote.html"]` → `?symbol=` param
- `oldProjectData/content_parser.py:307-325` — PDF link discovery: `a[href]` z `.pdf` extension
- `oldProjectData/content_parser.py:328-371` — HTML text extraction: `#emitent, .m-article__body, .article-content, .komunikat-content, article, main`
- `oldProjectData/content_parser.py:29-51` — fast-path KNF: `table.seauid2` dla raportów okresowych (optymalizacja timeout)

## What We're NOT Doing

- Pisanie produkcyjnego kodu scrapera (to S-01)
- Pisanie produkcyjnego parsera PDF/HTML (to S-02)
- Commitowanie skryptów eksploracyjnych do ścieżek produkcyjnych (`src/`, `app/`)
- Implementacja OCR (decyzja: HTML fallback gdy PDF skanem)
- Obsługa edge case'ów — to zadanie S-01/S-02

## Implementation Approach

Trzy fazy: (1) dodaj brakujące zależności do `pyproject.toml` i napisz skrypt weryfikujący selektory CSS live, (2) pobierz 5-10 PDF-ów ESPI/EBI i przetestuj pypdf, (3) skompiluj `research.md` na podstawie output obu skryptów.

Skrypty eksploracyjne trafiają do `scripts/research/` — throwaway (nie production code, ale commitowane żeby zachować powtarzalność).

---

## Phase 1: Add Dependencies & Verify HTML Selectors

### Overview

Dodaj wymagane pakiety do `pyproject.toml` (potrzebne też dla S-01/S-02) i uruchom live fetch Bankier.pl żeby zweryfikować, które selektory z `bankier.py` są aktywne w obecnym HTML.

### Changes Required

#### 1. pyproject.toml — dodaj pipeline dependencies

**File**: `pyproject.toml`

**Intent**: Dodaj pakiety wymagane zarówno do tej fazy badań jak i do produkcyjnego pipeline'u (S-01, S-02). Dodaj je teraz raz, żeby nie wracać.

**Contract**: Do sekcji `[project] dependencies` dodaj: `httpx>=0.27`, `beautifulsoup4>=4.12`, `html5lib>=1.1`, `pypdf>=4.0`. Uruchom `uv sync` po edycji.

#### 2. scripts/research/bankier_html_check.py — weryfikacja selektorów

**File**: `scripts/research/bankier_html_check.py`

**Intent**: Pobierz stronę 1 listy ESPI/EBI Bankier.pl i sprawdź, które selektory z `bankier.py` wciąż dopasowują obecny HTML. Wypisz diagnostykę dla research.md.

**Contract**: Standalone skrypt (bez importów z `oldProjectData/`). Używa `httpx.get()` z nagłówkami:
```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9",
}
```
Przed każdym requestem `time.sleep(0.5)`. Testuje w kolejności:
- Item selectors: `.m-quotes-announcements-item`, `.listingItem`, `li[class*='item']` — wypisz który trafił i ile elementów
- Date selector na pierwszym item: `.m-quotes-announcements-item__date, .date, time, [class*='date']`
- Company selector: `.m-quotes-announcements-item__company, .company, [class*='company'], strong`
- Ticker selector: `a[href*="profile/quote.html"]` → `?symbol=` param
- Link selector: `a[href]` → wypisz URL pierwszego ogłoszenia
- Wypisz: aktywny selektor, liczba elementów, dane pierwszego itemu (title, date_raw, company, ticker)

### Success Criteria

#### Automated Verification

- `uv run scripts/research/bankier_html_check.py` kończy się kodem 0 i wypisuje ≥ 1 element

#### Manual Verification

- Output pokazuje poprawne tytuły ogłoszeń i daty zgodne z tym co widać na bankier.pl
- Primary selector `.m-quotes-announcements-item` trafia (nie fallback) — jeśli nie, odnotuj w research.md który selektor jest aktywny
- Widoczne są: title, data, nazwa spółki, URL ogłoszenia

**Implementation Note**: Po zakończeniu tej fazy zrób ręczną weryfikację i zatwierdź wyniki przed przejściem do Phase 2.

---

## Phase 2: PDF Format Analysis

### Overview

Pobierz 5-10 PDF-ów z aktualnych ogłoszeń ESPI/EBI i przetestuj pypdf. Klasyfikuj każdy PDF: text-selectable, scan (pusta ekstrakcja), encrypted (wyjątek pypdf).

### Changes Required

#### 1. scripts/research/pdf_sampler.py — analiza formatu PDF

**File**: `scripts/research/pdf_sampler.py`

**Intent**: Dla każdego z 5-10 ogłoszeń pobierz PDF (jeśli dostępny), przetestuj `pypdf.PdfReader.extract_text()` i wypisz klasyfikację. Output posłuży bezpośrednio do sekcji research.md.

**Contract**: Standalone skrypt używający `httpx` z nagłówkami:
```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9",
}
```
`time.sleep(0.5)` przed każdym requestem. Pipeline:
1. Fetch strona 1 Bankier.pl → wyciągnij pierwsze 10 URL-i ogłoszeń (selektor `a[href]` w `.m-quotes-announcements-item`)
2. Dla każdego URL ogłoszenia: fetch strona → szukaj `a[href$='.pdf']` (bez BLOCKED_FILENAME_KEYWORDS z content_parser.py: regulamin, cookies, polityka)
3. Download PDF binary (`httpx.get()`)
4. Test: `pypdf.PdfReader(io.BytesIO(data))` → `pages[0].extract_text()`
5. Klasyfikacja: `TEXT` (non-empty, ≥100 znaków), `SCAN` (empty), `ENCRYPTED` (wyjątek), `NO_PDF` (brak linku)
6. Print tabela: `| spółka | URL_pdf | rozmiar_KB | klasyfikacja | sample_tekstu (50 znaków) |`
7. Print podsumowanie: `TEXT: N, SCAN: N, ENCRYPTED: N, NO_PDF: N`

### Success Criteria

#### Automated Verification

- `uv run scripts/research/pdf_sampler.py` kończy się kodem 0 i wypisuje wyniki dla ≥ 5 ogłoszeń

#### Manual Verification

- Sprawdź czy sklasyfikowane jako `TEXT` mają sensowny polski tekst (nie garbage/znaki specjalne)
- Jeśli > 20% to `SCAN` → odnotuj w research.md jako ryzyko dla S-02 i flag do przyszłej oceny OCR
- Odnotuj czy fast-path KNF (`table.seauid2`) byłby potrzebny dla któregoś z próbek

**Implementation Note**: Zrób ręczny przegląd tabeli PDF przed Phase 3. Decyzja OCR jest już podjęta (HTML fallback) — Phase 2 ją tylko potwierdza lub oznacza ryzyko.

---

## Phase 3: Write research.md

### Overview

Skompiluj wyniki Phase 1 i 2 w `research.md`. Zamknij OQ-2. Napisz notatki dla S-01 i S-02.

### Changes Required

#### 1. context/changes/scraper-parser-research/research.md

**File**: `context/changes/scraper-parser-research/research.md`

**Intent**: Główny deliverable F-01. Dokumentuje wszystko co potrzebuje S-01 i S-02 żeby zacząć planowanie. Bez dodatkowych pytań badawczych.

**Contract**: Sekcje (wypełniane na podstawie output Phase 1 i 2):

```
## HTML Structure (Bankier.pl)
- Aktywny selektor listy: <wynik Phase 1>
- Date selector: <wynik>; formaty dat: <wynik>
- Company selector: <wynik>
- Ticker selector: <wynik>
- URL pattern: <wynik>
- Pagination: <jak działa stop condition>
- Dostępne metadane per item: title, bankier_url, espi_url, company, date, pub_time, source

## PDF Format Analysis
| Spółka | PDF URL | Rozmiar | Klasyfikacja | Sample |
| ...    | ...     | ...     | ...          | ...    |
Podsumowanie: TEXT: N/M, SCAN: N/M

## OCR Decision (OQ-2 — RESOLVED)
Decyzja: HTML fallback. Bez pytesseract w MVP.
Uzasadnienie: <% skanów z próbki> + content_parser.py już implementuje HTML fallback.
Ryzyko: <jeśli % skanów > 20 — odnotuj>

## Metadata Schema (per ogłoszenie)
Pola zwracane przez bankier.py:
- title: str
- url: str  (ESPI URL — espi.com.pl lub bankier_url jeśli brak ESPI linku)
- bankier_url: str
- company: str (znormalizowana nazwa przez get_folder_name())
- date: datetime.date
- pub_time: datetime.time | None
- source: "bankier"

## Notes for S-01 (scraper-dedup)
- <obserwacje z Phase 1 — np. rate limiting, CDN stale cache pattern>
- Diagnostyki z bankier.py (diag dict) warto zachować

## Notes for S-02 (content-parser)
- <obserwacje z Phase 2 — np. struktura URLi PDF, ESPI redirect pattern>
- Fast-path KNF (table.seauid2) — zachować z content_parser.py

## Reference Code
- Scraper: oldProjectData/bankier.py (collect_bankier, linie 45-193)
- Parser: oldProjectData/content_parser.py (discover_announcement, fetch_files)
- HTTP client: oldProjectData/base.py (get, download_binary z retry logic)
```

#### 2. context/changes/scraper-parser-research/change.md — update status

**File**: `context/changes/scraper-parser-research/change.md`

**Intent**: Oznacz change jako zakończony research.

**Contract**: Ustaw `status: researched`, `updated: 2026-05-26`.

### Success Criteria

#### Manual Verification

- research.md zawiera wszystkie 4 deliverables z roadmapy F-01:
  - HTML Bankier.pl zmapowany (selektory, paginacja, URL-e)
  - ≥ 5 PDF-ów zbadanych z klasyfikacją
  - OQ-2 (OCR) resolved
  - Schemat metadanych udokumentowany

---

## Testing Strategy

### Manual Testing Steps

1. Uruchom `uv run scripts/research/bankier_html_check.py` — sprawdź aktywne selektory
2. Porównaj z aktualną stroną https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek (DevTools)
3. Uruchom `uv run scripts/research/pdf_sampler.py` — przejrzyj tabelę PDF
4. Dla ≥2 PDF-ów sklasyfikowanych jako TEXT — ręcznie sprawdź jakość extracted text
5. Potwierdź że research.md jest kompletny przed archiwizacją F-01

## References

- Roadmap F-01: `context/foundation/roadmap.md` §F-01
- Change folder: `context/changes/scraper-parser-research/`
- Reference scraper: `oldProjectData/bankier.py`
- Reference parser: `oldProjectData/content_parser.py`
- Reference HTTP client: `oldProjectData/base.py`
- Target URL: https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Add Dependencies & Verify HTML Selectors

#### Automated

- [x] 1.1 `uv run scripts/research/bankier_html_check.py` kończy się kodem 0 i wypisuje ≥ 1 element — de462e3

#### Manual

- [x] 1.2 Output pokazuje poprawne tytuły i daty zgodne z bankier.pl — de462e3
- [x] 1.3 Odnotowany aktywny selektor listy (primary lub fallback) — de462e3
- [x] 1.4 Widoczne są: title, data, company, URL ogłoszenia — de462e3

### Phase 2: PDF Format Analysis

#### Automated

- [x] 2.1 `uv run scripts/research/pdf_sampler.py` kończy się kodem 0 i wypisuje wyniki dla ≥ 5 ogłoszeń — 6ea4f2a

#### Manual

- [x] 2.2 TEXT-classified PDFs mają sensowny polski tekst — 6ea4f2a
- [x] 2.3 OCR decision potwierdzona (lub flagowana jeśli > 20% skanów) — 6ea4f2a
- [x] 2.4 Fast-path KNF (table.seauid2) noted if applicable — 6ea4f2a

### Phase 3: Write research.md

#### Manual

- [x] 3.1 research.md zawiera HTML selektory
- [x] 3.2 research.md zawiera tabelę PDF z ≥ 5 wynikami
- [x] 3.3 OQ-2 (OCR decision) resolved w research.md
- [x] 3.4 Schemat metadanych udokumentowany
- [x] 3.5 change.md status = researched
