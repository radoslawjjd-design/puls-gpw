<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X Publisher Core

- **Plan**: context/changes/x-publisher-core/plan.md
- **Scope**: Phase 3 of 4
- **Date**: 2026-06-15
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

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

### F1 — Placeholder marker "brak post" over-matches legit Polish text

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: post_main.py:50 (_PLACEHOLDER_MARKERS)
- **Detail**: The substance guard rejects a thread if any marker is a substring of the
  joined lowercase text. "brak post" is a substring of legitimate Polish phrasing like
  "brak postępów" (no progress), so a real company thread could be classed not-publishable
  → silently skipped (status=skipped, email still sent, no auto-publish). Latent while the
  flag is OFF; surfaces as occasional suppressed-but-legit posts once ON. The intended
  hard-constraint phrase "brak posta" is already covered by its own entry.
- **Fix**: Drop "brak post" from _PLACEHOLDER_MARKERS (keep "brak posta"); degenerate
  threads are already caught by the <3-tweet / blank-text / no-cashtag checks.
- **Decision**: FIXED — removed "brak post", added comment warning against loose markers,
  added regression test `test_is_publishable_true_for_brak_postepow_not_a_placeholder`.

### F2 — MIN_XPOST_SCORE default duplicated as a literal

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:322 (min_score: float = 50) + post_main.py:42
- **Detail**: To avoid a circular import (bigquery ← post_main), the value 50 lives as a
  literal default in fetch_top_n_for_window AND as the tunable MIN_XPOST_SCORE in post_main,
  with the call site passing the constant explicitly. The pipeline is correct, but the two
  50s could silently drift for a future caller that omits the arg.
- **Fix**: Add a cross-reference comment so the bigquery default points at the source of truth.
- **Decision**: FIXED — added `# mirrors post_main.MIN_XPOST_SCORE (the tunable source of truth)`.

### F3 — Cashtag regex also matches dollar amounts

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: post_main.py:48 (_CASHTAG_RE)
- **Detail**: `$[A-Z0-9]{1,10}` matches "$100" as well as "$PKO", so a body tweet that
  references a price but no ticker could pass the substance check. This is exactly the regex
  the plan specified and mirrors the email renderer's `\$([A-Z0-9]+)` — consistent, not drift.
  Real body tweets always carry a real ticker, so the false-positive is theoretical.
- **Fix**: None required. If ever tightened, require a leading letter: `\$[A-Z][A-Z0-9]{0,9}`
  (and mirror the change in src/notifier.py for consistency).
- **Decision**: SKIPPED — accepted as-is per plan + notifier consistency; risk is theoretical.

## Notes

Phase 3 (commit 528d897) matches the plan contract: X_AUTO_PUBLISH flag (module-level,
default OFF), is_publishable substance guard (≥3 tweets, non-blank, ≥1 body cashtag, no
placeholder), _publish_to_x orchestration with correct ordering (save_x_post → guards →
publish → persist → email), never-raises contract, status taxonomy published|skipped|
failed|partial, email banner, and the MIN_XPOST_SCORE=50 fetch-time gate (verified live on
BQ — row count drops with the threshold, no sub-threshold row returns). Scope guardrails
respected (only a substance guard, no compliance caps; no Sentry; no published_at; no
rollback). Automated criteria green (14 post_main tests after F1 regression, full suite 132);
manual 3.7/3.10/3.11 verified with real evidence (live BQ query, real owner email, logic
tests), 3.8/3.9 correctly left pending for live-smoke (no X creds locally).
