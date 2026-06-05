---
id: content-parser
title: "S-02: Content Parser (PDF + HTML)"
status: implemented
created: 2026-06-06
updated: 2026-06-06
roadmap_id: S-02
tracking:
  linear: PUL-9
  github: 5
---

# S-02: Content Parser (PDF + HTML)

Parser treści tekstowej ogłoszeń ESPI/EBI — wyciąga tekst z `table.seauid2` (primary),
załączników PDF (fallback), lub HTML strony ogłoszenia (last-resort). Wyekstrahowany
tekst i dane spółki (ticker, company) zapisywane do BQ w tabeli `announcements`.

## Prerequisites

- F-01 done — selektory HTML Bankier.pl i format PDF zbadane
- S-01 done — `Announcement` dataclass z `bankier_url` dostępna; BQ tabela istnieje

## Scope

- `db/bigquery.py` — `parsed_content` column, `ensure_schema_current()`, `update_parsed_content()`
- `src/http_client.py` — `download_binary()`
- `src/parser.py` — nowy moduł: `ParsedContent`, `parse_announcement()`
- `main.py` — integracja parsera po scrapowaniu
- `tests/test_parser.py` — unit testy (fixture HTML + mock HTTP)
