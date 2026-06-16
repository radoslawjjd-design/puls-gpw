<!-- PLAN-REVIEW-REPORT -->
# Plan Review: X Post Char-Limit Guard Implementation Plan

- **Plan**: context/changes/xpost-char-limit-guard/plan.md
- **Mode**: Deep
- **Date**: 2026-06-16
- **Verdict**: REVISE → SOUND after triage (all 5 findings fixed)
- **Findings**: 1 critical, 3 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

6/6 paths ✓ (src/post_generator.py, src/post_supervisor.py, post_main.py, AGENTS.md, tach.toml, tests/test_post_generator.py), 7/7 symbols ✓ (_PAREN_TICKER_RE, _normalize_ticker_spacing, generate_post, validate_post, is_publishable, feedback_block, _MAX_ATTEMPTS), brief↔plan ✓

## Findings

### F1 — Unresolved tie-break between two absolute-sounding guarantees

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1, Contract (plan.md:140-147); Desired End State (plan.md:43-50)
- **Detail**: The Contract states two things as unconditional: (1) `len(result) <= limit` for any input, and (2) every ticker-paren/hashtag/disclaimer span present in the input is still present in the output. Desired End State repeats (1) as an absolute. Neither says which wins when a hook with 4 ticker-parens + structural text alone approaches 280, leaving too little free text to cut. Frame evidence suggests this is rare (worst observed overflow was 95 chars) but unverified at the floor.
- **Fix A ⭐ Recommended**: Length is non-negotiable; hard-truncate as last resort
  - Strength: Matches the plan's own Desired End State exactly; costs nothing extra; no regression vs. today.
  - Tradeoff: In the (unobserved) pathological case, the trimmed tweet might fail a different validate_post check instead of the length one.
  - Confidence: HIGH — no-op fallback in the worst case.
  - Blind spot: Untested — no real case has hit this floor yet.
- **Fix B**: Protected spans are non-negotiable; length may stay over 280
  - Strength: Never silently mangles a structurally-required token.
  - Tradeoff: Directly contradicts the plan's own Desired End State.
  - Confidence: MEDIUM — frequency unverified.
  - Blind spot: No data on how often structural-only content could exceed 280 at n=4.
- **Decision**: FIXED (Fix A — plan.md Contract now states length as the non-negotiable invariant, protected-span preservation is best-effort with a hard-truncate fallback)

### F2 — New trim step shares the Gemini-call exception handler

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1, Intent (plan.md:134-138) vs. src/post_generator.py:279-301
- **Detail**: The guard is wired right after `_normalize_ticker_spacing` inside `generate_post()`'s try block. Any exception it raises falls into `except Exception:` at post_generator.py:299-301, logged as "post_generator: Gemini call failed" — misleading for a bug unrelated to the Gemini call. Not called out in Critical Implementation Details.
- **Fix A ⭐ Recommended**: Document the risk, rely on Phase 1's test coverage
  - Strength: Zero added code complexity; guard is a simple, thoroughly-tested pure function.
  - Tradeoff: If a bug ships anyway, debugging is confused by the misleading log line.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Fix B**: Wrap the guard call in its own try/except, log distinctly
  - Strength: Clear debugging signal if the guard ever breaks.
  - Tradeoff: Adds a branch for a low-probability case; raises a new question (return untrimmed or None on guard failure).
  - Confidence: MEDIUM.
  - Blind spot: Fallback behavior on guard-failure still undecided.
- **Decision**: FIXED (Fix A — documented in plan.md Critical Implementation Details, no code change)

### F3 — Phase 2's company-count scaling needs an architecture decision

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 2 §1, Intent (plan.md:208-213)
- **Detail**: "Scales down as company count increases" is ambiguous: generic prose (keeps `_SYSTEM_PROMPT` static, post_generator.py:61,285) vs. dynamic per-call interpolation of `n_companies` (architecture change, forfeits prompt caching). The plan doesn't say which.
- **Fix A ⭐ Recommended**: Keep it static — phrase scaling as prose, not interpolation
  - Strength: Zero architecture change; matches how existing budgets (140-180, max-280) are already phrased; preserves caching.
  - Tradeoff: Less precise per-n than a literal interpolated number — acceptable since Phase 1's guard is the real backstop.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Fix B**: Build system_instruction dynamically, interpolating n_companies
  - Strength: Most precise guidance possible.
  - Tradeoff: Real architecture change; loses system_instruction caching across calls.
  - Confidence: MEDIUM — cost/latency impact at this job's volume (3x/day) probably negligible but unverified.
  - Blind spot: Caching/cost impact not measured.
- **Decision**: FIXED (Fix A — plan.md Phase 2 §1 now specifies static prose, no per-call interpolation)

### F4 — Manual verification re-derives an existing one-off script

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1, Manual Verification (plan.md:179-185)
- **Detail**: `_run_generate_post.py` (repo root) already implements "fetch real announcements → generate_post() → validate_post() → console + HTML email preview with per-tweet char-count badge" — exactly Phase 1's described manual-verification recipe. It currently fetches "last 7 days" rather than a specific historical window.
- **Fix**: Point Phase 1's manual verification at `_run_generate_post.py` (tweak its date range to target the 2026-06-16 windows) instead of describing a fresh reconstruction.
- **Decision**: FIXED (plan.md Phase 1 Manual Verification + Testing Strategy now point to `_run_generate_post.py`)

### F5 — Off-by-one line citation

- **Severity**: 📝 OBSERVATION
- **Dimension**: Plan Completeness
- **Location**: Critical Implementation Details (plan.md:104-106)
- **Detail**: Cites `post_supervisor.py:69` for the "rekomendacj" check; the actual `if` condition is on line 68 (69 is the `issues.append` call).
- **Fix**: Update citation to `post_supervisor.py:68-69`.
- **Decision**: FIXED (plan.md Critical Implementation Details citation corrected)
