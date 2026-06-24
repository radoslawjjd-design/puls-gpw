<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Companies Dictionary Table (ticker, name, hop_url, isin)

- **Plan**: context/changes/companies-dictionary-table/plan.md
- **Scope**: Phase 2 of 4
- **Date**: 2026-06-23
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Summary

Git scope (commit f93dd31) exactly matches the plan's Phase 2 file list:
`src/company_profile.py` (new), `src/parser.py`, `tests/test_company_profile.py`
(new), `tests/test_parser.py`, plus the Progress section in `plan.md`. No
unplanned files in the diff.

### Plan Adherence

All four planned changes verified as exact matches:

1. **`src/company_profile.py`** — `CompanyProfile` dataclass (`ticker`,
   `company`, `isin`, `hop_url`) and `fetch_company_profile(profile_url) ->
   CompanyProfile | None` exactly as specified. Returns `None` on
   `ScraperError` (lines 27-31). `isin` parsed from `data-isin` on
   `#quotes-profile-header-box`, degrades to `None` without raising if
   absent (lines 52-56).
2. **`_extract_ticker_company` refactor** (`src/parser.py:178-195`) — anchor-
   finding logic kept local, hop+parse delegated to
   `fetch_company_profile()`, returns the new 4-tuple
   `(ticker, company, hop_url, isin)`. The one call site
   (`src/parser.py:53`) correctly unpacks all four values.
3. **`ParsedContent` threading** — `hop_url: str | None` and
   `isin: str | None` added to the dataclass; all 6 constructor call sites
   (`src/parser.py:50,74,76,88,93,96`) updated; HTTP-failure path passes
   `None, None, None, None`.
4. **Tests** — `_HTML_PROFILE_PAGE` fixture gained the `data-isin` wrapper
   section; existing parser tests updated for the 4-tuple/new fields;
   `tests/test_company_profile.py` covers happy path, missing-isin, and
   `ScraperError` → `None`, all mocking `src.company_profile.get`.

### Scope Discipline

No violations of the plan's "What We're NOT Doing" guardrails. No changes to
`update_parsed_content()`, the `announcements` schema, watchlist/portfolio
wiring, autocomplete endpoints, Cloud Run provisioning, or retry/backoff
logic.

### Safety & Quality

No CRITICAL or WARNING findings. HTTP boundary correctly wrapped in
`try/except ScraperError` (matches `parser.py`'s existing pattern). All HTML
attribute/element access is guarded before use (`if not heading`, `if not
section`, `.get("data-isin") or None`). No secrets, no unbounded loops, no
injection risk (regex output used only as a plain string field, not passed
to any SQL/shell/HTML sink in this phase).

One non-finding OBSERVATION noted by the reviewing agent: `_extract_isin`
selects `#quotes-profile-header-box` by ID via `select_one`, same
single-element-assumption risk profile as the pre-existing heading selector
— consistent with the codebase's existing tolerance for markup drift, not a
new risk introduced by this phase.

### Architecture

Clean extraction: `src/company_profile.py` is a new leaf module with no
circular dependency back into `src/parser.py`. `_extract_ticker_company`
remains module-private to `parser.py`, was the function's only caller, and
was updated correctly.

### Pattern Consistency

Logging, docstring style, error-handling convention (`return None` on
degrade, raise only at `http_client.get`), `str | None` type hints, import
ordering, and dataclass style all match `src/parser.py`/`src/http_client.py`
conventions.

### Success Criteria

- `uv run pytest tests/test_parser.py tests/test_company_profile.py -q` —
  13 passed
- `uv run pytest` (full suite) — 319 passed
- `uvx ruff check src/parser.py src/company_profile.py tests/test_parser.py
  tests/test_company_profile.py` — clean (one pre-existing unused-import
  warning in `tests/test_parser.py` predates this phase, not introduced by
  it)
- Manual verification 2.4 (real `parse_announcement` run against a live,
  current bankier.pl announcement) — evidence-backed, not rubber-stamped:
  extracted `ticker=CFG`, `company=CreativeForge Games SA`,
  `hop_url=.../profile/quote.html?symbol=CFG`, `isin=PLCRFRG00016`, then
  cross-checked directly against the raw `data-isin`/`data-symbol`
  attributes on the live page — exact match.

## Findings

None.
