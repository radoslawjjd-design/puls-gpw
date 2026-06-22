---
change_id: x-post-domain-guard
title: Block domain-like text (e.g. .pl/.com) from generated X posts
status: implemented
created: 2026-06-22
updated: 2026-06-22
last_phase_review: phase-2 (APPROVED)
archived_at: null
tracking:
  linear: PUL-48
  github: 70
---

## Notes

X posts must not contain domain-like text (e.g. company names ending in .pl/.com) that X auto-renders as a link.

Some GPW-listed company names are themselves domain-like strings — e.g. Oponeo.pl (ticker OPN). When that literal company name is inserted into generated tweet text (either the ESPI/EBI pipeline `src/post_generator.py` / `src/post_supervisor.py`, or the portfolio thread composer `src/portfolio_thread_composer.py`), X's own client-side link detection auto-renders `oponeo.pl` (or any `*.com`/`*.pl`/etc. substring) as a clickable hyperlink to that external site. This is unwanted in every case — none of our generated posts should ever contain a clickable external link.

Goal: deterministic guard that detects domain-like substrings (common TLD pattern: `.pl`, `.com`, `.net`, `.org`, `.io`, `.co`, etc.) in generated tweet text, across both pipelines, and prevents the offending text from being published as-is.

Scope:
- New regex-based check (mirrors the existing `_ADVICE_RE` pattern in `src/post_supervisor.py`): something like `\b[\w-]+\.(pl|com|net|org|info|io|co)\b` applied to each tweet's text.
- Main ESPI/EBI pipeline — extend `validate_post()` in `src/post_supervisor.py` to flag any tweet containing a domain-like match (added to `ValidationResult.issues`, same as the existing investment-advice check), so the supervisor retry loop in `post_main.py` regenerates instead of letting it through.
- Portfolio thread composer — apply the same check as part of the `portfolio-xpost` skill's per-tweet validation (Step 2.2), since `post_supervisor.py`'s validator isn't currently invoked there.
- Decide and implement the actual fix applied when a match is found — likely: prefer the company's plain name/ticker over a domain-suffixed legal name where the data source provides one, or break the auto-link by substituting a non-breaking variant of the dot (e.g. a fullwidth `．`) in the offending string so the text stays human-readable but X no longer linkifies it.

Acceptance criteria:
- No tweet published by either pipeline ever renders as a clickable link to an external domain
- A company whose real name is domain-like (e.g. Oponeo.pl) still appears recognizably in the post text, just without triggering X's auto-link
- Regression: existing valid posts (no domain-like text) are unaffected by the new check
