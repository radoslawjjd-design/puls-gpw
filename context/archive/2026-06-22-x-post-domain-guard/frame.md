# Frame Brief: Block domain-like text in generated X posts

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

X's client-side link detection auto-renders domain-like substrings (e.g.
`Oponeo.pl` → `oponeo.pl`) in generated tweet text as a clickable hyperlink
to an external site. This is unwanted in every case, across both the
ESPI/EBI pipeline and the portfolio thread composer.

## Initial Framing (preserved)

- **User's stated cause**: a GPW company's real/legal name is itself
  domain-like (e.g. `Oponeo.pl`), and that literal name gets inserted into
  generated tweet text by either pipeline.
- **User's proposed direction**: add a regex TLD check mirroring `_ADVICE_RE`
  to `validate_post()` in `src/post_supervisor.py` (so the existing
  reject-and-regenerate retry loop in `post_main.py` catches it), apply the
  same check to the portfolio composer's per-tweet validation (Step 2.2 of
  the `portfolio-xpost` skill), then either substitute a non-linkifying
  character or prefer the ticker/plain name when available.
- **Pre-dispatch narrowing**: not run as a separate question round — the
  scope was concrete and the codebase small enough that direct reads of all
  four named files (`post_generator.py`, `post_supervisor.py`, `post_main.py`,
  `portfolio_thread_composer.py`) plus one history check produced conclusive,
  file:line-backed evidence without needing to dispatch parallel hypothesis
  agents or ask the user to disambiguate first.

## Dimension Map

The observation could originate at, or be best addressed at, any of these
dimensions:

1. **Data sourcing** — `src/parser.py:175-201` (`_extract_ticker_company`):
   company name is scraped verbatim from the Bankier profile heading. No
   alternate "short/plain name" field exists anywhere in the pipeline.
2. **LLM generation** — `src/post_generator.py:61-169` (`_SYSTEM_PROMPT`):
   Gemini is instructed to print the literal company name (line 124,
   "📊 Nazwa Spółki"). "Linki w tweetach" is already forbidden (line 159) but
   doesn't stop this, because the model doesn't see a company's own name as
   a link.
3. **Validation/retry loop** — `src/post_supervisor.py:32-80` +
   `post_main.py:271-298` (`_MAX_ATTEMPTS = 3`). ← user's proposed
   enforcement point. This is a reject-and-regenerate-with-feedback loop,
   all-or-nothing across the whole window's thread.
4. **Deterministic post-generation transform** — `src/post_generator.py:203-220`
   (`_enforce_body_cashtag`, `_normalize_ticker_spacing`): existing,
   already-used convention for fixing textual details Gemini gets wrong or
   inconsistent, applied once, after the Gemini call, with no
   reject/regenerate involved.
5. **Portfolio composer** — `src/portfolio_thread_composer.py` +
   `src/gemini_client.py:104-108` (`PortfolioPosition`): only carries
   `ticker`, never a company name.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| (1) Data sourcing is the literal origin point | `parser.py:175-201` confirms `company` is the *only* name field scraped; no alternate plain-name source exists anywhere upstream | STRONG |
| (2) LLM generation is where domain-text enters tweet body | `post_generator.py:124` instructs Gemini to print the full company name; line 159's existing "no links" rule is proven insufficient by the bug itself | STRONG |
| (3) Validator+retry-loop is the right enforcement point (user's framing) | `post_main.py:271-298` is a deliberately all-or-nothing loop (confirmed via `context/archive/2026-06-08-xpost-generation/plan.md:372-395`, commit `e4cf6f4`). Domain-like text, unlike `_ADVICE_RE` phrasing, has **no alternate text the LLM can regenerate toward** (per hypothesis 1) — so every retry fails identically, exhausting all 3 attempts and silently dropping the *entire* multi-company thread via `save_x_post(ann_ids, None, ...)` (line 297). This exact scenario (one company's unfixable violation costing 3 unrelated companies their post) was never analyzed in any prior plan/commit — confirmed by history search. | WEAK as the *sole* mechanism — structurally miscategorizes a literal-text defect as a rephrasable one |
| (4) Deterministic post-generation transform is the right enforcement point | Direct precedent already in the same file (`_enforce_body_cashtag`, `_normalize_ticker_spacing`) for exactly this class of "Gemini got a textual detail wrong, fix it deterministically, no LLM judgment needed" | STRONG |
| (5) Portfolio composer has real exposure to this defect | `compose_portfolio_thread`/`PortfolioPosition` never carry a company name, only `ticker` — structurally cannot produce domain-like text on its own | NONE (aside from the already human-reviewed Step 2.2 "Edytuj" free-text path) |

