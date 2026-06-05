# S-02: Content Parser (PDF + HTML) — Implementation Plan

## Overview

Implementacja S-02: parser treści tekstowej ogłoszeń ESPI/EBI. Dla każdego nowego
ogłoszenia zwróconego przez S-01 scraper wchodzi na stronę Bankier.pl i wyciąga tekst
metodą hierarchiczną: `table.seauid2` (primary, 80% przypadków) → PDF-y (max 3 pliki,
cap 15 000 znaków) → `section.o-article-content` blok 2 (last-resort). Przy okazji
fetcha strony ogłoszenia wyciąga też ticker i company via 2-hop (strona ogłoszenia →
profil spółki). Wszystkie pola zapisywane do BQ via `UPDATE` na istniejącym wierszu.

## Current State Analysis

- `db/bigquery.py` — schemat `_SCHEMA` nie ma kolumny `parsed_content`; `insert_announcement()`
  nie przekazuje `parsed_content`; brak wrappera do zapisu wyekstrahowanego tekstu
- `src/http_client.py` — ma `get()` z retry; brak `download_binary()` dla binarek PDF
- `src/parser.py` — nie istnieje
- `src/exceptions.py` — `ParserError(PipelineStageError)` już zdefiniowany — nie tworzyć nowego
- `main.py` — pipeline wywołuje `scrape_new_announcements()` + `insert_announcement()`;
  brak wywołania parsera
- `oldProjectData/content_parser.py` — referencyjna implementacja (inna architektura:
  2-etapowa discover+fetch, XHTML/XBRL scope, fast-path tylko dla raportów finansowych);
  użyj jako referencję dla selektorów i logiki, NIE kopiuj 1:1

## Desired End State

Po wdrożeniu:
- `python main.py` scrapuje listing → parsuje każde nowe ogłoszenie → zapisuje
  `parsed_content`, `ticker`, `company` do BQ; kończy z `exit 0`
- BQ: wiersz ogłoszenia ma `parsed_content` (tekst lub NULL gdy parsowanie niemożliwe),
  `ticker` i `company` uzupełnione gdy dostępne na Bankier.pl
- Parsowanie nie blokuje pipeline'u: jeden nieudany komunikat → WARNING + NULL,
  pozostałe przetwarzane normalnie
- `uv run pytest tests/ -v` — wszystkie testy przechodzą (S-01 + nowe S-02)

### Key Discoveries

- `ParserError` w `src/exceptions.py:20` — gotowy, nie tworzyć nowego
- `_SCHEMA` w `db/bigquery.py:17` — brak `parsed_content`; `ensure_schema_current()`
  musi zrobić BQ schema update na istniejącej tabeli (nie drop/recreate)
- `insert_announcement()` w `db/bigquery.py:95` — jawnie wymienia kolumny w INSERT,
  nowa kolumna `parsed_content` nie wymaga zmiany tej funkcji (BQ zapisze NULL domyślnie)
- `src/http_client.get()` zwraca `httpx.Response` (raises `ScraperError` po retry);
  `download_binary()` musi zwracać `bytes | None` (None = nie rzuca wyjątku, tylko WARNING)
- Ticker 2-hop (z F-01 research): strona ogłoszenia → `a[href*="profile/quote.html"]`
  → URL z symbolem ESPI → strona profilu → `span.a-heading__suffix.-blue.-with-dot`
  → tekst w nawiasach = ticker giełdowy. Symbol w URL (`SFKPOLKAP`) ≠ ticker (`SFK`)
- `BLOCKED_FILENAME_KEYWORDS` z `oldProjectData/content_parser.py:22`:
  `regulamin, polityka_prywatnosci, polityka_plikow, cookies, privacy_policy, terms_of_service`
- `PARSED_CONTENT_MAX_CHARS` env var (default `"15000"`) — cap łączny dla wszystkich
  PDFów; pozwala na tuning bez zmiany kodu przed S-03

## What We're NOT Doing

