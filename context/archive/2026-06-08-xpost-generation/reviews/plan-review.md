<!-- PLAN-REVIEW-REPORT -->
# Plan Review: X-post Generation + Email Delivery

- **Plan**: context/changes/xpost-generation/plan.md
- **Mode**: Deep
- **Date**: 2026-06-08
- **Verdict**: REVISE → SOUND (after fixes)
- **Findings**: 2 critical, 4 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

5/5 paths ✓ (src/analyzer.py, src/notifier.py, db/bigquery.py, pyproject.toml, .github/workflows/deploy.yml), 3/3 symbols ✓ (_get_client, save_analysis_result, send_alert), brief↔plan ✓

## Findings

### F1 — Auto-detect window logic wrong for all 3 Scheduler triggers

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness / Blind Spots
- **Location**: Phase 3 — post_main.py auto-detect + Phase 4 Scheduler gcloud commands
- **Detail**: Original boundaries used exclusive `<` causing 08:30 → "poludnie", 13:00 → "wieczor", 17:30 → "ranek" (all wrong). Also, Scheduler triggers had no `--message-body` override so auto-detect would run at job start time (always wrong).
- **Fix A ⭐ Applied**: Changed to inclusive `<=` boundaries (`<= 08:30 → ranek`, `08:31–13:00 → poludnie`, `13:01–17:30 → wieczor`) + added `--message-body` with explicit `--window` arg to all 3 Scheduler gcloud commands.
- **Decision**: FIXED via Fix A

### F2 — Progress section missing Phase 0 Automated item

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: ## Progress ### Phase 0 Automated
- **Detail**: Phase 0 has 2 Automated SC (pytest + json5 smoke-test) but Progress only had item 0.1. Missing 0.2 for json5 import smoke-test. `/10x-implement` would skip the check.
- **Fix**: Added `- [ ] 0.2 json5 import smoke-test exits 0`; renumbered Manual item to 0.3.
- **Decision**: FIXED

### F3 — _get_client() private cross-module import

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 2 — src/post_generator.py contract
- **Detail**: Plan had post_generator.py importing `_get_client()` from `src/analyzer.py` — a private symbol. Any refactor of analyzer.py could silently break the post pipeline.
- **Fix A ⭐ Applied**: Extract Gemini singleton to new `src/gemini_client.py`; both analyzer.py and post_generator.py import from it. Added new Phase 2 Changes Required item 0 (gemini_client.py refactor) and Progress item 2.5.
- **Decision**: FIXED via Fix A

### F4 — structured_analysis JSON parsing missing from generator contract

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 — src/post_generator.py contract
- **Detail**: BQ stores structured_analysis as STRING. Plan mentioned extracting key_numbers/summary_pl from it but didn't specify who parses the string. Implementer would have to guess.
- **Fix**: Added explicit note to contract: `json5.loads(row["structured_analysis"])` inside generate_post, with graceful fallback on parse failure.
- **Decision**: FIXED

### F5 — CI/CD step fails until manual job creation

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 4 — .github/workflows/deploy.yml
- **Detail**: `gcloud run jobs update puls-gpw-post` would fail on every push until the job was manually created.
- **Decision**: DISMISSED — job `puls-gpw-post` already exists in Cloud Run (verified during triage). CI update step will work immediately.

### F6 — supervisor_attempts not persisted on total failure

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 — post_main.py flow
- **Detail**: When all 3 supervisor attempts fail, `save_post_text` was never called — BQ rows would have no record of the failed generation attempt.
- **Fix**: Changed `save_post_text` signature to `post_text: str | None`; added `save_post_text(ann_ids, post_text=None, supervisor_attempts=3)` call before the failure email in post_main.py flow.
- **Decision**: FIXED

## Triage Summary

| ID | Decision |
|----|----------|
| F1 | FIXED via Fix A (inclusive boundaries + Scheduler body override) |
| F2 | FIXED (Progress item 0.2 added, Manual renumbered to 0.3) |
| F3 | FIXED via Fix A (src/gemini_client.py extracted) |
| F4 | FIXED (structured_analysis parsing documented in contract) |
| F5 | DISMISSED (job already exists) |
| F6 | FIXED (Optional post_text + save on failure) |

## Side actions during triage

- `puls-gpw` Cloud Run Job memory upgraded: 512 Mi → 1 Gi (production change, done during triage)
- `puls-gpw-post` Cloud Run Job confirmed to already exist (no provisioning needed for Phase 4 step 2)
- Cloud Scheduler triggers for puls-gpw-post (08:30 / 13:00 / 17:30) do NOT exist yet — to be created in Phase 4 as planned
