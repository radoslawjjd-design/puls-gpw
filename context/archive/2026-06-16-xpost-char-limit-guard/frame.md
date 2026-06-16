# Frame Brief: X post hook/tweets exceed 280-char limit

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

On 2026-06-16, in both the `ranek` (06:25) and `poludnie` (10:55) windows, the
supervisor (`src/post_supervisor.py`) rejected all 3 generation attempts.
Tweet 1 (hook) exceeded 280 chars in every attempt; in the noon window tweets
2 and 5 also exceeded it.

- ranek: hook 293 → 315 → 375 chars (grew with each retry)
- poludnie: hook 332 → 316 → 336 chars, plus tweet 2/5 over limit

Effect: `post_main.py` exhausted `_MAX_ATTEMPTS=3` and sent "brak posta" for
both windows (`post_main.py:250-252`).

## Initial Framing (preserved)

- **User's stated cause**: (1) `_SYSTEM_PROMPT` gives no explicit char budget
  for the hook (only middle tweets 140-180 and closing max-280 have one);
  (2) `feedback_block` retry hint is generic, not deterministic — the LLM
  can't self-correct (hook grew across retries despite feedback);
  (3) no deterministic side-net in code that trims the hook after 3 failed
  LLM attempts, analogous to `is_publishable`.
- **User's proposed direction**: explicit per-company char budget in the
  hook prompt (scaled by company count); strengthen `feedback_block` with a
  concrete trim amount and target fragment; deterministic programmatic
  fallback truncation after 3 failures; reconsider `n=4` in
  `fetch_top_n_for_window` as possibly too aggressive for the hook's char
  budget.
- **Pre-dispatch narrowing**: user was not sure whether the leading concern
  is the hook specifically or the whole self-correction mechanism, not sure
  how to read the non-monotonic noon pattern, and not sure whether this was
  a first occurrence — asked to check logs for all three. All three were
  resolved by direct log/BQ/git investigation (see below) rather than by
  user judgment call.

## Dimension Map

The observation could originate at any of these dimensions:

1. **Hook prompt has no explicit char budget** — `_SYSTEM_PROMPT`
   (`src/post_generator.py:93-110`) gives bullet-list instructions for the
   hook with no numeric ceiling, unlike middle tweets (140-180,
   `post_generator.py:112`) and closing (max 280, `post_generator.py:140`). ←
   user's initial framing
2. **Retry feedback loop doesn't reliably self-correct** —
   `feedback_block` (`post_generator.py:256-263`) injects a generic textual
   hint ("Skróć hook jeśli lista spółek jest długa") with no deterministic
   target; the LLM may ignore or worsen it across attempts.
3. **No deterministic code-side length guard** — `post_supervisor.py:51-53`
   only *flags* `len(tweet) > 280`; nothing in `post_generator.py` or
   `post_main.py` trims/enforces length programmatically after repeated
   LLM failures.
4. **`n=4` fetch aggressiveness** — `post_main.py:200-202` hardcodes
   `fetch_top_n_for_window(..., n=4, ...)`; more companies → more hook
   bullets → harder to fit budget.
