# X Post Char-Limit Guard Implementation Plan

## Overview

Generated X post tweets occasionally exceed the 280-char limit (hook, and
sometimes body tweets), and the LLM cannot be relied on to self-correct this
across retries — confirmed by a prior prompt-only fix (`fed8214`) that
already tried and failed to hold. This plan adds a deterministic, code-side
length guard inside `generate_post()` (mirroring the existing
`is_publishable()` convention), plus complementary prompt/feedback
tightening to reduce how often the guard needs to act.

## Current State Analysis

- `src/post_generator.py:93-110` — `_SYSTEM_PROMPT`'s hook section gives no
  numeric char budget, unlike middle tweets (140-180,
  `post_generator.py:112`) and closing (max 280, `post_generator.py:140`).
- `src/post_generator.py:256-263` — `feedback_block` injects a generic hint
  ("Skróć hook jeśli lista spółek jest długa") with no concrete target.
- `src/post_generator.py:294` — `generate_post()` already runs deterministic
  post-processing on every attempt before returning
  (`_normalize_ticker_spacing`) — a ready-made convention to extend.
- `src/post_supervisor.py:51-53` — only *flags* `len(tweet) > 280`; nothing
  trims it.
- `src/post_supervisor.py:55-62` — the per-ticker `(TICKER)` presence check
  runs on `tweets[1:-1]` only — the hook is excluded, so trimming hook text
  is structurally safer than trimming body tweets.
- `src/post_supervisor.py:71-73` — rejects any tweet ending in `...` or `…`
  as "appears truncated." A trim that appends an ellipsis would immediately
  self-defeat against this existing check.
- `post_main.py:56-78` — `is_publishable()` is the established convention
  for "the LLM/supervisor can't be trusted here, add an independent
  deterministic Python guard."
- `AGENTS.md:12` — hard rule: "the supervisor gate is hard... do not bypass
  or soften the gate." The guard must not replace `validate_post` or change
  the 3-attempt discard semantics in `post_main.py:226-252`.
- `tach.toml:29-33` — `src` has no dependency that would block adding this
  logic to `src/post_generator.py`; no new module needed.
- `tests/test_post_generator.py` — pure pytest unit tests with a mocked
  Gemini client (`_mock_client` helper); no network/BQ. New tests follow
  the same pattern.

## Desired End State

`generate_post()` never returns a tweet longer than 280 characters,
regardless of what the LLM produced, while preserving every substring the
supervisor requires (`( TICKER )` mentions in body tweets, `#GPW` and the
"rekomendacj..." disclaimer in whichever tweet carries them) and never
producing a tweet that ends in `...`/`…` (which would trip the existing
truncation check). This is verified by:

- Unit tests on the guard function in isolation.
- A round-trip test: mocked Gemini response with deliberately oversized
  tweets → `generate_post()` → `validate_post()` reports `approved=True`.
- A manual replay of the actual 2026-06-16 failing announcement sets through
  the updated pipeline.

### Key Discoveries:

- The hook is excluded from the supervisor's per-ticker check
  (`post_supervisor.py:55-62` operates on `tweets[1:-1]`), so hook trimming
  has the fewest constraints to preserve.
- `_normalize_ticker_spacing` (`post_generator.py:294`) already establishes
  the "deterministic post-processing on every attempt, before return"
  pattern — the length guard slots in right next to it, with zero changes
  to `post_main.py`'s retry loop.
- The anti-truncation check (`post_supervisor.py:71-73`) means the guard
  must never end a trimmed tweet in `...`/`…` — it must cut at a clean
  sentence/clause boundary instead.

## What We're NOT Doing

- Not changing `n=4` in `fetch_top_n_for_window` (`post_main.py:201`) — the
  frame brief found weak evidence this is the discriminating factor (2/4
  successes at n=4); changing it would conflate two different fixes.
- Not changing the retry loop or attempt count in `post_main.py:226-252`.
- Not adding a 4th LLM call or any LLM-based length-fixing step — the guard
  is pure Python.
- Not touching `is_publishable()`, the publish pipeline, or the BigQuery
  schema.
- Not modifying `post_supervisor.py`'s validation rules — the guard runs
  before validation, the gate itself stays untouched.

## Implementation Approach

Add a single trim function in `src/post_generator.py`, applied to every
tweet on every attempt (idempotent no-op when already ≤280), right after
`_normalize_ticker_spacing` in the existing post-processing pipeline. This
keeps the change contained to one file, requires no changes to
`post_main.py`'s retry/discard logic, and naturally respects the hard
supervisor gate (the now-trimmed tweets still flow through the unchanged
`validate_post()` call on every attempt). A second, independent phase
tightens the prompt and retry feedback as a complementary measure — useful
on its own merits, but not load-bearing for correctness, since the guard
phase already guarantees the 280-char invariant regardless of LLM behavior.

## Critical Implementation Details

