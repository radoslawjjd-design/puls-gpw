# BigQuery Fields Audit — Plan Brief

> Full plan: `context/changes/bq-fields-audit/plan.md`

## What & Why

Tabela `announcements` rozrastała się przez 5 slices i ma kilka pól w stanie niezgodnym z aktualną implementacją: martwy kod (`save_analysis()`, `analysis_type`), myląca nazwa timestampa (`processed_at`), parametry funkcji których nikt nie używa (`company`/`ticker` w `insert_announcement`) i odrzucenia analizy niewidoczne w logach. Audit porządkuje semantykę wszystkich 13 nullable pól i usuwa to co jest dead code.

## Starting Point

`db/bigquery.py` ma 17 kolumn i 7 funkcji DML. `save_analysis()` (linie 171-203) nigdy nie jest wywoływana z pipeline'u — jedynym rzeczywistym setterem `analysis_type` jest ta martwa funkcja. `processed_at` jest ustawiane dopiero przez `save_post_text`, nie przez analyzer. `insert_announcement` przyjmuje `company`/`ticker` ale `main.py` zawsze przekazuje `None, None` (scraper ich nie wyciąga z HTML).

## Desired End State

- `save_analysis()` i `analysis_type` usunięte z kodu i z BQ
- `processed_at` → `posted_at`; nowe pole `analyzed_at` stampowane przez `save_analysis_result`
- `insert_announcement` ma tylko 4 parametry (url, published_at, title, priority)
- Odrzucenia analizy widoczne w Cloud Logging jako WARNING z treścią `analysis_reject_reason`
- Docstring w `_SCHEMA` dokumentuje semantykę każdego nullable pola
- 4 nowe testy jednostkowe potwierdzające kontrakt każdego kroku pipeline'u

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| `analysis_type` disposition | Usuń z schematu + usuń `save_analysis()` | Całkowicie martwe — jedyny setter nigdy nie wywoływany | Plan |
| `processed_at` semantics | Rename → `posted_at` + nowe `analyzed_at` | Każdy krok pipeline ma własny timestamp; nazwa `processed_at` myląca | Plan |
| `company`/`ticker` w insert | Usuń z sygnatury | Scraper nie ma tych pól; parser wymaga drugiego HTTP hop | Plan |
| `analysis_reject_reason` | Log WARNING w Cloud Logging | Debugging bez BQ query; zero zmian w emailach | Plan |
| `supervisor_attempts` | Zostaw nazwę, dokumentuj | Koszt BQ rename > zysk; semantyka jasna po dodaniu docstringa | Plan |

## Scope

**In scope:**
- Usunięcie `save_analysis()` i `analysis_type` z kodu i BQ
- Rename `processed_at` → `posted_at` + nowe pole `analyzed_at`
- Uproszczenie sygnatury `insert_announcement`
- WARNING log przy odrzuceniu analizy
- Semantyka nullable pól w docstringu
- 4 testy jednostkowe

**Out of scope:**
- Wyciąganie `company`/`ticker` ze scrapera
- Śledzenie prób analizatora w BQ
- Rename `supervisor_attempts`
- Surfacing `analysis_reject_reason` w emailach

## Architecture / Approach

Zmiany tylko w `db/bigquery.py`, `main.py`, `scripts/test_bq.py`, `tests/test_bigquery.py`. BQ schema migration jest addytywna przez `ensure_schema_current()` (auto przy starcie) + dwa ręczne DROP COLUMN. Żadnych nowych modułów.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Code cleanup | Martwy kod usunięty, nowe timestampy, log, testy | Żaden — pure Python, w pełni testowalne bez BQ |
| 2. BQ migration (human) | Schema BQ zsynchronizowana z kodem | Kolejność: deploy code first, potem DROP |
| 3. Tests | 4 testy kontrakt per krok pipeline | — |

**Prerequisites:** Dostęp do BQ console dla Fazy 2.
**Estimated effort:** ~1 sesja (Fazy 1+3 razem), Faza 2 to 5 minut w BQ console po deployu.

## Open Risks & Assumptions

- BQ `DROP COLUMN` na `processed_at` traci historyczne dane jeśli migracja (`UPDATE SET posted_at = processed_at`) nie zostanie wykonana przed dropem — plan dokumentuje kolejność

## Success Criteria (Summary)

- `grep -rn "processed_at\|analysis_type\|save_analysis\b" . --include="*.py"` — brak wyników
- `bq show --schema` zawiera `posted_at`, `analyzed_at`; nie zawiera `processed_at`, `analysis_type`
- `uv run pytest` — pełny suite przechodzi bez regresji
