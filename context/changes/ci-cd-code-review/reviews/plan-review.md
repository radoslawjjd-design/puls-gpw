<!-- PLAN-REVIEW-REPORT -->
# Plan Review: CI/CD AI Code-Review Pipeline

- **Plan**: `context/changes/ci-cd-code-review/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-15
- **Verdict**: REVISE → SOUND (all findings fixed in plan)
- **Findings**: 0 critical · 3 warnings · 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

7/7 paths ✓ (`deploy.yml`, `gemini_client.py`, `.gitignore`, `lessons.md`, `pyproject.toml`, `tach.toml`, `Dockerfile`), symbols ✓ (`vertexai=True`, `GOOGLE_CLOUD_PROJECT/REGION`, `GEMINI_MODEL`, `puls_gpw_secret`, `branches:[master]`, `.gitignore:dist/` w/o `node_modules`), brief↔plan ✓. No codebase sub-agent spawned — Node-greenfield repo (nothing to blast-radius/pattern-check); riskiest claims are external SDK APIs → captured as F3.

## Findings

### F1 — `workflow_dispatch` has no PR context; side-effect verification can't run there

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4 (triggers + manual verification 4.4–4.9), Overview, Desired End State
- **Detail**: Plan lists `workflow_dispatch` and repeatedly says "verify via workflow_dispatch on a throwaway branch," but dispatch has no `github.event.pull_request.*` / `base_ref` / PR number — diff/comment/label/status all have nothing to act on. The full loop needs a throwaway PR; the two verification stories were conflated.
- **Fix**: Guard PR-dependent steps on `github.event_name == 'pull_request'`; state the full loop is verified via a throwaway PR; fix 4.4–4.9 wording.
- **Decision**: FIXED — guard added to Phase 4 §1 intent/contract; Overview "Verification split" note added; success criteria, Implementation Note, Testing Strategy, and Manual Testing Step 1 reworded (dispatch = agent-only smoke test, throwaway PR = full loop).

### F2 — Labels aren't auto-created; `gh pr edit --add-label` errors on a missing label

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 4 §3 + Migration Notes
- **Detail**: Migration Notes said labels "create on first use," but `gh pr edit --add-label` does not create labels — first run fails unless the four `ai-cr:*` labels exist.
- **Fix**: Add an idempotent `gh label create … --force` step before label-apply; correct Migration Notes + brief.
- **Decision**: FIXED — label-ensure step added to Phase 4 §3 contract; Migration Notes corrected; brief Prerequisites updated.

### F3 — Vercel AI SDK 6 agent API + Vertex ADC grounded only in course material

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 §3 (agent), Critical Implementation Details
- **Detail**: `ToolLoopAgent`, `Output.object`, `stepCountIs`, and `@ai-sdk/google-vertex` ADC pickup are grounded only in m5l3 snippets; no Node code exists to cross-check, and SDK 6 names can shift between versions. If misnamed, all of Phase 2 stalls.
- **Fix**: Add a Phase 2 first sub-step to pin exact versions + verify the API/ADC against current docs (10-line spike) before writing `agent.ts`.
- **Decision**: FIXED — Phase 2 §0 "Verify SDK API surface + Vertex ADC (spike)" added; manual verification bullet + ADC env var (`GOOGLE_APPLICATION_CREDENTIALS`) named in Phase 4 §1.

### F4 — SHA-pinning mandate diverges from the repo's existing `@v2` convention

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 3 §1, Phase 4 success criteria
- **Detail**: Plan mandates SHA-pinning (correct per lesson) but `deploy.yml` uses floating majors — the new workflow is stricter/inconsistent with the repo's only other workflow.
- **Fix**: Document the divergence as a conscious decision (keep stricter standard).
- **Decision**: FIXED (per reviewer recommendation) — Migration Notes now records the pinning divergence as the intentional going-forward standard; `deploy.yml` left unchanged.

### F5 — Minor accuracy nits: comment update, gitignore redundancy, duplicated stripping

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4 §3, Phase 1 §3, Phase 2 §1 / Phase 4 §2
- **Detail**: (a) `gh pr comment` only appends — in-place update needs `gh api` PATCH; (b) added `tools/**/dist/` ignore redundant with existing unanchored `dist/`; (c) diff-stripping duplicated in workflow + package.
- **Fix**: Tighten the three wordings.
- **Decision**: FIXED — (a) Phase 4 §3 now specifies `gh api` list+PATCH on the marker comment; (b) Phase 1 §3 notes the redundancy, only `node_modules/` + `*.tsbuildinfo` required; (c) Phase 4 §2 intent already labels the double-stripping "defense in depth."

## Triage Summary

- **Fixed**: F1, F2, F3, F4, F5 (5)
- Verdict after fixes: **SOUND** — ready for `/10x-implement`.
