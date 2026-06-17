<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio status X-post generator skill

- **Plan**: context/changes/portfolio-xpost-skill/plan.md
- **Scope**: Phase 3 of 4
- **Date**: 2026-06-17
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 4 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | FAIL |

## Findings

### F1 — Documented Automated Verification command selects 0 tests (exit 5)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/portfolio-xpost-skill/plan.md:79 (Progress 3.1)
- **Detail**: The plan's literal Automated Verification command is `pytest tests/test_gemini_client.py -k portfolio`. None of the 6 test function names in tests/test_gemini_client.py contain the literal substring "portfolio" (named test_extract_*). Verified by running the exact command: `6 deselected in 0.83s`, exit code 5 (pytest's "no tests collected" failure). Running the file without -k passes all 6. Progress 3.1 is checked off with commit 8805e44, implying verification happened via the file directly rather than the literal documented command.
- **Fix**: Change the plan's Automated Verification line to `pytest tests/test_gemini_client.py` — the whole file is already portfolio-only, so the `-k portfolio` filter was never needed and currently breaks the documented check.
- **Decision**: FIXED — plan.md Automated Verification (3.1) and Progress line updated to drop the `-k portfolio` filter.

### F2 — Step 2.2 composes tweets via template, not "a text Gemini call" as planned

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: .claude/skills/portfolio-xpost/SKILL.md:117-145
- **Detail**: Plan's Phase 3 contract specified generating thread drafts "via a text Gemini call that also enforces ... char-limit/cashtag-style discipline". The shipped SKILL.md instead composes each tweet from a fixed template using already-extracted data, explicitly stating "Compose each tweet directly (no separate Gemini call needed)". The validation rules themselves (≤280 chars, no truncation, `_ADVICE_RE` reuse, disclaimer-present) are faithfully carried over from `post_supervisor.py:32-80` and applied correctly — so the contract's underlying goal is met, but the literal mechanism diverges from what the plan specified.
- **Fix A ⭐ Recommended**: Document this as an addendum in plan.md — the template-based approach is intentional and arguably safer.
  - Strength: Avoids letting an LLM touch already-validated financial figures a second time; deterministic output for the exact numbers extracted in Step 1. No added latency/cost.
  - Tradeoff: Plan text no longer literally matches what was implemented until the addendum is added.
  - Confidence: HIGH — consistent with this project's broader pattern of keeping LLMs away from final financial figures once extracted.
  - Blind spot: Haven't checked whether future thread styles need more natural-language variety than a fixed template allows.
- **Fix B**: Add a real Gemini text-generation call in Step 2.2 to match the plan's literal wording.
  - Strength: Keeps the plan text accurate without amendment.
  - Tradeoff: Reintroduces non-determinism/latency/cost for text that's already fully determined by Step 1's data — risk of the model paraphrasing or rounding a financial figure, the exact failure class the template approach avoids.
  - Confidence: LOW — likely a regression, not an improvement.
  - Blind spot: None significant — included for completeness only.
- **Decision**: FIXED via Fix A — addendum added to plan.md under Phase 3 item #2 contract documenting the template-based approach and its rationale.

### F3 — Gemini API call itself is unguarded

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/gemini_client.py:119-126
- **Detail**: Only `ValidationError`/`ValueError` (from parsing) are caught. Both existing Gemini call sites (`post_generator.py:392-423`, `analyzer.py:147-184`) also guard the API call itself with a broad except and degrade gracefully. Here, a network error, rate limit, or SDK `ServerError` from `generate_content` propagates unwrapped instead of the project's established `AnalysisError` failure contract — which SKILL.md's Step 1.3 explicitly relies on ("If the command raises (AnalysisError), treat it like a HALT case").
- **Fix**: Wrap the `client.models.generate_content(...)` call itself in `except Exception as exc: raise AnalysisError(...) from exc`, matching the wrapping convention at the other two Gemini call sites.
- **Decision**: FIXED — added `except Exception as exc: raise AnalysisError(...) from exc` after the existing `(ValidationError, ValueError)` clause in `extract_portfolio_snapshot`.

### F4 — Unguarded file read can leak a raw OSError instead of AnalysisError

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/gemini_client.py:111-115
- **Detail**: `open(path, "rb")` in the image-reading loop has no try/except. A screenshot deleted/moved between the skill's Glob and this call, or a 0-byte/corrupt file, raises a raw `FileNotFoundError`/`OSError` — a type the SKILL.md doesn't anticipate (it only documents handling `AnalysisError`).
- **Fix**: Wrap the read loop in try/except OSError and re-raise as AnalysisError, so this failure surfaces through the same documented HALT path as a parse failure.
- **Decision**: FIXED — image-read loop wrapped in `try/except OSError` re-raising as `AnalysisError`.

### F5 — SKILL.md interpolates filenames into `python -c` strings

- **Severity**: 📝 OBSERVATION
- **Dimension**: Safety & Quality
- **Location**: .claude/skills/portfolio-xpost/SKILL.md:39-48, 60-73
- **Detail**: Wallet names are a fixed 4-value enum, but the image paths interpolated into the `python -c "..."` string come from a `Glob` over local filenames, which are arbitrary. A filename containing a quote character would break out of the string. Low real-world risk (XTB-exported screenshots, locally placed by the user), but worth a defensive note.
- **Fix**: Add a note telling the orchestrating agent to verify screenshot filenames contain no quote/backslash characters before interpolating, or pass paths via a temp file/argv instead.
- **Decision**: FIXED — added a "Path safety" note in SKILL.md before the embedded `python -c` snippets in Step 1.

### F6 — Silent mimetype fallback to image/png

- **Severity**: 📝 OBSERVATION
- **Dimension**: Safety & Quality
- **Location**: src/gemini_client.py:113-114
- **Detail**: `mimetypes.guess_type(path)` returns `None` for an unrecognized or missing extension, silently defaulting to `"image/png"`. A misnamed `.jpg`/`.heic` screenshot would be sent to Gemini mislabeled.
- **Fix**: Log a warning when `guess_type` returns `None`, so a misnamed file isn't a silent mystery later.
- **Decision**: FIXED — added `logger.warning(...)` when `mimetypes.guess_type` returns `None`.
