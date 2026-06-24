<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Companies Dictionary Table (ticker, name, hop_url, isin)

- **Plan**: context/changes/companies-dictionary-table/plan.md
- **Scope**: Phase 4 of 4
- **Date**: 2026-06-24
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 8 observations (observations omitted from report body per cap; both warnings detailed below)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Unguarded listing-page fetch crashes the whole script on network failure

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/seed_companies.py:42
- **Detail**: `get(_LISTING_URL)` is called with no try/except. `src/http_client.get()` raises `ScraperError` after 3 retries (confirmed in src/http_client.py). If bankier.pl is unreachable, the script dies with a raw traceback instead of a clean log message — acceptable for a human-run one-off but inconsistent with the script's own per-company resilience (it already catches `BigQueryError` around `upsert_company`, scripts/seed_companies.py:64-69).
- **Fix**: Wrap the `get(_LISTING_URL)` call in `try/except ScraperError`, log a clean error, and `sys.exit(1)`.
- **Decision**: FIXED

### F2 — Test coverage gaps in extract_company_profile_links

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_company_profile.py:67-73
- **Detail**: The single test covers dedup-while-preserving-order and non-profile-link filtering well, but two edge cases are untested: empty HTML input (`extract_company_profile_links("")` should return `[]`) and an already-absolute href passing through `urljoin` unchanged. The second case is also the regression test that would catch the protocol-relative-URL edge case noted in the safety scan (see Note below).
- **Fix**: Add `test_extract_company_profile_links_empty_html_returns_empty_list` and a case asserting an absolute href in the fixture passes through unchanged.
- **Decision**: FIXED

## Note (informational, not a finding)

Safety scan flagged that `urljoin(_BANKIER_BASE_URL, href)` resolves on a substring filter (`"profile/quote.html" in href`) rather than a domain check, so a hypothetical `href="//evil.com/profile/quote.html"` would resolve off-domain. Not exploitable today — the listing URL is hardcoded to the trusted bankier.pl page — so this was not raised as a WARNING. Worth tightening only if this extraction function is ever pointed at a less-trusted source.

## Plan Drift Detection — summary

All Phase 4 contract items verified MATCH against actual code:
- `extract_company_profile_links` (src/company_profile.py:63-80) — exact contract match (html5lib, substring filter, urljoin, ordered dedup).
- Test fixture (tests/test_company_profile.py:56-73) — matches plan's specified shape (multiple rows, one duplicate, live href format).
- `seed_companies.py` flow (scripts/seed_companies.py:39-79) — matches: ensure table → fetch listing → extract links → per-link fetch+upsert → dry-run branch → summary log.
- `--dry-run` flag — exact match (`action="store_true", default=False`).
- Conventions vs `scripts/test_bq.py`/`test_alert.py` — matches (load_dotenv early, configure_logging, docstring with invocation).
- `pyproject.toml` `"scripts/*.py" = ["E402"]` — not spelled out verbatim in plan prose, but a direct necessary consequence of the plan's required `load_dotenv()`-before-import ordering, same justification already used for `main.py`/`src/api.py`. Not scope creep.
- No unplanned files, functions, or flags found.

## Success Criteria — verified

- `uv run pytest tests/test_company_profile.py -k extract_company_profile_links` → 1 passed
- `uv run pytest` (full suite) → 322 passed
- `uv run ruff check scripts/seed_companies.py src/company_profile.py` → all checks passed
- Manual 4.4/4.5/4.6 — all marked `[x]` in plan.md with evidence (BigQuery query results: 263 rows, 86 zero-announcement companies; live ISIN spot-check against bankier.pl for OPM/OND/NTT) — no rubber-stamping detected, evidence is concrete and reproducible.
