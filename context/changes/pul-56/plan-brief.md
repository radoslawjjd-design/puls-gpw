# AI Security Review Pipeline — Plan Brief

> Full plan: `context/changes/pul-56/plan.md`
> Research: `context/changes/pul-56/research.md`

## What & Why

Add a dedicated AI security review gate (PUL-56) that runs on every PR to `master` alongside the existing AI code-review gate (PUL-33). The two gates are intentionally separate: when a PR fails, "security" vs "code quality" is immediately legible from the commit status context name — no guessing which reviewer flagged the issue.

## Starting Point

A complete, working AI code-review pipeline already exists: `.github/workflows/ai-code-review.yml` + `.github/actions/ai-reviewer/` + `tools/ai-code-reviewer/` (Node 22, Vercel AI SDK 6, Gemini via Vertex, pinned deps, 4 test files). This plan mirrors that pattern exactly — the only novel logic is the security-focused prompt and criteria schema.

## Desired End State

Every PR to `master` automatically receives a security review comment with 5 scored criteria (secretsLeakage, injectionRisk, inputValidation, dependencySafety, authPermissions) and a separate `ai-security-review/verdict` commit status. The gate is informational on day 1 (soft-launch); a human promotes it to a required branch protection check after observing false-positive rates in production.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Security criteria (schema.ts) | 5 keys from ticket spec | Matches PUL-56 description exactly; no 6th criterion added | Plan |
| Gate rule | `verdict==pass AND min_score≥4` | Identical to code-review gate — safety net catches cases where model says "pass" but one criterion scores 2/10 | Plan |
| Gemini region | `europe-central2` | Unchanged from existing workflow; global endpoint migration is a separate concern | Plan |
| Soft-launch scope | Non-blocking only | Branch protection update is human-only after stabilization; mirrors how PUL-33 was rolled out | Plan |
| Code sharing | Zero sharing between packages | Independent packages — a bug in one can't cascade into the other | Research |
| Heredoc delimiter | `__AI_SEC_RESULT__` | Avoids collision with `__AI_CR_RESULT__` if both actions ever run in the same job | Plan |

## Scope

**In scope:**
- `tools/ai-security-reviewer/` — full Node.js package (schema, instructions, agent, review, input, 4 tests, package-lock.json)
- `.github/actions/ai-security-reviewer/action.yml` — composite action
- `.github/workflows/ai-security-review.yml` — workflow (non-blocking, informational status check)
- Smoke test via `workflow_dispatch`

**Out of scope:**
- Modifying any existing file
- Adding `ai-security-review/verdict` to GitHub branch protection required checks
- 6th security criterion (transportSecurity)
- Shared modules between the two reviewer packages

## Architecture / Approach

Three-layer stack mirroring the existing code-review pipeline:

```
Workflow YML          ← trigger, guard, diff, labels, comment, status gate
Composite Action      ← build tool package, run, extract verdict/min-score
Node Tool Package     ← Gemini call via Vertex, structured output, JSON5 fallback
```

The only novel logic: `src/schema.ts` (5 security criteria Zod schema) and `src/instructions.ts` (security-focused system prompt with puls-gpw context). Everything else is a near-verbatim copy with string substitutions.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Tool Package | `tools/ai-security-reviewer/` with security schema, prompt, tests passing | instructions.ts prompt quality — hard to test without real Gemini call |
| 2. Composite Action | `.github/actions/ai-security-reviewer/action.yml` | Path substitution errors (wrong `ai-code-reviewer` vs `ai-security-reviewer`) |
| 3. Workflow | `.github/workflows/ai-security-review.yml` (non-blocking) | Label namespace bleed — `ai-cr:*` leaking into new workflow |
| 4. Smoke Test | `workflow_dispatch` end-to-end green, two independent statuses on test PR | None beyond bugs caught in earlier phases |

**Prerequisites:** `GOOGLE_APPLICATION_CREDENTIALS` locally for Phase 1 manual test; pushed branch for Phase 3/4.
**Estimated effort:** ~1 session across 4 phases (phases 2–3 are short; Phase 1 is the bulk of the work)

## Open Risks & Assumptions

- `instructions.ts` prompt quality is the only unjustifiable risk — the LLM's security analysis can't be unit-tested. Mitigation: `instructions.test.ts` asserts key terms are present; smoke test (Phase 4) validates a real Gemini call produces a sensible verdict.
- `min_score≥4` threshold may generate false positives on PRs with no security surface (e.g., pure docs changes scoring `dependencySafety=3`). Mitigated by soft-launch: non-blocking during observation period.

## Success Criteria (Summary)

- `npm test` green on all 4 test files in `tools/ai-security-reviewer/`
- `workflow_dispatch` produces `ai-security-review/verdict` status and `<!-- ai-security-review -->` comment on a test PR without touching the `ai-code-review` gate
- `ai-sec:override` and `ai-sec:review` labels work correctly (bypass + re-run)
