<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Cap the decompressed size of an uploaded broker .xlsx

- **Plan**: `context/changes/xlsx-import-size-cap/plan.md`
- **Mode**: Deep
- **Date**: 2026-08-01
- **Verdict**: REVISE → **SOUND** after triage (all 6 findings fixed)
- **Findings**: 0 critical, 4 warnings, 2 observations

## Verdicts

| Dimension | Verdict (at review) | After fixes |
|-----------|--------------------|-------------|
| End-State Alignment | PASS | PASS |
| Lean Execution | WARNING | PASS |
| Architectural Fitness | PASS | PASS |
| Blind Spots | WARNING | PASS |
| Plan Completeness | WARNING | PASS |

## Grounding

7/7 paths ✓ · 6/6 `read_sheets` call sites ✓ · `UploadFile.size` confirmed as `int | None`
(`starlette/datastructures.py:419-425,453`) ✓ · brief↔plan ✓

Verification was performed inline (grep/ls against the working tree) rather than via a
sub-agent, since the reviewer held the full research context.

## Findings

### F1 — Thresholds bound one request, not the instance

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Implementation Approach
- **Detail**: 8 MiB of XML expands to ~20–40 MiB of Python objects, plus the `bytes` copy and
  the tmpfs spool — roughly 50 MiB per request. At Cloud Run's default of 80 concurrent
  requests per 512 MiB instance (`deploy.yml:81-97` declares no `--concurrency`), ten
  simultaneous maximum-size uploads still exhaust it. The plan implied the availability problem
  was solved; it is reduced from unbounded to ~50 MiB/request, which is not the same thing.
- **Fix A ⭐ Recommended**: State the residual risk and add `--concurrency=8` as a fourth phase
  - Strength: Closes the real vector; today's 80 is an oversight, not a decision.
  - Tradeoff: Touches production config beyond the ticket's stated scope.
  - Confidence: MEDIUM — the memory arithmetic is estimated, not measured.
  - Blind spot: Real peak concurrency on this service is unknown.
- **Fix B**: Record the residual risk only
  - Strength: Change stays strictly within the ticket.
  - Tradeoff: Closes PUL-105 knowing the endpoint can still be toppled.
  - Confidence: HIGH — documentation-only.
  - Blind spot: None.
- **Decision**: FIXED via Fix A, with `--concurrency=8` chosen (8 × ~50 MiB ≈ 400 MiB inside
  512 MiB; 16 concurrent total across two instances). Added as Phase 4 plus a "Residual risk"
  paragraph in Implementation Approach.

### F2 — The user is told the wrong thing regardless of status code

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 3 / What We're NOT Doing
- **Detail**: `static/index.html:4242` renders a fixed string for every non-OK status —
  "Nie udało się odczytać pliku. Sprawdź, czy to eksport z wybranego domu maklerskiego." — so a
  413 tells the user their file is from the wrong broker. The entire 413-vs-422 decision, which
  consumed one of six question rounds, has zero user-visible effect.
- **Fix**: Branch on `r.status === 413` in the three handlers (`:4242`, `:4263`, `:4353`).
  - Strength: Three lines; also fixes the pre-existing 5 MB path, misreporting since PUL-95.
  - Tradeoff: Pulls `static/index.html` into a backend-shaped change.
  - Confidence: HIGH — all three sites confirmed by grep.
  - Blind spot: None.
- **Decision**: FIXED — added as change #4 in Phase 3; "What We're NOT Doing" narrowed to
  exclude only *pre-upload* browser checks.

### F3 — Phase 2 invents a parameter that `_REQUIRED_COLUMNS` already is

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Lean Execution
- **Location**: Phase 2
- **Detail**: The plan added a "wanted sheet titles" parameter to `read_sheets`. But
  `_REQUIRED_COLUMNS` (`xlsx_reader.py:15`, referenced only at `:53`) is already a dict keyed by
  sheet title holding exactly the one sheet the parser uses. Filtering on it achieves the same
  result with no signature change, no caller update, and no test churn.
- **Fix**: Filter the comprehension on `sheet.title in _REQUIRED_COLUMNS`.
  - Strength: Zero API change; the dict is already the single source of truth.
  - Tradeoff: Couples "what we read" to "what we validate".
  - Confidence: HIGH — `_REQUIRED_COLUMNS` has exactly two occurrences.
  - Blind spot: None.
- **Decision**: FIXED — Phase 2 shrank from three files to one; the coupling is named
  explicitly in the phase so a future split is a deliberate act.

### F4 — Signature change touches five test call sites

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2
- **Detail**: `read_sheets` is called at `tests/test_brokers_xlsx_reader.py:39,50,60,75,82`. The
  plan did not say whether the new parameter was required or optional, so it did not say whether
  those five break.
- **Fix**: List the call sites, or adopt F3 and remove the problem.
- **Decision**: FIXED — resolved by F3; Phase 2 now asserts the five call sites pass unmodified
  as an automated success criterion (2.1).

### F5 — Step 1.6 measures "before vs after" when "after" no longer parses

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 Manual Verification
- **Detail**: Once the ceiling lands, the bomb is rejected rather than parsed, so the pre-fix
  baseline becomes unrecoverable without stashing the change.
- **Fix**: Split into "record baseline before writing code" and "confirm rejection after".
- **Decision**: FIXED — Progress 1.6/1.7/1.8.

### F6 — Between Phase 1 and Phase 3 an oversized upload returns 422

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phases 1–3
- **Detail**: `BrokerFileTooLargeError` subclasses `BrokerImportError`, so until Phase 3 adds
  its own clause it falls through to `src/api.py:399-401`. Harmless, but reads like a regression
  if unannounced.
- **Fix**: One sentence in Migration Notes.
- **Decision**: FIXED.

## Triage summary

```
Fixed:     F1 (Fix A), F2, F3, F4, F5, F6   (6)
Skipped:   —
Accepted:  —
Dismissed: —

► Verdict after fixes: REVISE → SOUND
```
