---
project: ESPI/EBI Analyzer
version: 2
status: active
created: 2026-06-08
updated: 2026-08-10
---

# Test Plan: ESPI/EBI Analyzer

> Coverage of the automated test suite — risks, mapping, and strategy.
> All tests live in `tests/`. Run with `uv run pytest`.

## Testing strategy

**Approach: two layers, both with mocked boundaries.**

**Unit** — 982 tests across 43 files in `tests/`. All external services (BigQuery, Bankier.pl HTTP, Gemini API) are mocked via `unittest.mock.patch`.

**End-to-end** — 196 tests across 30 files in `tests/e2e/`, driving the real frontend in headless Chromium via Playwright against a live app instance (`live_server_url` fixture). BigQuery is stubbed at the `db.bigquery.*` boundary in `tests/e2e/conftest.py`, so e2e exercises the true request → API → template → DOM path without touching a real dataset. This is the only automated coverage the frontend has: `static/index.html` is a single 6326-line file with no unit-testable module structure.

Both layers are fast, deterministic, and need no credentials — the whole suite (**1178 tests**) runs in about two minutes.

**Not covered by either layer:** anything whose behaviour lives in BigQuery's own SQL engine. A mocked query returns whatever the mock was told to return, so a malformed statement or a schema constraint passes green — this has caused production failures twice (see `lessons.md`: `400 Syntax error: Unexpected keyword WINDOW`, and `400 Required field client_id cannot be null`). The round-trip script `scripts/test_bq_portfolio_time_dimension.py` is the only thing that runs statements against a real dataset, and it is manual by design (it creates and drops tables).

**Runner:** `uv run pytest --tb=short`

**CI:** the same command runs twice — as the required `Tests` job on every PR (`.github/workflows/tests.yml`, pre-merge gate) and again in `.github/workflows/deploy.yml` before the Docker build on `master`. The PR gate exists because e2e used to run post-merge only, so a CI-environment-only flake surfaced after landing instead of blocking the PR.

---

## Risk → test mapping

### R1 — Duplicate announcements stored in BigQuery

**Risk:** The scraper re-processes the same announcement ID on repeated runs, causing duplicate rows in `espi_ebi.announcements` and duplicate emails.

**Why high risk:** Cloud Scheduler triggers the scraper three times per day. A re-run after a transient failure or a window overlap would reprocess recent announcements without dedup.

| Test | File | What it covers |
|---|---|---|
| `test_dedup_filter` | `tests/test_scraper.py` | Known URLs filtered out of scrape result |
| `test_stop_condition_on_page` | `tests/test_scraper.py` | Stop fetching pages when all items on a page are older than window |
| `test_pagination_continues` | `tests/test_scraper.py` | New items on page 2 trigger next page fetch |
| `test_max_pages_safeguard` | `tests/test_scraper.py` | Hard page cap prevents runaway fetching |
| `test_empty_page_stops` | `tests/test_scraper.py` | Empty page terminates pagination |

---

### R2 — AI hallucinations / malformed JSON in Gemini response

**Risk:** Gemini returns trailing-comma JSON or deviates from the expected schema. Without strict parsing the pipeline silently stores garbage or crashes.

**Why high risk:** ~14% of Gemini responses historically had trailing commas (stdlib `json` fails); schema drift would bypass downstream supervisor checks.

| Test | File | What it covers |
|---|---|---|
| `test_happy_path_approved` | `tests/test_analyzer.py` | Valid JSON → `AnalysisResult`, approved=True |
| `test_trailing_comma_json_handled` | `tests/test_analyzer.py` | Trailing-comma JSON parsed via `json5` (not stdlib) |
| `test_gemini_api_error_analysis` | `tests/test_analyzer.py` | Gemini call failure → `None` (no crash) |
| `test_happy_path_returns_generated_post` | `tests/test_post_generator.py` | Happy path: valid tweets list |
| `test_missing_tweets_key_returns_none` | `tests/test_post_generator.py` | Schema missing `tweets` key → returns `None` |
| `test_empty_tweets_list_returns_none` | `tests/test_post_generator.py` | Empty tweets list → returns `None` |
| `test_trailing_comma_json_still_parses` | `tests/test_post_generator.py` | Trailing-comma JSON in post response handled |

---

### R3 — Supervisor retry loop failure / runaway

**Risk:** The supervisor retry loop in `post_main.py` runs up to 3 attempts. If the gate is wrong it could approve a bad post or loop forever.

**Why high risk:** The loop drives the final email send. An infinite loop would cause the Cloud Run Job to time out; an always-approve gate would send low-quality posts.