- XHTML / XBRL attachments — poza zakresem MVP (tylko PDF i HTML)
- OCR (pytesseract) — 0% skanów w próbce F-01; gdy pypdf zwraca pusty tekst → fallback do HTML
- Równoległe parsowanie — sekwencyjnie; przy 30–50 ogłoszeniach ~75s mieści się w timeout
- Backfill istniejących wierszy BQ (4 testowe z S-01) — zostają z `parsed_content=NULL`
- Persystencja surowych binarek PDF do Cloud Storage — tylko wyekstrahowany tekst do BQ
- FastAPI endpoint — pipeline to Cloud Run Job

## Implementation Approach

Trzy fazy:
1. **BQ** — rozszerzenie schematu (`parsed_content`), migracja istniejącej tabeli,
   nowe wrappery (`ensure_schema_current`, `update_parsed_content`)
2. **HTTP + parser** — `download_binary()` w `src/http_client.py` +
   nowy moduł `src/parser.py` z hierarchią seauid2 → PDF → HTML + ticker 2-hop
3. **Integracja + testy** — wpięcie parsera w `main.py` + `tests/test_parser.py`

## Critical Implementation Details

**BQ schema migration**: `create_table_if_not_exists()` nie aktualizuje schematu istniejącej
tabeli. `ensure_schema_current()` musi zrobić `client.get_table()` → sprawdzić czy
`parsed_content` jest w `table.schema` → jeśli nie: `table.schema = table.schema + [new_field]`;
`client.update_table(table, ["schema"])`. Wywoływana z `main.py` obok
`create_table_if_not_exists()`.

**Ticker 2-hop jest opcjonalny**: jeśli strona ogłoszenia nie ma linku do profilu spółki
(np. niektóre EBI), `ticker` i `company` zostają NULL. Nie rzucaj wyjątku — tylko
`logger.debug()`. Koszt: +1 HTTP request per ogłoszenie (tylko gdy link profilu znaleziony).

**seauid2 jako wystarczający source**: gdy `table.seauid2` ma treść → zapisz do
`parsed_content` i pomiń pobieranie PDF-ów. Nie ma sensu fetch PDF-ów gdy seauid2
już zawiera skondensowane dane finansowe (analogicznie do fast-path w oldProjectData,
ale jako zasada ogólna, nie tylko dla raportów finansowych).

**pypdf import**: `pypdf` jest już zainstalowane jako zależność (sprawdź `pyproject.toml`);
jeśli nie — dodać do `[project] dependencies`. Użyj `pypdf.PdfReader(io.BytesIO(data))`
(nie `PyPDF2` — deprecated).

---

## Phase 1: BQ schema extension

### Overview

Dodaje kolumnę `parsed_content` do schematu BQ (w kodzie i w istniejącej tabeli
produkcyjnej). Nowe wrappery: `ensure_schema_current()` (migracja) i
`update_parsed_content()` (UPDATE wiersza po parsowaniu).

### Changes Required

#### 1. `db/bigquery.py` — `parsed_content` w `_SCHEMA`

**File**: `db/bigquery.py`

**Intent**: Dodać `parsed_content STRING NULLABLE` do `_SCHEMA` — tak żeby nowo tworzone
tabele (np. na środowiskach deweloperskich) miały kolumnę od razu.

**Contract**: Wstawić jako ostatni element listy `_SCHEMA`:
```python
bigquery.SchemaField("parsed_content", "STRING", mode="NULLABLE"),
```

#### 2. `db/bigquery.py` — `ensure_schema_current()`

**File**: `db/bigquery.py`

**Intent**: Zmigrować istniejącą tabelę BQ (production) dołączając `parsed_content`
jeśli kolumna jeszcze nie istnieje. Idempotentna — bezpieczna przy wielokrotnym wywołaniu.

**Contract**:
```python
def ensure_schema_current() -> None:
    """Add any missing columns from _SCHEMA to the existing BQ table.

    Safe to call on every startup — no-op if schema is already current.
    Raises BigQueryError if the schema update fails.
    """
```

