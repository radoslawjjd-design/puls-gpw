# Block domain-like text in generated X posts — Plan Brief

> Full plan: `context/changes/x-post-domain-guard/plan.md`
> Frame brief: `context/changes/x-post-domain-guard/frame.md`

## What & Why

X's link detection auto-renders domain-like company names (e.g.
`Oponeo.pl`) as clickable hyperlinks in generated tweets — unwanted in
every case. The actual problem to plan around is: this needs a
deterministic text transform applied after tweet generation, not a
`validate_post()` rejection rule, because unlike the existing
investment-advice check there is no alternate text the LLM can regenerate
toward — flagging it as a blocking issue would silently drop the entire
multi-company thread for that window every time it fires.

## Starting Point

`post_generator.py` already has two deterministic post-processing fixups —
`_normalize_ticker_spacing` and `_enforce_body_cashtag` — applied to every
tweet after the Gemini call, for exactly this class of "model gets a
textual detail wrong, fix it deterministically" problem. `validate_post()`
is a reject-and-regenerate gate with a hard 3-attempt limit
(`post_main.py`); exhausting it drops the whole window's thread, not just
one company. The portfolio composer (`portfolio_thread_composer.py`) only
ever carries a ticker, never a company name, so it has no code-level
exposure to this defect.

## Desired End State

No tweet published by either pipeline can ever render as a clickable
external link. A domain-like company name still appears recognizably
(TLD suffix stripped), and existing valid posts are unaffected.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Enforcement point | Deterministic post-generation transform, not `validate_post()` rejection | No alternate text exists for the LLM to regenerate toward; rejecting would drop unrelated companies in the same thread | Frame |
| Substitution strategy | Strip the TLD suffix (`Oponeo.pl` → `Oponeo`) | User choice — simplest, no exotic unicode | Plan |
| Detection scope | Full text of every tweet, not just the company-name field | Also catches domain-like text the model introduces on its own; the "no links" prompt rule is already proven unreliable | Plan |
| Observability | Add non-blocking `ValidationResult.warnings`, logged only | Safety net independent of how a `GeneratedPost` was built (e.g. portfolio skill's manual text), never risks the all-or-nothing drop | Plan |
| Portfolio composer | No code change | Never carries a company name, only a ticker — confirmed by reading `PortfolioPosition` | Frame |
| Portfolio skill doc | Add same sanitizer call to Step 2.2/3 manual checks | Closes the one residual human-text injection path ("Edytuj" free edit) at near-zero cost | Plan |

## Scope

**In scope:**
- `_strip_domain_suffix()` + regex in `post_generator.py`, wired into the
  existing per-tweet post-processing loop
- `ValidationResult.warnings` field + non-blocking log line in `post_main.py`
- `portfolio-xpost` SKILL.md Step 2.2/3 instruction updates

**Out of scope:**
- Any change to `portfolio_thread_composer.py` or `PortfolioPosition`
- Sanitizing the `company` field before it's sent to Gemini in the prompt
- Adding the domain check to `validate_post()`'s blocking `issues` list
- Email content changes to surface `warnings`
- Expanding the TLD list beyond `pl, com, net, org, info, io, co`

## Architecture / Approach

A single regex (`_DOMAIN_TLD_RE`) lives in `post_generator.py` and is
applied to every tweet's text right after `_normalize_ticker_spacing`, by
stripping the matched `.<tld>` suffix. The same regex is imported into
`post_supervisor.py` (the existing import direction — supervisor already
depends on generator) to populate a new, non-blocking `warnings` list on
`ValidationResult`, acting as an independent safety net rather than a
duplicate check. The portfolio-xpost skill reuses the same sanitizer
function via direct import in its manual validation steps, with no
underlying composer code touched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Core sanitizer | `_strip_domain_suffix` wired into `generate_post()` | Regex false-positive on legitimate dotted text (mitigated by `\b` boundaries + unit test table) |
| 2. Observability | Non-blocking `warnings` field + log line | Accidentally coupling it to `approved` (mitigated by explicit regression test) |
| 3. Skill doc | Step 2.2/3 instructions reuse the sanitizer | Documentation-only — risk is low, mainly clarity of instructions |

**Prerequisites:** None — builds entirely on existing code paths and conventions.
**Estimated effort:** ~1 session across 3 phases (single-file core change + small doc update).

## Open Risks & Assumptions

- Assumes the TLD list (`pl, com, net, org, info, io, co`) covers the
  realistic domain-suffix space for GPW-listed company names; easy to
  extend later if a gap surfaces (no architecture change needed).
- Assumes stripping the TLD suffix (vs. e.g. a fullwidth-dot swap) is an
  acceptable visual change to the company's printed name — confirmed by
  user during planning, not re-litigated here.

## Success Criteria (Summary)

- No generated tweet, in either pipeline, contains a substring X would
  auto-linkify.
- A domain-like company name still reads naturally in the post.
- All existing `post_generator`/`post_supervisor` tests continue to pass.
