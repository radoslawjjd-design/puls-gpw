<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: CI/CD AI Code-Review Pipeline

- **Plan**: context/changes/ci-cd-code-review/plan.md
- **Scope**: Phase 2 of 4
- **Date**: 2026-06-15
- **Verdict**: NEEDS ATTENTION → all findings triaged (2 fixed, 1 accepted)
- **Findings**: 0 critical, 1 warning, 2 observations

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

### F1 — json5 fallback can't catch the malformed-JSON case it's for

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence / Safety & Quality
- **Location**: tools/ai-code-reviewer/src/agent.ts:58-67
- **Detail**: Plan §3 requires a defensive json5 fallback for Gemini Flash's ~14% malformed-JSON rate. The SDK throws `NoObjectGeneratedError` *inside* `agent.generate()` (node_modules/ai/dist/index.js:3711+), carrying the raw output on `err.text`. The original code wrapped only `result.output` — a getter that throws the text-less `NoOutputGeneratedError` (index.js:5158-5163) — so the fallback never fired on the real failure mode. Both live round-trips passed only because output was well-formed.
- **Fix**: Wrapped `generate()` in the try; catch keys on `NoObjectGeneratedError.isInstance(err)` + `err.text`, then `parseReviewResult()` (json5 + schema). Extracted pure `parseReviewResult(text)` helper; added 2 unit tests feeding a trailing-comma payload through the recovery path.
- **Decision**: FIXED (commit pending)

### F2 — Convoluted diff-section header extraction

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tools/ai-code-reviewer/src/input.ts:51
- **Detail**: `section.slice(0, section.indexOf("\n") === -1 ? undefined : section.indexOf("\n"))` called `indexOf` twice and read awkwardly.
- **Fix**: Replaced with `section.split("\n", 1)[0]`.
- **Decision**: FIXED (commit pending)

### F3 — Fenced untrusted input is breakable by a closing tag

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tools/ai-code-reviewer/src/input.ts:78-87
- **Detail**: A PR body containing literal `</pr-body>` can break out of its fence. The plan ("Untrusted inputs") explicitly accepts this and relies on the structured score/verdict fields plus the system prompt's "treat as untrusted" instruction, not echoed text.
- **Fix**: None required (plan-accepted). Optional later hardening: strip/escape fence tokens from title/body.
- **Decision**: SKIPPED (accepted-by-design)