Implementacja: `client.get_table(table_id)` → zbuduj `existing_names = {f.name for f in table.schema}` →
znajdź brakujące pola z `_SCHEMA` → jeśli są: `table.schema = table.schema + missing_fields`;
`client.update_table(table, ["schema"])` → loguj jakie kolumny dodano.

#### 3. `db/bigquery.py` — `update_parsed_content()`

**File**: `db/bigquery.py`

**Intent**: UPDATE istniejącego wiersza w BQ ustawiając `parsed_content`, `ticker` i
`company` po parsowaniu ogłoszenia. Odpowiednik `save_analysis()` dla etapu S-02.

**Contract**:
```python
def update_parsed_content(
    announcement_id: str,
    parsed_content: str | None,
    ticker: str | None,
    company: str | None,
) -> None:
    """Update parsed_content, ticker, company for an existing announcement row.

    parsed_content=None is valid (parse failed gracefully).
    Raises BigQueryError if the UPDATE fails or matches 0 rows.
    """
```

UPDATE query analogiczny do `save_analysis()` — parametryzowany, sprawdza
`num_dml_affected_rows == 0` i rzuca `BigQueryError`.

### Success Criteria

#### Automated Verification

- `python -c "from db.bigquery import ensure_schema_current, update_parsed_content"` — brak ImportError
- `uv run python -c "from db.bigquery import _SCHEMA; names = [f.name for f in _SCHEMA]; assert 'parsed_content' in names"` — assertion passes

#### Manual Verification

- `ensure_schema_current()` wywołana na istniejącej tabeli BQ — log
  `"BQ schema updated: added columns ['parsed_content']"` lub
  `"BQ schema already current"` (gdy kolumna już istnieje)
- BQ Console: tabela `espi_ebi.announcements` ma kolumnę `parsed_content STRING NULLABLE`

**Implementation Note**: Po tej fazie zatrzymaj się i potwierdź manualnie że kolumna
pojawiła się w BQ Console przed przejściem do Phase 2.

---

## Phase 2: HTTP client extension + parser module

### Overview

Dwa zmiany: `download_binary()` w `src/http_client.py` oraz nowy moduł `src/parser.py`
implementujący hierarchię ekstrakcji treści i ticker 2-hop.

### Changes Required

#### 1. `src/http_client.py` — `download_binary()`

**File**: `src/http_client.py`

**Intent**: Dodać funkcję do pobierania binarek (PDF). Analogiczna do `get()` ale zwraca
`bytes | None` zamiast `httpx.Response` — None gdy wszystkie retry wyczerpane
(nie rzuca wyjątku, bo brak jednego PDF-u nie powinien blokować parsowania).

**Contract**:
```python
def download_binary(url: str) -> bytes | None:
    """Download binary content (e.g. PDF) with retry.

    Returns None (with WARNING log) instead of raising on exhausted retries —
    a missing attachment should not abort the whole parsing run.
    """
```

Używa tego samego `_get_http_client()`, `_MAX_RETRIES`, `_REQUEST_DELAY` co `get()`.
Zwraca `response.content` (bytes). Przy błędzie po retry: `logger.warning(...)`, return None.

#### 2. `pyproject.toml` — `pypdf` w dependencies (jeśli brak)

**File**: `pyproject.toml`

**Intent**: Upewnić się że `pypdf>=4.0` jest w `[project] dependencies` — potrzebny do
ekstrakcji tekstu z PDF. Sprawdź najpierw czy już jest; dodaj tylko jeśli brak.

#### 3. `src/parser.py` — nowy moduł parsera

**File**: `src/parser.py` (nowy plik)

**Intent**: Zaimplementować pełny pipeline ekstrakcji treści dla jednego ogłoszenia:
1. Fetch strony ogłoszenia (httpx GET)
2. Ekstrakcja seauid2 (primary) → jeśli znaleziony: done
3. Ekstrakcja linków PDF → pobierz binarne → wyciągnij tekst pypdf (max 3 pliki, cap 15k znaków)
4. Fallback HTML (section.o-article-content, 2. blok `<br>`)
5. Ticker 2-hop: `a[href*="profile/quote.html"]` → fetch profilu → `span.a-heading__suffix.-blue.-with-dot`