| Test | File | What it covers |
|---|---|---|
| `test_valid_post_approved` | `tests/test_post_supervisor.py` | Correct post approved |
| `test_wrong_tweet_count_rejected` | `tests/test_post_supervisor.py` | Wrong tweet count → rejected |
| `test_missing_ticker_rejected` | `tests/test_post_supervisor.py` | Missing `$TICKER` → rejected |
| `test_tweet_over_280_rejected` | `tests/test_post_supervisor.py` | Tweet > 280 chars → rejected |
| `test_missing_gpw_hashtag_rejected` | `tests/test_post_supervisor.py` | Missing `#GPW` hashtag → rejected |
| `test_missing_disclaimer_rejected` | `tests/test_post_supervisor.py` | Missing disclaimer → rejected |
| `test_buy_signal_rejected` | `tests/test_post_supervisor.py` | Buy/sell recommendation language → rejected |

---

### R4 — BigQuery write failures (silent data loss)

**Risk:** A DML operation (save analysis, save X-post, delete announcement) silently succeeds without writing any rows. The `num_dml_affected_rows == 0` check must raise rather than let the job complete with a false success.

| Test | File | What it covers |
|---|---|---|
| `test_save_analysis_result_null_approved_does_not_raise` | `tests/test_bigquery.py` | `NULL` for BOOL param is valid; does not raise |
| `test_save_analysis_result_approved_true` | `tests/test_bigquery.py` | Approved result written without error |
| `test_save_x_post_raises_on_no_announcements_updated` | `tests/test_bigquery.py` | Post write matching zero announcements raises instead of reporting success |
| `test_delete_announcement_raises_on_no_match` | `tests/test_bigquery.py` | Delete matching no row raises `no row matched` |
| `test_fetch_top_n_for_window_returns_rows` | `tests/test_bigquery.py` | Rows returned and mapped correctly |
| `test_fetch_top_n_for_window_empty` | `tests/test_bigquery.py` | Empty result handled without error |

---

### R5 — Parser breaks on malformed/empty HTML or PDF

**Risk:** Bankier.pl listing pages or linked PDFs can be empty, truncated, or structurally different. A parse failure should return `None` / empty string, not propagate an exception into the main pipeline.

| Test | File | What it covers |
|---|---|---|
| `test_seauid2_path` | `tests/test_parser.py` | seauid2 PDF link resolved and content extracted |
| `test_pdf_path_no_seauid2` | `tests/test_parser.py` | Direct PDF fallback path |
| `test_html_fallback_path` | `tests/test_parser.py` | HTML body used when no PDF found |
| `test_all_paths_fail` | `tests/test_parser.py` | All content paths unavailable → empty string |
| `test_pdf_char_cap` | `tests/test_parser.py` | PDF text truncated at char cap |
| `test_ticker_company_extracted` | `tests/test_parser.py` | Ticker and company name parsed from page |
| `test_parse_item_fields` | `tests/test_scraper.py` | Item fields (ticker, title, date, URL) from fixture HTML |

---

## Coverage gaps (known)

| Gap | Risk | Notes |
|---|---|---|
| No automated test runs SQL against a real BigQuery dataset | R1, R4 | The largest gap, and the source of two production failures. `scripts/test_bq_portfolio_time_dimension.py` does run real statements (throwaway `_rt_<uuid>` tables, cleanup in `finally`), but it is manual — it needs GCP credentials and creates/drops tables, so it cannot run in `tests.yml` |
| No HTTP contract tests for Bankier.pl | R5 | Bankier HTML structure changes are caught at runtime; `_FIXTURES/` HTML is locked to current structure |
| Line/branch coverage is not measured | Low | `pytest-cov` is not a project dependency, so no coverage number exists for this suite. Adding it is a decision, not an oversight — the number would be reported but nothing would gate on it |
| `list_broker_trades` — the only production entry point into the portfolio import flow — has no direct test | Medium | Every test reaches it through `patch`, so no test would notice a change to its SQL filters. Queued as phase 1 of `context/changes/refactor-opportunities/plan.md` |

---

## Running tests

```bash
# All tests
uv run pytest --tb=short

# Single module
uv run pytest tests/test_scraper.py -v

# Unit layer only (skips Playwright, ~10s)
uv run pytest --tb=short --ignore=tests/e2e

# E2E only (needs `uv run playwright install chromium` once)
uv run pytest tests/e2e --tb=short
```

Coverage flags are deliberately absent: `pytest-cov` is not installed (see Coverage gaps).
