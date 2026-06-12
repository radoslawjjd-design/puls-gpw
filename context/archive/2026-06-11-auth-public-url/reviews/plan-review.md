<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Auth + Public URL — Implementation Plan

- **Plan**: context/changes/auth-public-url/plan.md
- **Mode**: Deep
- **Date**: 2026-06-12
- **Verdict**: REVISE → SOUND (all findings fixed)
- **Findings**: 1 critical | 3 warnings | 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

7/7 paths ✓, 6/6 symbols ✓ (`delete_announcement:352`, `fetch_top_n_for_window:266`, `_get_client:56`, 17 schema columns, `_mock_bq_client_with_rows` pattern, `json5` in pyproject.toml), brief↔plan ✓

## Findings

### F1 — Real USER_API_KEY value embedded in plan.md

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Phase 5 — CI/CD & Secrets
- **Detail**: plan.md and plan-brief.md contained the literal key value `sGANKdcKo2tYY6PW1t3irMsja7klJ7uqzQ0bs_ggmpg`. Once committed to git it's permanent in history. Violates CLAUDE.md rule: "Secrets live in environment variables only. Never commit them."
- **Fix Applied**: Fix A — replaced both occurrences with `<generate-with: openssl rand -base64 32>` placeholder. The exposed value must be rotated in Secret Manager before go-live.
- **Decision**: FIXED via Fix A

### F2 — Module-level `_UI` read breaks Phase 2 smoke test

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 — FastAPI Application (`src/api.py` contract)
- **Detail**: `_UI = pathlib.Path("static/index.html").read_text(...)` at module level fires at import time. Phase 2's smoke test imports `src.api` before Phase 3 creates the file → `FileNotFoundError`.
- **Fix Applied**: Fix A — plan contract updated to move `read_text` inside `create_app()` body. Import is always safe; file read happens at process start.
- **Decision**: FIXED via Fix A

### F3 — `puls-gpw-runner` SA permission unverified, no mitigation step

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 5 — CI/CD & Secrets (prerequisites)
- **Detail**: Plan acknowledged `roles/run.admin` requirement as unverified with no verification step. First CI push would fail with a gcloud 403 if missing.
- **Fix Applied**: Added Phase 5 step 0 with `gcloud projects get-iam-policy` verification command and a note that granting the role is human-only (per CLAUDE.md infra rules).
- **Decision**: FIXED

### F4 — Phase 3 Progress section missing two Success Criteria entries

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 — Frontend (Progress section)
- **Detail**: Phase 3 Automated had 2 items but Progress tracked only 1 (curl). Phase 3 Manual had 7 items but Progress had 6. Missing: server-start check (automated) and browser form-visible check (manual).
- **Fix Applied**: Added missing entries, renumbered Phase 3 Progress from 3.1–3.7 to 3.1–3.9.
- **Decision**: FIXED