**Contract**:
```python
@dataclass
class ParsedContent:
    announcement_id: str
    parsed_content: str | None   # None = nie udało się wyciągnąć treści
    ticker: str | None           # None = brak linku do profilu lub fetch profilu nieudany
    company: str | None          # None = analogicznie jak ticker

def parse_announcement(ann: Announcement, announcement_id: str) -> ParsedContent:
    """Fetch announcement page and extract content + ticker/company.

    Never raises — all failures are logged as WARNING and return None fields.
    """
```

Stałe konfiguracyjne jako module-level z `os.environ.get`:
- `_MAX_PDFS = int(os.environ.get("PARSER_MAX_PDFS", "3"))`
- `_MAX_CHARS = int(os.environ.get("PARSED_CONTENT_MAX_CHARS", "15000"))`

Wewnętrzne helpery (prywatne):
- `_extract_seauid2(soup) -> str | None` — `soup.find("table", class_=lambda c: "seauid2" in ...)`;
  `get_text(" | ", strip=True)`; zwróć None jeśli pusty lub < 100 znaków
- `_find_pdf_links(soup, base_url) -> list[str]` — `soup.find_all("a", href=True)`;
  filtr `.pdf` extension + `BLOCKED_FILENAME_KEYWORDS`; max `_MAX_PDFS` linków;
  `urljoin` dla relatywnych URL
- `_extract_pdf_text(pdf_bytes: bytes) -> str` — wrappuje całość w `try/except Exception`:
  wyjątek pypdf (PdfReadError, ValueError, itp.) loguje WARNING i zwraca `""` — nigdy
  nie propaguje poza tę funkcję. Wewnątrz: `pypdf.PdfReader(io.BytesIO(pdf_bytes))`;
  pętla po `reader.pages`; `page.extract_text() or ""`; przerwij gdy łączny tekst >= `_MAX_CHARS`
- `_extract_html_fallback(soup) -> str | None` — `soup.select_one("section.o-article-content")`;
  `<br>` to element void (brak treści) — nie używaj `find_all("br").get_text()`.
  Podejście: wyciągnij dzieci sekcji jako tekst z separatorem znacznikowym per `<br>`:
  ```python
  section = soup.select_one("section.o-article-content")
  if not section: return None
  # Zamień każdy <br> na unikalny separator, potem podziel
  for br in section.find_all("br"):
      br.replace_with("§BR§")
  segments = [s.strip() for s in section.get_text().split("§BR§") if s.strip()]
  # Pierwszy segment = AI summary Bankier, drugi = treść właściwa
  text = segments[1] if len(segments) >= 2 else None
  return text if text and len(text) >= 50 else None
  ```
- `_extract_ticker_company(soup, base_url) -> tuple[str | None, str | None]` — znajdź
  `a[href*="profile/quote.html"]`; jeśli brak: return (None, None); fetch profilu via `get()`;
  parsuj `span.a-heading__suffix.-blue.-with-dot`; ticker = tekst w nawiasach `\(([^)]+)\)`;
  company = tekst przed nawiasami

Logika `parse_announcement()`:
```
1. try:
       resp = get(ann.bankier_url)
   except ScraperError:
       logger.warning("parse_announcement: HTTP failed for %s", ann.bankier_url)
       return ParsedContent(announcement_id, None, None, None)
2. soup = BeautifulSoup(resp.text, "html5lib")
3. ticker, company = _extract_ticker_company(soup, ann.bankier_url)
4. text = _extract_seauid2(soup)
   if text:
       logger.info("Parser: seauid2 for %s", ann.bankier_url)
       return ParsedContent(parsed_content=text[:_MAX_CHARS], ticker=ticker, company=company)
5. pdf_links = _find_pdf_links(soup, ann.bankier_url)
   if pdf_links:
       all_text = ""
       for url in pdf_links:
           data = download_binary(url)
           if data: all_text += _extract_pdf_text(data)
           if len(all_text) >= _MAX_CHARS: break
       if all_text.strip():
           logger.info("Parser: pdf for %s", ann.bankier_url)
           return ParsedContent(parsed_content=all_text[:_MAX_CHARS], ticker=ticker, company=company)
6. text = _extract_html_fallback(soup)
   if text:
       logger.info("Parser: html for %s", ann.bankier_url)
       return ParsedContent(parsed_content=text[:_MAX_CHARS], ticker=ticker, company=company)
7. logger.warning("Parser: none for %s", ann.bankier_url)
   return ParsedContent(parsed_content=None, ticker=ticker, company=company)
```