**Protected-span detection is position-independent.** The guard must not
assume the hook has no required substrings and the closing always has the
hashtags/disclaimer at a fixed offset — the LLM doesn't always follow the
prompt template exactly. Detect protected spans by content (regex), not by
tweet index: ticker-paren matches (reuse `_PAREN_TICKER_RE`,
`post_generator.py:191`), hashtag tokens (`#\w+`), and a disclaimer span
(any clause matching `rekomendacj` case-insensitively, per
`post_supervisor.py:68-69`). Only text outside these spans is eligible for
trimming.

**Trim must never produce a `...`/`…` ending.** Cut at the nearest preceding
sentence/clause boundary (`. `, `\n`, `!`, `?`) instead of mid-word, and
never append an ellipsis — `post_supervisor.py:71-73` rejects any tweet
ending that way, which would make the guard self-defeating.

**Idempotency.** Running the guard twice on its own output must return the
same string unchanged. This matters because it runs unconditionally on
every attempt (not just failed ones), same as `_normalize_ticker_spacing`.

**Exception handling is shared with the Gemini call.** The guard runs
inside `generate_post()`'s existing try block (`post_generator.py:279-301`),
alongside `_normalize_ticker_spacing`. Any exception it raises is caught by
the generic `except Exception:` and logged as "post_generator: Gemini call
failed" — misleading for a bug that has nothing to do with the Gemini call.
Accepted as-is: the guard is a simple pure function covered by Phase 1's
test suite, so the actual risk of an uncaught bug reaching this path is
low; no separate exception handling is being added for it.

## Phase 1: Deterministic length guard

### Overview

Add a pure-Python function that trims any tweet over 280 characters down to
the limit, preserving ticker mentions, hashtags, and the disclaimer, and
wire it into `generate_post()`'s existing post-processing step. This is the
primary, load-bearing fix — it guarantees the 280-char invariant regardless
of LLM behavior.

### Changes Required:

#### 1. Length guard function + wiring

**File**: `src/post_generator.py`

**Intent**: Add a function that takes one tweet string and returns it
unchanged if ≤280 chars, or trimmed to fit otherwise, protecting ticker
mentions / hashtags / the disclaimer span and never ending the result in
`...`/`…`. Call it on every tweet, on every attempt, immediately after the
existing `_normalize_ticker_spacing` pass (`post_generator.py:294`).

**Contract**: `def _enforce_length(tweet: str, limit: int = 280) -> str` —
pure function, no side effects, idempotent
(`_enforce_length(_enforce_length(t)) == _enforce_length(t)`). Reuses
`_PAREN_TICKER_RE` (`post_generator.py:191`) for ticker-span detection.
`len(result) <= limit` is the non-negotiable invariant — it must hold for
every input, with no exception. Preserving ticker-paren / hashtag /
disclaimer spans is best-effort on top of that: trim free text first, and
if no free text remains and the tweet is still over `limit`, fall back to a
hard cut at `limit` (still never ending in `...`/`…`) even if that means a
protected span gets clipped. This is no worse than today's behavior — an
unfixable tweet still gets caught by `validate_post`'s other checks (e.g.
missing ticker) the same way an over-length tweet does now — but it
guarantees the length invariant unconditionally, matching Desired End
State.

#### 2. Unit tests for the guard

**File**: `tests/test_post_generator.py`

**Intent**: Cover the guard in isolation and as part of the full
`generate_post()` round-trip, following the existing `_mock_client` pattern
in this file.