5. **Source data (`summary_pl` / `key_numbers`) verbosity** — longer or more
   complex announcement summaries could force longer hook bullets.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. Hook has no char budget | Confirmed by reading `_SYSTEM_PROMPT` — true gap. But the 2026-06-12 `wieczor` failure also breached on **tweet 2 and 3** (`post_generator.py:112`'s 140-180 budget tweets), which already have an explicit numeric limit and still overflowed by attempt 3. An explicit number alone doesn't guarantee compliance. | WEAK (real but not sufficient) |
| 2. Self-correction via feedback is unreliable | Commit `fed8214` (2026-06-12 15:49:24 UTC, *"pass supervisor rejection feedback to Gemini on retry"*) was deployed as a reactive hotfix **24 minutes after** the `wieczor` failure at 15:25 UTC that same day; a manual rerun 5 min later succeeded. The exact same failure signature recurred 4 days later (2026-06-16) with the feedback mechanism active, in one case the length *grew* across all 3 attempts (293→315→375) despite explicit feedback. This is a fix that was already tried, for this exact bug, and didn't hold. | STRONG |
| 3. No deterministic code-side guard | Confirmed absent: `post_supervisor.py` only flags, `post_main.py` only retries 3x then gives up (`post_main.py:226-252`). Codebase precedent exists for exactly this kind of guard: `is_publishable()` (`post_main.py:56-78`) is described in its own docstring as a "belt-and-braces substance guard, independent of post_supervisor" because "the supervisor occasionally approves an empty/degenerate thread" — same shape of problem (LLM/supervisor blind spot → need deterministic Python check), no equivalent exists for length. | STRONG |
| 4. `n=4` aggressiveness | BigQuery (`espi_ebi.x_posts` joined to `announcements` via `x_post_id`, data available since the table was added 2026-06-14) shows 4 `n=4` window-runs: 2 succeeded on attempt 1 (06-15 poludnie, 06-15 wieczor), 2 failed all 3 attempts (06-16 ranek, 06-16 poludnie). Both failures landed on the same day; other `n=4` days didn't fail. Not zero risk, but not the discriminating factor. | WEAK |
| 5. Source data verbosity | Compared `summary_pl` length / `key_numbers` count for tickers in the two failed 06-16 windows vs. the two successful `n=4` windows from 06-15 — both groups span ~150-450 chars with no outlier explaining 06-16 specifically (e.g. successful-group ticker NNG has a 456-char summary, same as the longest in either group). | NONE |

Hypothesis evidence was conclusive after the log/BQ/git investigation — Step 4
narrowing questions were skipped per protocol (stated explicitly to the user
before proceeding).

## Narrowing Signals

- User answered all three Step 1.5 questions with "not sure — check logs,"
  which redirected the investigation from judgment calls to direct evidence
  (Cloud Logging, BigQuery, git history) rather than guesswork.
- Full window-outcome history (`jsonPayload.message` in Cloud Logging,
  project `puls-gpw`, job `puls-gpw-post`, since 2026-06-08) shows this
  failure signature recurring (06-12, 06-16×2) against a majority of
  clean attempt-1 approvals — confirms "recurring latent issue," not a
  one-off or a brand-new regression.
- The 06-12 fix (`fed8214`) targeting this exact bug class, followed by
  recurrence in the same shape, is the single most decisive signal: it
  rules out "just improve the prompt wording" as a durable fix on its own.

## Cross-System Convention

This codebase already has an established pattern for exactly this situation:
when an LLM-driven step (generation or the rule-based supervisor) can't be
trusted to catch a class of defect reliably, a deterministic, independent
Python guard is added downstream (`is_publishable()` in `post_main.py:56-78`,
guarding against empty/degenerate threads the supervisor sometimes approves).
No equivalent deterministic guard exists yet for tweet length — the gap is
inconsistent with the codebase's own convention, not just with general best
practice.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: the 280-char limit has no
> deterministic, code-side enforcement — the system relies entirely on the
> LLM to self-judge and self-correct length, which this model is empirically
> unreliable at, including on tweets that already have an explicit numeric
> budget. A prompt-only fix for this exact bug (`fed8214`) was already tried
> and did not durably hold.

The user's own root-cause analysis (1-3) was directionally correct, but the
evidence shifts the weighting: (3) deterministic code-side guard is the
must-have fix, not an optional "belt and braces" extra — it's the only
dimension with both strong evidence and a direct codebase precedent
(`is_publishable`). (1) and (2) are reasonable complementary tightening but
historically insufficient alone. (4) is a risk-reduction tweak worth
considering but not the differentiator (n=4 mostly succeeds).

## Confidence

**HIGH** — strong evidence (git timeline + recurring log pattern + BQ cross-
check) on the leading hypothesis, matches an existing codebase convention,
and an alternative hypothesis (n=4, source verbosity) was actively
investigated and weakened/ruled out rather than assumed away.

## What Changes for /10x-plan

The plan should center a deterministic, code-side length guard for generated
tweets (the same "independent of the LLM/supervisor" pattern as
`is_publishable`) as the primary deliverable — not a prompt-wording change
alone. Prompt/budget tightening (explicit hook char ceiling scaled by company
count, more specific feedback on retry) is worth including as a
complementary measure to reduce how often the guard has to act, but should
not be planned as the sole fix, since an equivalent prompt-only approach
already failed to hold once. Whether n=4 should be reduced is a secondary,
lower-confidence consideration for the plan to weigh, not a given.

## References

- Source files: `src/post_generator.py:93-110,256-263`,
  `src/post_supervisor.py:51-53`, `post_main.py:56-78,200-202,226-252`
- Commit: `fed8214` (2026-06-12T17:49:24+02:00) — prior attempted fix for
  this exact bug class
- Logs: Cloud Logging, project `puls-gpw`, `resource.labels.job_name=
  "puls-gpw-post"`, `jsonPayload.message` field, 2026-06-08 through
  2026-06-16
- BigQuery: `puls-gpw.espi_ebi.x_posts` joined to `puls-gpw.espi_ebi.
  announcements` on `x_post_id`
- Tracking: Linear PUL-38, GitHub #49