### Success Criteria

#### Automated Verification

- `python -c "from src.parser import parse_announcement, ParsedContent"` — brak ImportError
- `python -c "from src.http_client import download_binary"` — brak ImportError
- `uv sync` kończy bez błędów (pypdf zainstalowany)

#### Manual Verification

- Ręczny smoke test na jednym URL:
  ```python
  from src.parser import parse_announcement
  from src.scraper import Announcement
  import datetime
  from zoneinfo import ZoneInfo
  ann = Announcement(title="TEST", espi_code="TEST", bankier_url="<url_z_BQ>",
                     published_at=datetime.datetime.now(ZoneInfo("Europe/Warsaw")), source="espi")
  result = parse_announcement(ann, "test-id")
  print(result)
  ```
- Wynik ma `parsed_content` (string, nie None) dla ogłoszenia z `table.seauid2`
- Log `"Scraper: ..."` i `"Parser: seauid2|pdf|html|none for <url>"` widoczne w stdout

**Implementation Note**: Przetestuj ręcznie wszystkie 3 ścieżki (seauid2, PDF, HTML)
zanim przejdziesz do Phase 3. Wybierz URL-e z BQ Console dla różnych typów ogłoszeń.

---

## Phase 3: main.py integration + unit tests

### Overview

Wplata parsera w pipeline `main.py` (po `insert_announcement()` → `parse_announcement()`
→ `update_parsed_content()`). Dodaje `tests/test_parser.py` pokrywający każdą ścieżkę
fallback + edge case'y.

### Changes Required

#### 1. `main.py` — integracja parsera

**File**: `main.py`

**Intent**: Rozszerzyć pipeline S-01 o etap parsowania. Po wstawieniu nowych ogłoszeń
do BQ, dla każdego z nich: sparsuj treść i zaktualizuj wiersz BQ.

**Contract**: Sekwencja w `main()`:
1. `create_table_if_not_exists()`
2. `ensure_schema_current()` ← nowe
3. `new = scrape_new_announcements()`
4. `if not new: return`
5. For each `ann` in `new`:
   - `ann_id = insert_announcement(ann.bankier_url, ann.published_at, ann.title, None, None)`
   - `parsed = parse_announcement(ann, ann_id)` ← nowe
   - `update_parsed_content(ann_id, parsed.parsed_content, parsed.ticker, parsed.company)` ← nowe
6. `logger.info("Pipeline completed: %d announcements scraped and parsed", len(new))`

`parse_announcement()` nigdy nie rzuca — błędy parsowania to WARNING + NULL w BQ.
`update_parsed_content()` może rzucić `BigQueryError` → propaguje do outer `except`
→ `send_alert()`.

#### 2. `tests/test_parser.py` — unit testy

**File**: `tests/test_parser.py` (nowy)

**Intent**: Pokryć każdą ścieżkę ekstrakcji treści i edge case'y używając fixture HTML
i mocków httpx/pypdf. Analogiczna struktura do `tests/test_scraper.py`.

**Test cases**:

