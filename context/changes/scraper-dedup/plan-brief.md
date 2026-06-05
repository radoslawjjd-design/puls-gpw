# S-01 Scraper Bankier.pl + Dedup BigQuery — Plan Brief

> Full plan: `context/changes/scraper-dedup/plan.md`
> Research: `context/archive/2026-05-26-scraper-parser-research/research.md`

## What & Why

Implementujemy S-01 — scraper listingu Bankier.pl, który w oknie 15 minut pobiera nowe ogłoszenia ESPI/EBI, deduplikuje je via BigQuery i insertuje nowe wiersze. To pierwszy „mięśniowy" krok pipeline'u — bez niego S-02, S-03, S-04 nie mają na czym pracować.

## Starting Point

`main.py` to stub: inicjalizuje tabelę BQ, loguje `"Pipeline started"` i kończy. F-02 dostarczyło `insert_announcement()` i `is_processed()`. F-03 dostarczyło `send_alert()` i `ScraperError`. HTML selektory Bankier.pl są znane z F-01.

## Desired End State

`python main.py` scrapuje listing, deduplikuje via batch BQ query, insertuje nowe ogłoszenia z `ticker=NULL` / `company=NULL` (uzupełni S-02) i loguje ile znalazł. Zero nowych = INFO + exit 0. Błąd HTTP po 3 retry lub błąd BQ = `ScraperError` → `send_alert()` → exit 1.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Ticker/company w S-01 | Skip (NULL) | S-02 i tak fetchuje stronę ogłoszenia — 1 request zamiast 2 | Plan |
| Dedup strategy | Batch prefetch `get_processed_ids_since()` | 1 BQ query zamiast ~20 per-item queries | Plan |
| Timezone | Europe/Warsaw (zoneinfo stdlib) | Daty na Bankier.pl są w CET/CEST | Plan |
| HTTP fail | Retry 3× + ScraperError → send_alert | Spójne z F-03 NFR "cicha awaria niedopuszczalna" | Plan |
| HTTP library | httpx sync (już w deps) | requests nie jest w deps; httpx wystarczy | Plan |
| Max pages | 5 (env var) | Zabezpieczenie przed nieskończoną pętlą | Plan |
| Empty run | INFO log, exit 0 | Normalna sytuacja poza sesją giełdową | Plan |
| BQ insert fail | Fail fast → send_alert | Spójne z F-03; dedup chroni przed duplikatami przy retry | Plan |
| Testy | Unit z respx + mock BQ | Szybkie, deterministyczne, bez GCP | Plan |

## Scope

**In scope:**
- `src/http_client.py` — httpx GET z retry + rate limit
- `src/scraper.py` — `Announcement` dataclass + `scrape_new_announcements()`
- `db/bigquery.py` — `announcement_id_for_url()` (public) + `get_processed_ids_since()`
- `main.py` — integracja (stub → scrape + insert loop)
- `tests/test_scraper.py` — 6 unit testów z respx
- `pyproject.toml` — pytest + respx w dev deps

**Out of scope:**
- Ticker i company lookup (2-hop HTTP) → S-02
- Przetwarzanie treści ogłoszenia (PDF/HTML) → S-02
- Reaktywacja Cloud Schedulera → S-04
- FastAPI endpoint

## Architecture / Approach

```
main.py
  └── scrape_new_announcements()       [src/scraper.py]
        ├── get_processed_ids_since()  [db/bigquery.py]  ← 1 BQ query
        └── get(url)                   [src/http_client.py] ← httpx + retry
              └── BeautifulSoup parse
  └── insert_announcement() × N       [db/bigquery.py]
```

Scraper zwraca `list[Announcement]` z `ticker=None`, `company=None`. main.py insertuje każdy announcement sekwencyjnie — fail-fast przy błędzie BQ.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BQ helpers + dev deps | `get_processed_ids_since()` + pytest/respx w deps | Zmiana sygnatury `_announcement_id` może złamać F-02 kod |
| 2. HTTP client + scraper | `src/http_client.py` + `src/scraper.py` działają | Zmiana HTML Bankier.pl po F-01 research |
| 3. main.py + testy | Pełny E2E pipeline + 6 unit testów | Dedup timing edge case przy DST |

**Prerequisites:** F-01, F-02, F-03 archived (done). `.env` z `GOOGLE_CLOUD_PROJECT=puls-gpw`.
**Estimated effort:** ~1 sesja, 3 fazy.

## Open Risks & Assumptions

- Selektory HTML Bankier.pl mogą się zmienić od daty F-01 research (2026-05-27) — jeśli scraper zwróci 0 items, sprawdź manualnie HTML
- Items na stronie są interleaved (nie chronologiczne) — stop-condition oparty na `min_dt` strony, nie na pierwszym item starszym niż cutoff
- BQ tabela nie ma partycji — przy dużej historii `get_processed_ids_since()` będzie full scan; akceptowalne na MVP

## Success Criteria (Summary)

- `uv run pytest tests/ -v` — 6 testów przechodzi
- `python main.py` insertuje nowe ogłoszenia, drugi run w ciągu minuty loguje `0 new announcements`
- BQ Console pokazuje wiersze z `published_at` w ostatnich 15 min i `ticker=NULL`
