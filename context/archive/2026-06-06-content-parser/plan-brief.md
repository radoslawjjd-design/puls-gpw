# S-02: Content Parser — Plan Brief

> Full plan: `context/changes/content-parser/plan.md`
> Research: `context/archive/2026-05-26-scraper-parser-research/research.md`

## What & Why

S-02 dodaje do pipeline'u etap parsowania treści ogłoszeń ESPI/EBI. Dla każdego
nowego komunikatu zwróconego przez scraper (S-01) wchodzi na stronę Bankier.pl,
wyciąga tekst i dane spółki, i zapisuje je do BQ — gotowe do analizy Gemini w S-03.

## Starting Point

S-01 insertuje ogłoszenia do BQ z `ticker=NULL`, `company=NULL` i bez `parsed_content`
(kolumna jeszcze nie istnieje w tabeli). `src/http_client.get()` i `src/exceptions.ParserError`
są gotowe; brak `download_binary()` i całego modułu parsera.

## Desired End State

`python main.py` scrapuje listing, dla każdego nowego ogłoszenia parsuje treść i zapisuje
`parsed_content`, `ticker`, `company` do BQ. Jeden nieudany parse → WARNING + NULL,
pipeline kontynuuje. Tabela BQ ma nową kolumnę `parsed_content STRING NULLABLE`.

## Key Decisions Made

| Decyzja | Wybór | Dlaczego | Source |
|---|---|---|---|
| Persystencja tekstu | Opcja B — `parsed_content` do BQ | Re-runnability Gemini bez re-parsowania; audit trail dla debugowania jakości | Plan |
| Hierarchia ekstrakcji | seauid2 → PDF (max 3, cap 15k) → HTML fallback | seauid2 pokrywa 80% i jest już skondensowany; PDF tylko gdy brak tabeli | Research + Plan |
| Multi-PDF strategy | Cap znaków (15k), nie liczba stron | Spółki mogą wrzucać 100-stronowe PDF-y; cap jest content-based, nie structure-based | Plan |
| Ticker/company | 2-hop w S-02 (strona ogłoszenia + profil) | S-02 i tak fetchuje stronę; naturalne miejsce; S-03 (Gemini) potrzebuje nazwy spółki | Plan |
| Parse failure | Skip + WARNING, `parsed_content=NULL` | Jeden zepsuty komunikat nie blokuje reszty runu | Plan |
| Concurrency | Sekwencyjnie | ~75s mieści się w Cloud Run timeout; brak thread-safety komplikacji | Plan |
| Testy | Fixture HTML + mock HTTP (9 testów) | Spójne z S-01; 3 ścieżki fallback wymagają osobnych case'ów | Plan |

## Scope

**In scope:**
- `db/bigquery.py` — `parsed_content` w `_SCHEMA`, `ensure_schema_current()`, `update_parsed_content()`
- `src/http_client.py` — `download_binary()`
- `src/parser.py` — nowy moduł: `ParsedContent`, `parse_announcement()` + helpery
- `main.py` — `ensure_schema_current()` + pętla parse/update po scrape
- `tests/test_parser.py` — 9 unit testów (wszystkie ścieżki + edge case'y)

**Out of scope:**
- XHTML / XBRL attachments
- OCR (0% skanów w próbce F-01)
- Backfill istniejących wierszy BQ (4 testowe z S-01)
- Równoległe parsowanie
- Persystencja surowych PDF do Cloud Storage

## Architecture / Approach

```
main.py
  └── create_table_if_not_exists()   [BQ — F-02]
  └── ensure_schema_current()        [BQ — S-02, Phase 1]
  └── scrape_new_announcements()     [scraper — S-01]
  └── for each ann:
        insert_announcement()        [BQ — S-01]
        parse_announcement()         [parser — S-02, Phase 2]
          ├── GET bankier_url
          ├── _extract_seauid2()     → if found: done (80%)
          ├── _find_pdf_links()
          │     _extract_pdf_text()  → if found: done (20%)
          ├── _extract_html_fallback() → if found: done (8%)
          └── None (log WARNING)
          + _extract_ticker_company() → ticker, company (best-effort)
        update_parsed_content()      [BQ — S-02, Phase 1]
```

## Phases at a Glance

| Phase | Co dostarcza | Kluczowe ryzyko |
|---|---|---|
| 1. BQ schema extension | `parsed_content` kolumna + migracja + wrapper | BQ `update_table()` może wymagać uprawnień `bigquery.tables.update` — sprawdź ADC |
| 2. HTTP + parser module | `download_binary()` + `src/parser.py` z hierarchią | Selektory Bankier.pl mogą się zmienić — smoke test manualny kluczowy |
| 3. Integration + tests | Pipeline E2E + 9 unit testów | Test ticker 2-hop wymaga podwójnego mock (strona + profil) |

**Prerequisites:** F-01 done (selektory znane), S-01 done (BQ tabela istnieje, `Announcement` dataclass gotowa)
**Estimated effort:** ~2 sesje, 3 fazy
