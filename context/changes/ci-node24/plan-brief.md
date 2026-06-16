# Bump GitHub Actions to Node 24-compatible versions — Plan Brief

> Full plan: `context/changes/ci-node24/plan.md`

## What & Why

GitHub forces Node 24 as the default JavaScript-action runtime from 2026-06-16 and removes Node 20 from runners on 2026-09-16. Our CI/CD actions are pinned to Node-20-era majors (deprecation warnings today, hard breakage after September). This change bumps every action across the deploy and AI-review pipelines to its latest Node 24-native major. Preventive maintenance — deploys currently succeed.

## Starting Point

Three files reference actions: `deploy.yml` (tag-pinned `@vN`), `ai-code-review.yml` (SHA-pinned with `# vX.Y.Z` comments, gates PRs via `ai-code-review/verdict`), and the local composite action `.github/actions/ai-reviewer/action.yml` (a hidden Node-20 `setup-node`). `deploy.yml` runs only on push to master.

## Desired End State

Every action resolves to a Node 24-native major; each file keeps its existing pinning style. The post-merge Deploy run and the PR's AI-review run both complete with no Node deprecation warnings.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Scope | Both pipelines (3 files) | ai-code-review runs on every PR — same Node 20 exposure as deploy | Plan |
| Versions | Latest majors (checkout v6, auth v3, setup-gcloud v3, uv v8, setup-node v6) | Longest runway before the next forced bump | Plan |
| Pinning | Keep each file's existing style | Minimal diff; respects each file's security posture | Plan |
| Verification | Merge to master + watch first Deploy run | Tests the real workflow on its real trigger; revert is one line | Plan |

## Scope

**In scope:**
- `deploy.yml`: checkout v4→v6, auth v2→v3, setup-gcloud v2→v3, setup-uv v6→v8 (tag pins)
- `ai-code-review.yml`: checkout→v6.0.3, auth→v3 (SHA pins + comments)
- `.github/actions/ai-reviewer/action.yml`: setup-node v4.4.0→v6.4.0 (SHA pin + comment)

**Out of scope:**
- Converting `deploy.yml` to SHA pins / unifying pin conventions
- Any action-input, job-logic, or Python-version changes
- Adding `workflow_dispatch`; bumping the reviewer SDK/code

## Architecture / Approach

Two phases by pinning style: Phase 1 = four tag bumps in `deploy.yml`; Phase 2 = three SHA bumps across the review pipeline. The delivering PR exercises Phase 2's actions through the AI-review gate; the post-merge Deploy run validates Phase 1's.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. deploy.yml tag bumps | Deploy pipeline on Node 24 | Only verifiable post-merge (master-only trigger) |
| 2. AI-review SHA bumps | Review pipeline + composite action on Node 24 | Cross-two-major bumps (checkout, uv); stale version comments |

**Prerequisites:** `gh` access to re-verify SHAs; branch `pul-36-ci-node24`; ability to merge a PR past the `ai-code-review/verdict` gate.
**Estimated effort:** ~1 session, single PR carrying both phases.

## Open Risks & Assumptions

- Major bumps assumed to change only the bundled Node runtime + irrelevant defaults — re-confirm against release notes if a step errors.
- Deploy acceptance happens on prod's real deploy path; mitigated by a one-line revert and the fact that deploys currently succeed.

## Success Criteria (Summary)

- Post-merge Deploy run on master succeeds with no Node deprecation warnings.
- PR's `ai-code-review/verdict` is green and warning-free.
- No Node-20-era version tags/SHAs remain in any of the three files.
