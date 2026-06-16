# X Post Char-Limit Guard — Plan Brief

> Full plan: `context/changes/xpost-char-limit-guard/plan.md`
> Frame brief: `context/changes/xpost-char-limit-guard/frame.md`

## What & Why

The 280-char limit on generated X tweets has no deterministic, code-side
enforcement — the system relies entirely on the LLM to self-judge and
self-correct length, which it is empirically unreliable at, including on
tweets that already have an explicit numeric budget. A prompt-only fix for
this exact bug (`fed8214`) was already tried and did not durably hold. This
plan adds the missing deterministic guard, plus complementary prompt
tightening.

## Starting Point

`generate_post()` (`src/post_generator.py`) already does one deterministic
post-processing pass on every attempt (`_normalize_ticker_spacing`) before
returning. `post_supervisor.py` only flags `len(tweet) > 280` — it never
fixes it — and `post_main.py` gives up after 3 failed attempts
(`AGENTS.md`'s hard supervisor-gate rule forbids softening that). No
equivalent of `is_publishable()`'s "independent deterministic guard" pattern
exists yet for tweet length.

## Desired End State

Every tweet `generate_post()` returns is ≤280 characters, with ticker
mentions, hashtags, and the disclaimer intact, regardless of what the LLM
produced — verified by unit tests, a round-trip test against
`validate_post()`, and a manual replay of the actual 2026-06-16 failing
data.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Where the guard lives | Inside `generate_post()`, every attempt | Mirrors the existing `_normalize_ticker_spacing` post-processing pattern; zero changes to the retry loop; naturally respects the hard supervisor gate | Plan |
| Trim strategy | Content-aware, protect structural tokens | A naive hard-truncate risks cutting `( TICKER )`, `#GPW`, or the disclaimer, or tripping the existing anti-truncation check | Plan |
| Scope of tweets covered | All positions (hook, body, closing) | 2026-06-12 evidence showed body tweets overflow too, not just the hook | Frame |
| Prompt/feedback changes | Yes, as a complementary (non-load-bearing) measure | Reduces how often the guard has to intervene; cheap to add | Plan |
| `n=4` fetch count | Leave unchanged | Frame found weak evidence n=4 is the discriminating factor (2/4 successes at n=4) | Frame |

## Scope

**In scope:**
- A deterministic length-trim function in `src/post_generator.py`, applied
  to every tweet on every attempt
- Explicit hook char budget in `_SYSTEM_PROMPT`
- Sharper, more directive `feedback_block` wording on retry
- Unit + round-trip tests

**Out of scope:**
- `n=4` in `fetch_top_n_for_window`
- The retry loop / attempt count in `post_main.py`
- Any LLM-based length-fixing call
- `is_publishable()`, the publish pipeline, BigQuery schema

## Architecture / Approach

One pure function in `src/post_generator.py`, slotted into the existing
post-processing pipeline right after `_normalize_ticker_spacing`. It
detects protected spans by content (ticker-parens, hashtags, the disclaimer
clause) rather than by tweet position, trims only the free text around
them, and never produces a `...`/`…`-terminated result (which would trip
the supervisor's own anti-truncation check). A second, independent phase
tightens the prompt and feedback text — useful but not required for
correctness, since the guard alone already guarantees the invariant.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Deterministic length guard | `generate_post()` never returns a >280-char tweet; ticker/hashtag/disclaimer spans survive trimming | Trim logic accidentally clips a protected span or produces a `...`-ending result |
| 2. Prompt and feedback tightening | Explicit hook budget + directive retry feedback, reducing how often Phase 1 has to act | Prompt changes aren't unit-testable for LLM compliance — only that the text is sent, not that Gemini obeys it |

**Prerequisites:** None — single-file change, existing test infra in place.
**Estimated effort:** ~1 session, 2 phases, each a separate commit.

## Open Risks & Assumptions

- Assumes tweet structure (bullets in hook, line-based body, hashtag/
  disclaimer tail in closing) stays consistent enough for regex-based
  protected-span detection to work; if the LLM's output shape drifts
  significantly, the guard's trim quality (not its 280-char guarantee)
  could degrade.
- Phase 2's effectiveness can only be observed in production over the next
  few days (Cloud Logging), not unit-tested for LLM compliance.

## Success Criteria (Summary)

- No generated tweet ever exceeds 280 characters, regardless of LLM output
- The 2026-06-16 failing announcement sets, replayed locally, now pass
  `validate_post` instead of exhausting all 3 attempts
- Existing test suite stays green; no regression in ticker/cashtag/window
  behavior