**Contract**: New test cases —
- already-short tweet returned unchanged (no-op / idempotency check)
- an over-280 hook with multiple bullets gets trimmed to ≤280
- an over-280 body tweet keeps its `( TICKER )` mention after trimming
- an over-280 closing tweet keeps `#GPW` and the disclaimer after trimming
- trimmed output never ends in `...` or `…`
- round-trip: mock Gemini to return deliberately oversized tweets (reuse
  the `_SIX_TWEETS`-style fixture, inflated past 280 on tweet 1 and one body
  tweet) → call `generate_post()` → feed the result into
  `src.post_supervisor.validate_post()` → assert `approved is True` and no
  `"exceeds 280"` / `"truncated"` issues.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_post_generator.py` passes
- `uv run pytest` (full suite) passes — no regressions in existing
  normalization/cashtag/window tests

#### Manual Verification:

- Use the existing `_run_generate_post.py` (repo root) — it already does
  fetch-real-announcements → `generate_post()` → `validate_post()` →
  console + HTML email preview with a per-tweet char-count badge. Adjust
  its date range (currently "last 7 days") to target the 2026-06-16
  `ranek` (KLE, PUR, ULG, CRI) and `poludnie` (MOL, IPW, PRH, PEP) windows
  specifically, and confirm `validate_post()` now approves on attempt 1 (or
  at least no longer rejects for length across all 3 attempts)
- After deploy, watch Cloud Logging (`job_name="puls-gpw-post"`) for the
  next `ranek`/`poludnie`/`wieczor` runs — confirm no recurrence of "all 3
  supervisor attempts failed" for length reasons

---

## Phase 2: Prompt and feedback tightening

### Overview

Complementary risk-reduction: give the LLM an explicit numeric hook budget
and a more directive retry feedback message, so the guard from Phase 1 has
to intervene less often. Not load-bearing for correctness — Phase 1 already
guarantees the 280-char invariant — but reduces how frequently trimmed
(rather than originally well-formed) hooks ship.

### Changes Required:

#### 1. Hook char budget in the system prompt

**File**: `src/post_generator.py`

**Intent**: Add an explicit guideline to the hook section of
`_SYSTEM_PROMPT` (around `post_generator.py:93-110`) stating a per-company
description budget that scales down as company count increases (mirroring
the existing explicit budgets for middle tweets at line 112 and closing at
line 140), plus a reiteration of the 280-char hard cap for the hook as a
whole. `_SYSTEM_PROMPT` stays a static module-level constant (unchanged
from today, `post_generator.py:61,285`) — the scaling guidance is phrased
as a general prose formula (e.g. "the more companies, the shorter each
bullet's description — the full hook still has to fit in 280 chars
total"), not a literal number tied to a specific company count. This
matches how the existing 140-180 / max-280 budgets are already static
prose, and preserves Gemini's `system_instruction` caching across calls;
building the prompt dynamically per `n_companies` is explicitly out of
scope for this phase.

**Contract**: `_SYSTEM_PROMPT`'s hook section gains a prose char-budget
guideline that scales qualitatively with company count, expressed as
static text (no per-call interpolation); existing example blocks
(1-company, 3-company) stay valid illustrations.

#### 2. Directive retry feedback

**File**: `src/post_generator.py`

**Intent**: Strengthen `feedback_block` (`post_generator.py:256-263`) to
give a concrete, actionable correction target instead of the generic "Skróć
hook jeśli lista spółek jest długa" — point specifically at the event
description text (not the ticker, emoji, or structural tokens) as the part
to cut, with a numeric safety-margin ceiling below 280.

**Contract**: `feedback_block`'s trailing guidance line names the
event-description text as the trim target and states a numeric ceiling
(e.g. a safety margin below 280, not just "280").

#### 3. Prompt/feedback tests

**File**: `tests/test_post_generator.py`

**Intent**: Assert the new guidance text is actually present in what gets
sent to Gemini, following the existing `call_contents` assertion pattern
(e.g. `test_closing_question_injected`, `post_generator.py` test file).

**Contract**: New assertions on `system_instruction` (or `contents`, per
existing test pattern) confirming the numeric hook budget appears in the
system prompt, and a new test confirming `feedback_block`'s sharpened
wording appears in `contents` when `previous_issues` is passed to
`generate_post()`.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_post_generator.py` passes
- `uv run pytest` (full suite) passes

#### Manual Verification:

- After deploy, monitor Cloud Logging over the following few days for a
  reduction in attempt-1 rejections for length (secondary signal — Phase 1
  is the actual guarantee, this just confirms the prompt change isn't
  counterproductive)

---

## Testing Strategy

### Unit Tests:

- `_enforce_length` in isolation: no-op under limit, trims over limit,
  preserves protected spans, never ends in `...`/`…`, idempotent
- Prompt/feedback content assertions (Phase 2)

### Integration Tests:

- `generate_post()` + `validate_post()` round-trip with deliberately
  oversized mocked Gemini output, asserting final approval

### Manual Testing Steps:

1. Run `_run_generate_post.py` (repo root) with its date range adjusted to
   target the 2026-06-16 `ranek`/`poludnie` windows, against the real
   (non-mocked) pipeline
2. Confirm the resulting tweets are all ≤280 chars and `validate_post`
   approves
3. After deploy, watch the next few production windows in Cloud Logging for
   absence of the "all 3 attempts failed" signature

## Performance Considerations

Negligible — the guard runs on at most 6 short strings per generation
attempt, pure string/regex operations, no I/O.

## Migration Notes

None — no schema or data model change; `generate_post()`'s public signature
is unchanged.

## References

- Frame brief: `context/changes/xpost-char-limit-guard/frame.md`
- Source files: `src/post_generator.py:93-110,191,256-263,294`,
  `src/post_supervisor.py:51-53,55-62,69,71-73`, `post_main.py:56-78,201,226-252`
- Hard rule: `AGENTS.md:12`
- Tracking: Linear PUL-38, GitHub #49

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Deterministic length guard

#### Automated

- [x] 1.1 `uv run pytest tests/test_post_generator.py` passes — fff5444
- [x] 1.2 `uv run pytest` (full suite) passes — fff5444

#### Manual

- [x] 1.3 Replay 2026-06-16 ranek/poludnie data locally — confirm ≤280 chars and validate_post approval — fff5444
- [ ] 1.4 Watch next production windows in Cloud Logging for absence of the failure signature

### Phase 2: Prompt and feedback tightening

#### Automated

- [ ] 2.1 `uv run pytest tests/test_post_generator.py` passes
- [ ] 2.2 `uv run pytest` (full suite) passes

#### Manual

- [ ] 2.3 Monitor Cloud Logging over following days for reduced attempt-1 length rejections