| Test | Co weryfikuje | Mock |
|---|---|---|
| `test_seauid2_path` | Gdy `table.seauid2` ma treść → `parsed_content` = tekst seauid2; PDFy nie fetchowane | respx mock announcement page; brak mock PDF |
| `test_pdf_path_no_seauid2` | Brak seauid2, jest PDF link → `parsed_content` = tekst z PDF | respx mock page + PDF binary; pypdf mock |
| `test_html_fallback_path` | Brak seauid2, brak PDF → `parsed_content` z 2. bloku `<br>` | respx mock page |
| `test_all_paths_fail` | Brak seauid2, brak PDF, brak fallback HTML → `parsed_content=None`, brak wyjątku | respx mock page (empty) |
| `test_pdf_char_cap` | Suma tekstu z PDFów przekracza `_MAX_CHARS` → przycięte do `_MAX_CHARS` | respx + pypdf mock z długim tekstem |
| `test_max_pdfs_limit` | Strona z 5 linkami PDF → tylko pierwsze `_MAX_PDFS` (3) fetchowane | respx — weryfikuj liczbę wywołań download_binary |
| `test_ticker_company_extracted` | Link profilu znaleziony → ticker i company poprawnie wyciągnięte | respx mock announcement + profil page |
| `test_ticker_missing_gracefully` | Brak linku profilu → ticker=None, company=None, brak wyjątku | respx mock page bez linku profilu |
| `test_blocked_pdf_filtered` | Link PDF z `regulamin` w nazwie → pominięty | respx mock page |

Fixture HTML tworzyć inline w testach (`html_content = "..."`) lub jako pliki
`tests/fixtures/parser_seauid2.html`, `tests/fixtures/parser_pdf_only.html` itd.
Pypdf mockuj przez `unittest.mock.patch("src.parser.pypdf.PdfReader")`.

#### 3. `tests/fixtures/` — fixture pliki dla parsera (opcjonalnie)

**File**: `tests/fixtures/parser_seauid2.html`, `tests/fixtures/parser_pdf_only.html`,
`tests/fixtures/parser_html_fallback.html` (nowe)

