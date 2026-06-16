<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X Post Char-Limit Guard

- **Plan**: context/changes/xpost-char-limit-guard/plan.md
- **Scope**: Phase 1 of 2
- **Date**: 2026-06-16
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations

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

Both Phase 1 deliverables (the `_enforce_length` guard + its wiring into `generate_post()`, and the 6 required test cases in `tests/test_post_generator.py`) MATCH the plan's contract exactly — confirmed by an independent drift-detection sub-agent and a re-run of the full test suite (147 passed). The space-reinsertion fix and the bonus idempotency test found in the diff are justified refinements (a real word-gluing bug found during live manual replay against 2026-06-16 BigQuery data), not scope drift — the commit message documents this explicitly.

All five "What We're NOT Doing" guardrails (post_main.py retry loop, n=4, no 4th LLM call, is_publishable()/publish pipeline/BQ schema, post_supervisor.py validation rules) were confirmed respected — neither `post_main.py` nor `src/post_supervisor.py` appears in commit `fff5444`'s diff.

Success criteria: 1.1/1.2 (automated) re-confirmed passing. 1.3 (manual replay) has real evidence — performed live against BigQuery + Gemini this session, not rubber-stamped, and is what surfaced the word-gluing bug that got fixed before commit. 1.4 (post-deploy Cloud Logging monitoring) is correctly left unchecked since it requires this code to ship first.

## Findings

### F1 — Hard fallback may clip a protected span, undocumented in docstring

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/post_generator.py:280-283
- **Detail**: When `_free_text_gaps` returns nothing (text is effectively all protected spans) or gap-trimming stalls, the final `text[:limit]` hard cut can slice through a hashtag or `( $TICKER )` mid-token. This is exactly the plan's documented contract ("fall back to a hard cut at limit... even if that means a protected span gets clipped") — not a deviation. The one-line docstring's "best-effort" phrasing covers it implicitly but doesn't spell it out.
- **Fix**: Optional — not required by the plan's contract or the project's one-line-docstring convention. Leave as-is unless the docstring should spell out the fallback explicitly.
- **Decision**: SKIPPED — already an accepted, documented tradeoff in the plan; no action taken.

### F2 — O(n²) worst case in the trim loop on adversarial input

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/post_generator.py:263-279
- **Detail**: Each loop iteration re-runs 3 regex scans (`_protected_spans`) and can shrink the largest gap by as little as 1 char on adversarial input (many short gaps separated by protected spans). Confirmed ~150ms at realistic tweet length (~450 chars) and no infinite loop on a 5000-char adversarial fuzz string in sub-agent testing. The plan's own "Performance Considerations" section calls this negligible — confirmed true for actual Gemini-generated tweets (≤6 strings, ~150-400 chars each), validated live against real BigQuery data this session.
- **Fix**: None needed — input domain is bounded by the plan's stated scope (Gemini-generated tweets, not adversarial strings).
- **Decision**: SKIPPED — out of the plan's stated input domain; no action needed.
