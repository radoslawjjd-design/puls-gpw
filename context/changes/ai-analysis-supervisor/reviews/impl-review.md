<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: S-03 Analiza AI + scoring ESPI/EBI

- **Plan**: context/changes/ai-analysis-supervisor/plan.md
- **Scope**: All phases (0–3)
- **Date**: 2026-06-07
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — BigQueryError on save_analysis_result aborts entire batch

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: main.py:49–51 + db/bigquery.py:282–285
- **Detail**: `save_analysis_result()` raises `BigQueryError` when 0 rows affected. Propagates via `except BigQueryError: raise` to outer handler → `send_alert()` + `sys.exit(1)`. Intentional per plan but kills remaining announcements in the batch. Analysis is lower-stakes than the core INSERT — a save_analysis failure shouldn't stop other announcements from being processed.
- **Fix A ⭐ Recommended**: Wrap `save_analysis_result(...)` in its own `except BigQueryError` inside the per-announcement loop, log WARNING, continue.
  - Strength: Matches analyzer's "never raises" contract; BQ transient errors become recoverable.
  - Tradeoff: Persistent BQ issues on analysis writes need monitoring on WARNING rate.
  - Confidence: HIGH — analyzer errors already use this pattern.
  - Blind spot: Haven't checked if S-04 depends on analysis always being present when announcement_id exists.
- **Fix B**: Keep abort, add `logger.error("BQ save_analysis failed for %s", ann_id)` before raise.
  - Strength: Preserves strict failure semantics; adds observability.
  - Tradeoff: Still kills the batch.
  - Confidence: HIGH — minimal change.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A — save_analysis_result wrapped in per-announcement except BigQueryError

### F2 — BOOL/None in ScalarQueryParameter not covered by unit tests

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:271
- **Detail**: `ScalarQueryParameter("analysis_approved", "BOOL", None)` confirmed working in 24h production run, but no unit test exercises save_analysis_result directly with approved=None.
- **Fix**: Add one test for save_analysis_result with analysis_approved=None (mock BQ client).
- **Decision**: FIXED — tests/test_bigquery.py added

### F3 — GEMINI_MODEL read on every API call instead of once at startup

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/analyzer.py:104, 126
- **Detail**: `model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")` called on every invocation. Misconfigured env var silently uses default with no log.
- **Fix**: Read and log model name once inside `_get_client()` at init time: `logger.info("Gemini model: %s", model)`.
- **Decision**: FIXED — _GEMINI_MODEL module-level constant + INFO log in _get_client()

### F4 — Loose caplog assertion in test_gemini_api_error_analysis

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_analyzer.py:70
- **Detail**: `assert "analysis" in caplog.text.lower()` passes if the word "analysis" appears anywhere. Actual warning message is "Gemini analysis call failed".
- **Fix**: Replace with `assert "analysis call failed" in caplog.text`.
- **Decision**: FIXED — assert tightened to "analysis call failed"

### F5 — Partial result on gate failure undocumented in plan

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/analyzer.py:186–195
- **Detail**: Gate failure returns partial AnalysisResult with structured_analysis/event_type populated. Plan says "errors → all-NULL" but this is better design — just undocumented.
- **Fix**: No code change needed. Acceptable as-implemented.
- **Decision**: SKIPPED

### F6 — Score suppressed on rejection not stated in plan

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/analyzer.py:197
- **Detail**: `score = _compute_score(...) if approved else None` — plan describes score formula but doesn't state rejected → score=NULL. Code is clearly correct.
- **Fix**: No code change needed. Acceptable as-implemented.
- **Decision**: SKIPPED
