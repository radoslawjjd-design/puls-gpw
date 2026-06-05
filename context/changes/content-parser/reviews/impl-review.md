<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: S-02 Content Parser (PDF + HTML)

- **Plan**: context/changes/content-parser/plan.md
- **Scope**: Phases 1–3 of 3
- **Date**: 2026-06-06
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical · 3 warnings · 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Undocumented seauid2+PDF combination mode, untested code path

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: src/parser.py (parse_announcement body)
- **Detail**: Plan specified strict seauid2 OR pdf OR html hierarchy. Actual implementation adds a combination mode: when seauid2 IS present AND pdf links also exist, the code merges both. This branch has no unit test — test_seauid2_path uses HTML with no PDF links, so it is never exercised.
- **Fix A ⭐ Recommended**: Add a targeted test for the combination branch — fixture with seauid2 content AND a PDF link; assert download_binary IS called and parsed_content includes merged text.
  - Strength: Closes the coverage gap without changing production code; combination logic was validated manually.
  - Tradeoff: Small test authoring effort (~20 lines).
  - Confidence: HIGH — all other branches are tested; this is clearly missing.
  - Blind spot: Edge cases in merge behaviour (e.g., empty PDF text) not checked.
- **Fix B**: Remove the combination mode — restore strict seauid2 OR pdf hierarchy.
  - Strength: Matches plan exactly; simpler code path.
  - Tradeoff: Loses an enhancement that may improve content quality.
  - Confidence: MED — unclear whether combination mode has real production value.
  - Blind spot: Unknown how often seauid2 + PDF co-occur in practice.
- **Decision**: FIXED via Fix A — added test_seauid2_pdf_combination to tests/test_parser.py

### F2 — No per-announcement error isolation in main.py loop

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: main.py:32–35
- **Detail**: BigQueryError correctly propagates to outer except → send_alert(). But unexpected non-BQ exceptions (e.g., AttributeError in html5lib parsing, edge case in ticker 2-hop) also abort the remaining announcements mid-loop. parse_announcement() is designed to "never raise" but the html5lib soup construction and ticker fetch are not wrapped.
- **Fix A ⭐ Recommended**: Add per-announcement try/except for unexpected errors; re-raise BigQueryError explicitly so alert path is preserved.
  - Strength: One bad announcement never kills the rest; BigQueryError still alerts; aligns with "parse failures are non-fatal" principle.
  - Tradeoff: Requires careful exception filtering (re-raise BigQueryError, swallow others).
  - Confidence: HIGH — scraper already handles per-item failures via yield pattern.
  - Blind spot: Need to verify BigQueryError is re-raised and not accidentally swallowed.
- **Fix B**: Accept current design — add comment documenting the behaviour.
  - Strength: Fail-fast on unexpected bugs is a reasonable philosophy.
  - Tradeoff: A bad URL in the announcement list can abort the whole run.
  - Confidence: MED — depends on Bankier.pl HTML reliability.
  - Blind spot: No production data on html5lib exception frequency.
- **Decision**: FIXED via Fix A — added per-announcement try/except with BigQueryError re-raise in main.py

### F3 — _extract_html_fallback silently discards content if Bankier layout changes

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/parser.py:160–162
- **Detail**: segments[1] is the announcement body (segments[0] is the Bankier AI summary preamble). If Bankier removes the preamble, segments[1] becomes wrong content and segments[0] is silently discarded → returns None. No comment explains the index choice.
- **Fix**: Add comment "skip segments[0] (Bankier AI summary preamble); segments[1] is the announcement body" and optionally fall back to segments[0] when len(segments) == 1.
- **Decision**: FIXED — added comment explaining segments[1] choice and segments[0] fallback in src/parser.py:_extract_html_fallback

### F4 — ParserError defined but never raised

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/parser.py:12, src/exceptions.py:20
- **Detail**: exceptions.py defines ParserError(PipelineStageError) but parser.py never raises it — returns ParsedContent with None fields instead. Creates a dangling class; future developers may wonder why it exists.
- **Fix**: Add module-level comment in parser.py: "Parse failures are non-fatal: all errors return None fields rather than raising ParserError. ParserError is reserved for future callers that need to distinguish failure modes."
- **Decision**: FIXED — added module-level comment to src/parser.py

### F5 — _BLOCKED_FILENAME_KEYWORDS named private vs plan's public name

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/parser.py:22
- **Detail**: Plan specified BLOCKED_FILENAME_KEYWORDS (public); implementation uses _BLOCKED_FILENAME_KEYWORDS (private underscore). Only used internally in _find_pdf_links. No functional impact.
- **Fix**: Accept as-is (private is arguably more correct for an internal list).
- **Decision**: SKIPPED — private naming is correct for an internal constant

### F6 — test_ticker_company_extracted brittle side_effect ordering

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_parser.py:164
- **Detail**: patch("src.parser.get", side_effect=[resp1, resp2]) — if a refactor adds a third get() call, side_effect exhausts and raises StopIteration (opaque failure).
- **Fix**: Accept as-is, or add comment: "side_effect order: [announcement page, profile page]".
- **Decision**: FIXED — added comment "side_effect order: [announcement page, profile page]" to test_ticker_company_extracted

### F7 — test_max_pdfs_limit comment missing: cap is in link-collection, not download loop

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_parser.py:152
- **Detail**: The cap is enforced by _find_pdf_links at link-collection time, not in the download loop. The test is correct but may mislead future maintainers.
- **Fix**: Add comment: "_find_pdf_links caps at _MAX_PDFS links before download; download_binary is called once per collected link."
- **Decision**: FIXED — added clarifying comment to test_max_pdfs_limit
