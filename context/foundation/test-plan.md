---
project: ESPI/EBI Analyzer
version: 1
status: draft
created: 2026-06-08
updated: 2026-06-08
---

# Test Plan: ESPI/EBI Analyzer

> Coverage of the automated test suite — risks, mapping, and strategy.
> All tests live in `tests/`. Run with `uv run pytest`.

## Testing strategy

**Approach: unit tests with mocked boundaries.**

All external services (BigQuery, Bankier.pl HTTP, Gemini API) are mocked via `unittest.mock.patch`. Tests are fast, deterministic, and require no credentials — suitable for CI.

No integration or end-to-end tests exist yet. Manual verification steps in each change plan cover the production path.

**Runner:** `uv run pytest --tb=short`
**CI:** runs as a required step in `.github/workflows/deploy.yml` before the docker build.

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

**Risk:** A DML operation (save analysis, save post text) silently succeeds without writing any rows. The `num_dml_affected_rows == 0` check must raise rather than let the job complete with a false success.

| Test | File | What it covers |
|---|---|---|
| `test_save_analysis_result_null_approved_does_not_raise` | `tests/test_bigquery.py` | `NULL` for BOOL param is valid; does not raise |
| `test_save_analysis_result_approved_true` | `tests/test_bigquery.py` | Approved result written without error |
| `test_save_post_text_calls_query_with_unnest` | `tests/test_bigquery.py` | `UNNEST(@ids)` present in update query |
| `test_save_post_text_none_records_failure` | `tests/test_bigquery.py` | `post_text=None` records supervisor failure (no raise) |
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
| No integration tests against real BQ | R1, R4 | Manual verification covers production path via change plan steps |
| No HTTP contract tests for Bankier.pl | R5 | Bankier HTML structure changes are caught at runtime; `_FIXTURES/` HTML is locked to current structure |
| No email send test (`src/notifier.py`) | Low | SMTP call uses `unittest.mock` context implicitly; full send tested manually |
| No auth / API key test | Medium | FastAPI admin API (PUL-17) not yet implemented |

---

## Running tests

```bash
# All tests
uv run pytest --tb=short

# Single module
uv run pytest tests/test_scraper.py -v

# With coverage (dev use)
uv run pytest --tb=short --cov=src --cov=db
```
