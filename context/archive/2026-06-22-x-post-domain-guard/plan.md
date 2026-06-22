# Block domain-like text in generated X posts — Implementation Plan

## Overview

GPW company names that are themselves domain-like (e.g. `Oponeo.pl`) get
written verbatim into generated tweets by both the ESPI/EBI pipeline and
(in principle) the portfolio thread composer. X's client-side link
detection then auto-renders the substring as a clickable hyperlink to that
external domain, which must never happen in a generated post. This plan
adds a deterministic text transform that defuses the domain-like substring
before it ever reaches X, plus a non-blocking safety-net check, plus a
documentation update for the one remaining human-text-injection path.

## Current State Analysis

- `src/parser.py:175-201` (`_extract_ticker_company`) scrapes the company
  name verbatim from the Bankier profile heading. No alternate "short/plain
  name" field exists anywhere upstream.
- `src/post_generator.py:61-169` (`_SYSTEM_PROMPT`) instructs Gemini to
  print the literal company name (line 124, "📊 Nazwa Spółki"). The prompt
  already forbids "Linki w tweetach" (line 159), but this doesn't stop a
  company's own name from being printed — the model doesn't treat its own
  name as a link.
- `src/post_generator.py:203-220` already has two deterministic
  post-processing fixups applied after the Gemini call —
  `_normalize_ticker_spacing` and `_enforce_body_cashtag` — both wired into
  the per-tweet loop in `generate_post()` (lines 408-416), before
  `_enforce_length`. This is the established convention for "Gemini gets a
  textual detail wrong/inconsistent, fix it deterministically."
- `src/post_supervisor.py:32-80` (`validate_post`) is a reject-and-regenerate
  gate: any non-empty `issues` list rejects the post, and
  `post_main.py:271-298`'s loop retries up to `_MAX_ATTEMPTS = 3` times,
  feeding `issues` back as `previous_issues`. This loop is deliberately
  all-or-nothing across the whole window's thread (confirmed via
  `context/archive/2026-06-08-xpost-generation/plan.md:372-395`, commit
  `e4cf6f4`) — exhausting all 3 attempts calls
  `save_x_post(ann_ids, None, ...)` (line 297), silently dropping
  *every* company bundled in that window's thread (up to 4).
- `ValidationResult` (`post_supervisor.py:26-29`) currently has exactly one
  signal — `issues` — and it is binary: any entry means rejected. There is
  no existing concept of a non-blocking warning.
- `src/portfolio_thread_composer.py` + `PortfolioPosition`
  (`src/gemini_client.py:104-108`) only ever carry `ticker`, never a company
  name — confirmed by reading the dataclass. The composer cannot produce
  domain-like text on its own. The one residual text-injection path is the
  human-driven "Edytuj" free-text edit in
  `.claude/skills/portfolio-xpost/SKILL.md` Step 3, which already routes
  through a human approval gate (Step 3) before anything publishes.
- Full framing/evidence trail: `context/changes/x-post-domain-guard/frame.md`.

### Key Discoveries:

- A domain-like company name is a *literal fact*, not a *rephrasable
  choice* — unlike the existing `_ADVICE_RE` investment-advice check
  (`post_supervisor.py:8-23`), there is no alternate text the LLM can
  regenerate toward. Routing this defect through `validate_post()` as a
  blocking issue would make every retry attempt fail identically.
- `tests/test_post_generator.py` and `tests/test_post_supervisor.py` show
  the established test shape: pure-function unit tests with a small table
  of literal-string cases, plus one or two `generate_post()`/`validate_post()`
  integration ("round trip") tests with a mocked Gemini client. The new
  work should mirror this shape exactly.
- Import direction is fixed: `post_supervisor.py` already imports from
  `post_generator.py` (line 5, `GeneratedPost`). The new domain regex must
  therefore live in `post_generator.py`, with `post_supervisor.py` importing
  it — the reverse would be a circular import.

## Desired End State

No tweet published by either pipeline can ever render as a clickable link
to an external domain. A company whose real name is domain-like still
appears recognizably in the post (with the TLD suffix stripped), and
existing valid posts are unaffected.