## Narrowing Signals

- `_ADVICE_RE`-style violations are *phrasing choices* the model made and can
  un-make on retry; a domain-like company name is a *literal fact* about the
  company — there is nothing to regenerate toward. Conflating the two
  defect classes under one validator is the structural error.
- A 2026-06-09 production query (`context/archive/2026-06-09-prompt-review/research.md:346-359`)
  found 100% first-attempt validator approval (25/25) historically — meaning
  this all-or-nothing drop behavior has likely never actually fired in
  production yet, so there's no existing operational tolerance for it being
  exercised by an always-failing check.
- Portfolio composer scope item: confirmed moot by reading the actual
  dataclass, not assumed.

## Cross-System Convention

This project already has an established pattern for "Gemini outputs a
textual detail unreliably, fix it deterministically" — `_enforce_body_cashtag`
and `_normalize_ticker_spacing`, both applied as a post-processing pass in
`generate_post()` before `_enforce_length`. The leading hypothesis (4) matches
this convention exactly. The user's proposed direction (3) does not match any
existing convention for *unfixable* (non-rephrasable) violations — the only
existing reject/retry consumer (`_ADVICE_RE`) is a fundamentally different,
rephrasable defect class.

## Reframed Problem Statement

> **The actual problem to plan around is**: domain-like company names need a
> deterministic text transform — applied inside `generate_post()` after the
> Gemini call, in the same place and style as `_enforce_body_cashtag` /
> `_normalize_ticker_spacing` — not a `validate_post()` rejection rule.

Routing this through the reject-and-regenerate validator (the originally
proposed direction) would silently drop the entire multi-company thread for
that time window every time a domain-like company name appears, because
there is no alternate text the LLM can regenerate toward — unlike the
`_ADVICE_RE` check this mechanism already handles correctly. The portfolio
composer scope item should be dropped from the plan: it has no code-level
exposure to this defect, since it never carries a company name.

## Confidence

**HIGH** — strong file:line evidence at every dimension, the leading
hypothesis matches an existing in-file convention exactly, and a targeted
history search confirms the all-or-nothing retry design was a deliberate
choice for a different (rephrasable) defect class, never evaluated against
an unfixable one.

## What Changes for /10x-plan

- Scope the fix as a deterministic sanitizer in `src/post_generator.py`
  (mirroring `_enforce_body_cashtag`/`_normalize_ticker_spacing`), applied
  to every tweet after the Gemini call — not a `validate_post()` issue.
- A non-blocking, observability-only check inside `validate_post()` may still
  be useful for logging/alerting, but must never be the sole or a blocking
  enforcement mechanism for this defect class.
- Drop the portfolio-composer code-change item entirely; at most, leave a
  note in the `portfolio-xpost` skill doc that the Step 2.2 "Edytuj" free-text
  path is already covered by human review.
- The actual substitution strategy (non-breaking dot vs. ticker-only
  fallback vs. something else) is a solution choice for /10x-plan, not
  decided here.

## References

- `src/parser.py:175-201` — company name scraping, no alternate source
- `src/post_generator.py:61-169,203-220,310-417` — prompt, existing
  deterministic normalizers, generation entry point
- `src/post_supervisor.py:1-81` — `validate_post`, `_ADVICE_RE`
- `post_main.py:230-299` — retry loop, all-or-nothing drop behavior
- `src/portfolio_thread_composer.py`, `src/gemini_client.py:104-119` —
  portfolio composer, `PortfolioPosition` (ticker-only)
- `.claude/skills/portfolio-xpost/SKILL.md:130-211` — Step 2.2/3 manual
  validation + human approval gate
- History check (sub-agent `a277f00bf4c5582c1`): `context/archive/2026-06-08-xpost-generation/plan.md:372-395`
  (commit `e4cf6f4`, all-or-nothing retry design), `context/archive/2026-06-09-prompt-review/research.md:346-359,426-430`
  (100% first-attempt approval historically; single-company-thread possibility noted but never linked to drop risk)