**Intent**: Minimalne HTML fixtures dla każdej ścieżki parsera — jeśli inline strings
byłyby zbyt długie. Analogicznie do `tests/fixtures/sample_listing_page1.html` z S-01.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ -v` — wszystkie testy S-01 (6) + S-02 (9) przechodzą
- `uv run pytest tests/ --tb=short -q` — 0 failures, 0 errors
- `python -m py_compile src/parser.py main.py` — brak błędów składni

#### Manual Verification

- `python main.py` — kończy bez wyjątku; log `"Pipeline completed: N announcements scraped and parsed"`
- BQ Console: co najmniej jeden wiersz z `parsed_content` ≠ NULL (jeśli są ogłoszenia w oknie 15 min)
- BQ Console: wiersz z `ticker` i `company` uzupełnione (gdy ogłoszenie ma link profilu)
- `python main.py` ponownie w ciągu minuty — log `"0 new announcements"` (dedup działa)
- Cloud Logging: structured JSON logi widoczne, w tym WARNING per nieudany parse

**Implementation Note**: Uruchom pipeline w godzinach sesji giełdowej (9:00–17:05 w dni robocze)
żeby mieć ogłoszenia do sparsowania. Zweryfikuj w BQ Console wszystkie 3 ścieżki jeśli
możliwe.

---

## Testing Strategy

### Unit Tests

- seauid2 path: fixture z `table.seauid2` → poprawny tekst, brak fetch PDF
- PDF path: fixture bez seauid2, z linkiem PDF → tekst z pypdf
- HTML fallback: fixture bez seauid2, bez PDF → tekst z 2. `<br>` bloku
- All-fail: pusty fixture → `parsed_content=None`, brak wyjątku
- Char cap: mock pypdf z długim tekstem → przycięte do `_MAX_CHARS`
- Max PDFs: 5 linków PDF w fixture → tylko 3 fetchowane
- Ticker 2-hop: fixture z linkiem profilu → poprawny ticker i company
- Ticker missing: fixture bez linku profilu → None, None, brak wyjątku
- Blocked PDF: link z `regulamin` → pominięty

### Integration / Manual Testing

1. `python main.py` w godzinach sesji — weryfikacja E2E z prawdziwym Bankier.pl + BQ
2. BQ Console: `SELECT announcement_id, ticker, company, LEFT(parsed_content, 200) FROM espi_ebi.announcements ORDER BY processed_at DESC LIMIT 10`
3. Weryfikacja każdej ścieżki (seauid2, PDF, HTML) na prawdziwych ogłoszeniach

### Manual Testing Steps

1. Ustaw `.env`: `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`
2. (Opcjonalnie) `PARSED_CONTENT_MAX_CHARS=5000` żeby szybciej sprawdzić cap
3. `python main.py` — obserwuj logi
4. BQ Console: sprawdź `parsed_content`, `ticker`, `company` w nowych wierszach

## Performance Considerations

- 30–50 ogłoszeń × ~2–3 requesty (strona + ewentualnie profil + ewentualnie PDF)
  × 0.5s rate limit ≈ 30–75s per run — akceptowalne w ramach Cloud Run Job timeout
- seauid2 path (80% przypadków) = 1 request per ogłoszenie (strona) + opcjonalnie 1 (profil)
- PDF path: max 3 × pobieranie binarki — download_binary ma retry z backoff, timeout 30s

## Migration Notes

`ensure_schema_current()` migruje istniejącą tabelę `espi_ebi.announcements` dodając
kolumnę `parsed_content STRING NULLABLE`. Istniejące wiersze (4 testowe z S-01) dostaną
`parsed_content=NULL` — to oczekiwany stan, nie błąd.

## References

- Research HTML selektory i PDF format: `context/archive/2026-05-26-scraper-parser-research/research.md`
- Referencyjna implementacja parsera: `oldProjectData/content_parser.py`
- BQ client wzorzec: `db/bigquery.py`
- HTTP client wzorzec: `src/http_client.py`
- Exception hierarchy: `src/exceptions.py`
- S-01 unit testy (wzorzec): `tests/test_scraper.py`

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: BQ schema extension

#### Automated

- [x] 1.1 `python -c "from db.bigquery import ensure_schema_current, update_parsed_content"` — brak ImportError
- [x] 1.2 `parsed_content` obecny w `_SCHEMA` — assertion passes

#### Manual

- [x] 1.3 `ensure_schema_current()` dodaje kolumnę do istniejącej tabeli BQ — log potwierdzający
- [x] 1.4 BQ Console: kolumna `parsed_content STRING NULLABLE` widoczna

### Phase 2: HTTP client + parser module

#### Automated

- [ ] 2.1 `python -c "from src.http_client import download_binary"` — brak ImportError
- [ ] 2.2 `python -c "from src.parser import parse_announcement, ParsedContent"` — brak ImportError
- [ ] 2.3 `uv sync` kończy bez błędów (pypdf zainstalowany)

#### Manual

- [ ] 2.4 Smoke test `parse_announcement()` na URL z seauid2 — zwraca tekst
- [ ] 2.5 Smoke test na URL bez seauid2 (EBI raport roczny) — zwraca tekst z PDF lub fallback
- [ ] 2.6 Log `"Parser: seauid2|pdf|html|none for <url>"` widoczny w stdout

### Phase 3: main.py integration + unit tests

#### Automated

- [ ] 3.1 `uv run pytest tests/ -v` — wszystkie 15 testów (6 S-01 + 9 S-02) przechodzi
- [ ] 3.2 `uv run pytest tests/ --tb=short -q` — 0 failures, 0 errors
- [ ] 3.3 `python -m py_compile src/parser.py main.py` — brak błędów składni

#### Manual

- [ ] 3.4 `python main.py` kończy bez wyjątku; log `"Pipeline completed: N announcements scraped and parsed"`
- [ ] 3.5 BQ Console: wiersze z `parsed_content` ≠ NULL i `ticker` uzupełnionym
- [ ] 3.6 Drugi run w ciągu minuty → `"0 new announcements"` (dedup działa)
- [ ] 3.7 Cloud Logging: structured JSON logi widoczne