Verification: existing `tests/test_post_generator.py` and
`tests/test_post_supervisor.py` suites pass, new tests covering the
sanitizer/warnings pass, and a manual run of `generate_post()` with a
synthetic domain-like company name produces tweets with no remaining
`.pl`/`.com`/etc. substrings.

## What We're NOT Doing

- Not modifying `src/portfolio_thread_composer.py` or `PortfolioPosition` —
  neither ever carries a company name, only a ticker, so there is no
  code-level exposure to this defect in that pipeline.
- Not sanitizing the `company` field before it is sent to Gemini in the
  prompt (`post_generator.py`'s `enriched` dict) — the chosen fix operates
  on the final tweet text, which covers both the echoed-input case and any
  domain-like text Gemini might introduce independently of the company
  name. Sanitizing the prompt input as well would be redundant.
- Not adding the domain check to `validate_post()`'s blocking `issues` list
  — confirmed by the frame brief to risk silently dropping an entire
  multi-company thread, since there is no alternate text to regenerate
  toward.
- Not changing `send_post_email` or any email content to surface the new
  `warnings` field — it is a log-only signal for this change.
- Not expanding the TLD list beyond the common set already named in scope
  (`pl`, `com`, `net`, `org`, `info`, `io`, `co`) — easy to extend later if a
  gap surfaces.

## Implementation Approach

Add a deterministic sanitizer (`_strip_domain_suffix`) to
`post_generator.py`, wired into the same per-tweet post-processing loop as
the existing `_normalize_ticker_spacing`/`_enforce_body_cashtag` fixups —
applied to the full text of every tweet (hook, body, closing), not just the
company-name field, so it also catches domain-like text Gemini might
introduce on its own. Add a non-blocking `warnings` field to
`ValidationResult`, populated by independently re-checking the same regex
against the final post text — this is a safety net against any code path
that builds a `GeneratedPost` without going through the sanitizer (e.g. the
portfolio skill's manually-composed text), not a duplicate of the
sanitizer itself. Finally, extend the `portfolio-xpost` skill's manual
validation instructions to call the same sanitizer function, closing the
one real (human-text) injection path without touching
`portfolio_thread_composer.py`.

## Phase 1: Core sanitizer in post_generator.py

### Overview

Add the regex and transform function, wire it into `generate_post()`'s
per-tweet loop, and cover it with unit + integration tests.

### Changes Required:

#### 1. Sanitizer function and regex

**File**: `src/post_generator.py`

**Intent**: Detect a domain-like substring (`<name>.<tld>` for the TLD set
`pl|com|net|org|info|io|co`) and strip the dot+TLD suffix, leaving the bare
name — defusing X's link auto-detection while keeping the company
recognizable. Apply it inside the existing per-tweet post-processing loop,
alongside `_normalize_ticker_spacing`/`_enforce_body_cashtag`, before
`_enforce_length`.

**Contract**: New module-level regex `_DOMAIN_TLD_RE` and function
`_strip_domain_suffix(text: str) -> str`, placed near the other normalizer
helpers (after `_enforce_body_cashtag`, before the `_HASHTAG_RE`/length
helpers). Pattern:
```python
_DOMAIN_TLD_RE = re.compile(r"\b([\w-]+)\.(pl|com|net|org|info|io|co)\b", re.IGNORECASE)

def _strip_domain_suffix(text: str) -> str:
    return _DOMAIN_TLD_RE.sub(r"\1", text)
```
In `generate_post()`'s per-tweet loop (~line 409-416), insert the call right
after `_normalize_ticker_spacing(t)` and before the body-cashtag/length
steps, so length enforcement measures the already-shortened text:
```python
t = _normalize_ticker_spacing(t)
t = _strip_domain_suffix(t)
```
The `\b...\b` boundaries mean a TLD must be followed by a non-word
character or end-of-string, so e.g. `firma.placu` (TLD `pl` immediately
followed by the word characters `acu`) does not false-positive.

### Success Criteria:

#### Automated Verification:

- Unit tests for `_strip_domain_suffix` pass: `uv run pytest tests/test_post_generator.py -k strip_domain`
- Full `post_generator` suite passes (no regressions in existing normalizer tests): `uv run pytest tests/test_post_generator.py`

#### Manual Verification:

- Run `generate_post()` locally (or via the existing mocked-Gemini pattern) with an announcement whose `company` is `"Oponeo.pl"` and confirm the returned tweets contain `"Oponeo"` with no `.pl` substring anywhere in the thread

---

## Phase 2: Non-blocking observability in post_supervisor.py

### Overview

Add a `warnings` field to `ValidationResult` and populate it by
independently re-checking the domain regex against the final post text,
without affecting `approved`. Log it (non-blocking) in `post_main.py`.

### Changes Required:

#### 1. `ValidationResult` schema + check

**File**: `src/post_supervisor.py`

**Intent**: Provide a safety net that flags any domain-like text surviving
into a `GeneratedPost` — regardless of which code path constructed it —
without ever causing rejection, since this defect class cannot be fixed by
regeneration (see Current State Analysis).

**Contract**: Extend the import line to pull in the regex from
`post_generator.py` (`from src.post_generator import GeneratedPost,
_DOMAIN_TLD_RE`). Add `warnings: list[str] = field(default_factory=list)`
to the `ValidationResult` dataclass. At the end of `validate_post()`, after
the existing `_ADVICE_RE` check, scan `full_text` for `_DOMAIN_TLD_RE`
matches and append one warning string per match (mirroring the existing
`investment advice detected: "..."` message shape) into the new
`warnings` list — `issues` and `approved` are untouched by this scan.

#### 2. Non-blocking log line

**File**: `post_main.py`

**Intent**: Surface the warning for operator visibility without changing
control flow.

**Contract**: Right after `result = validate_post(...)` (~line 276), before
the `if result.approved:` check, if `result.warnings` is non-empty, log it
at `warning` level (e.g. `logger.warning("post_main: attempt %d has
warnings: %s", attempt, result.warnings)`) — placed so it fires on every
attempt regardless of approval, but never affects the `if result.approved:`
branch itself.

### Success Criteria:

#### Automated Verification:

- New tests pass: `uv run pytest tests/test_post_supervisor.py -k warning`
- Full `post_supervisor` suite passes: `uv run pytest tests/test_post_supervisor.py`
- A test confirms `approved` is unaffected by `warnings`: a post with
  `issues == []` and a domain-like substring present must have
  `approved is True` and a non-empty `warnings` list

#### Manual Verification:

- Confirm via log inspection (or a manual `post_main` dry run) that a
  warning is logged without blocking publication, for a post whose text
  contains domain-like text that bypassed the Phase 1 sanitizer (e.g. by
  constructing a `GeneratedPost` directly rather than via `generate_post()`)

---

## Phase 3: portfolio-xpost skill documentation

### Overview

Close the one real residual injection path — the human "Edytuj" free-text
edit — by reusing the Phase 1 sanitizer in the skill's existing manual
validation instructions. No Python source file changes.

### Changes Required:

#### 1. Step 2.2 validation checklist

**File**: `.claude/skills/portfolio-xpost/SKILL.md`

**Intent**: Apply the same domain-text defusing to the composed portfolio
tweets as a defense-in-depth measure, consistent with how the skill already
reuses `_ADVICE_RE` from `post_supervisor.py` for the investment-advice
check.

**Contract**: Add a bullet to the Step 2.2 validation list (alongside the
existing length/truncation/`_ADVICE_RE`/disclaimer bullets, ~lines 153-167)
instructing the agent to import and apply `_strip_domain_suffix` from
`src.post_generator` to both the header and leaders tweets before
presenting them.

#### 2. Step 3 "Edytuj" re-validation list

**File**: `.claude/skills/portfolio-xpost/SKILL.md`

**Intent**: Ensure a user-supplied free-text edit is re-checked for
domain-like text the same way the original checks are re-run.

**Contract**: Extend the existing sentence "Re-run the same Step 2.2
validation checks (length ≤280, no truncation, `_ADVICE_RE`, disclaimer
present)" (~line 204) to also name the domain-text check.

### Success Criteria:

#### Automated Verification:

- N/A — documentation-only change, no automated check applies

#### Manual Verification:

- Read through the updated Step 2.2/Step 3 sections and confirm the
  instructions are unambiguous about importing and calling
  `_strip_domain_suffix` on both tweets, in both the initial-compose and
  Edytuj-revision paths

---

## Testing Strategy

### Unit Tests:

- `_strip_domain_suffix`: strips `.pl`/`.com`/`.net`/`.org`/`.info`/`.io`/`.co`
  suffixes (case-insensitive); leaves non-TLD dotted text alone (e.g. a
  year-like or numeric pattern, an unrelated word ending in a near-miss
  like `plus`); idempotent on already-stripped text.
- `ValidationResult.warnings`: populated when domain-like text is present
  in `post.tweets`; empty when absent; never influences `approved`.

### Integration Tests:

- `generate_post()` round-trip (mirroring
  `test_round_trip_oversized_tweets_get_trimmed_and_approved`): mock Gemini
  to return a tweet containing a domain-like company name (e.g.
  `"📊 Oponeo.pl ( OPN )\n..."`), assert the returned tweet has no
  `.pl`/`.com`/etc. substring, then run the result through `validate_post()`
  and assert `approved is True` with empty `warnings`.
- `validate_post()` called directly on a hand-built `GeneratedPost` that
  still contains a domain-like substring (simulating a path that bypassed
  the sanitizer): assert `approved is True` (issues unaffected) and
  `warnings` is non-empty.

### Manual Testing Steps:

1. Run `generate_post()` with a synthetic announcement whose `company` is
   `"Oponeo.pl"` and confirm no tweet in the result contains a `.`-TLD
   substring.
2. Inspect logs from a `post_main` dry run to confirm the new warning line
   fires only when expected and never blocks the approved branch.
3. Read the updated `portfolio-xpost` SKILL.md Step 2.2/3 sections end to
   end to confirm the instructions are actionable as written.

## Performance Considerations

None — a single additional regex substitution per tweet, same cost class
as the existing `_normalize_ticker_spacing`/`_enforce_body_cashtag` calls.

## Migration Notes

None — no data model or stored-data changes; this is pure text
post-processing on newly generated content.

## References

- Frame brief: `context/changes/x-post-domain-guard/frame.md`
- `src/post_generator.py:203-220,408-416` — existing normalizer convention
  and per-tweet loop
- `src/post_supervisor.py:1-81` — `validate_post`, `_ADVICE_RE`,
  `ValidationResult`
- `post_main.py:230-299` — retry loop, all-or-nothing drop behavior
- `tests/test_post_generator.py`, `tests/test_post_supervisor.py` —
  existing test shape to mirror
- `.claude/skills/portfolio-xpost/SKILL.md:130-211` — Step 2.2/3 manual
  validation + human approval gate

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Core sanitizer in post_generator.py

#### Automated

- [x] 1.1 Unit tests for `_strip_domain_suffix` pass — ead7dce
- [x] 1.2 Full `post_generator` suite passes — ead7dce

#### Manual

- [x] 1.4 `generate_post()` with `"Oponeo.pl"` company produces tweets with no `.pl` substring — ead7dce

### Phase 2: Non-blocking observability in post_supervisor.py

#### Automated

- [x] 2.1 New warnings tests pass — 34d6d5e
- [x] 2.2 Full `post_supervisor` suite passes — 34d6d5e
- [x] 2.3 `approved` unaffected by `warnings` (regression test) — 34d6d5e

#### Manual

- [x] 2.4 Warning logged without blocking publication, confirmed via log inspection / dry run

### Phase 3: portfolio-xpost skill documentation

#### Manual

- [x] 3.1 Updated Step 2.2/Step 3 instructions read through and confirmed unambiguous — 6f7e7a7
