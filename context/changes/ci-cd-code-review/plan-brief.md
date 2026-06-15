# CI/CD AI Code-Review Pipeline — Plan Brief

> Full plan: `context/changes/ci-cd-code-review/plan.md`
> Research: `context/changes/ci-cd-code-review/research.md`

## What & Why

Build the project's first `pull_request` automation: an AI code-review agent that runs on every PR to `master`, scores the diff against 6 project-specific criteria, and turns that score into a **binding merge gate** (PR comment + pass/fail label + commit status). It moves the team from "commits straight to master" toward reviewed PR flow, and encodes hard-won project rules (esp. the BigQuery reserved-keyword + mocked-test blind spot) into a mechanical Definition of Done.

## Starting Point

Clean Python 3.13 / FastAPI / uv monolith with **zero Node infrastructure** and **no PR-triggered CI** (only `deploy.yml` on push to `master`). Gemini is reached via **Vertex AI + ADC** using the existing `puls_gpw_secret` service account — not an API key. A Node/TS package drops in fully isolated from the Python build/test/deploy paths.

## Desired End State

Every PR to `master` gets, within ~2 minutes: a comment with per-criterion scores + verdict, an `ai-cr:passed`/`ai-cr:failed` label, and an `ai-code-review/verdict` commit status that blocks merge when the verdict is `fail` or any criterion scores < 4. `ai-cr:override` bypasses; `ai-cr:review` re-runs; technical failures fail-closed. Runs on Gemini Flash via Vertex — no new vendor, secret, or cash cost.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Gemini auth | Vertex AI + existing `puls_gpw_secret` SA | Only honest "no new secret" path; mirrors prod | Research |
| Agent agency | Pure one-shot scorer (`Output.object`, no tools) | Deterministic, testable side-effects in YAML; lesson's MVP shape | Plan |
| Merge gate | Fail when verdict=fail OR any criterion < 4 | Turns opinion into a real mechanical gate, with override escape hatch | Plan |
| Error handling | Fail-closed + `ai-cr:override` | Lesson's "max rigor"; no PR merges without a real review | Plan |
| Model | Flash only, swappable via `GEMINI_MODEL` | Cheapest on credits; escalation best designed once evals exist | Plan |
| Action hosting | Local `.github/actions/` | Lesson recommends local-first; easy to extract later | Plan |
| promptfoo | Separate follow-up change, **Vertex direct** | Keeps this change shippable; evals on Gemini credits, no new secret | Plan |
| Plan-adherence | Out of scope | Needs Claude Action + `ANTHROPIC_API_KEY` (other vendor/cost) | Plan |

## Scope

**In scope:** Node/TS scorer package, Zod `ReviewResult` schema, system prompt encoding 6 criteria + project conventions, Vertex-authenticated single-shot agent with `stepCountIs` cap, local composite action, `pull_request` workflow with diff sanitization, idempotent comment, labels, and the fail-closed commit-status merge gate.

**Out of scope:** promptfoo evals, plan-adherence review, model auto-escalation to Pro, agent write-tools/agency, any change to `pyproject.toml`/`tach.toml`/`Dockerfile`/`deploy.yml`, separate action repo, committed build artifacts.

## Architecture / Approach

`pull_request` workflow → checkout (`fetch-depth: 0`) → GCP auth (`puls_gpw_secret` → ADC) → compute + sanitize diff to a file → **local composite action** (`setup-node` → `npm ci` → build → run CLI) → agent calls **Gemini Flash via Vertex** once, returns `ReviewResult` JSON → workflow steps post/update the comment, set the label, and POST the `ai-code-review/verdict` commit status (fail-closed, override-bypassable). The agent is a pure scorer; all GitHub side-effects live in deterministic workflow steps.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Scaffold package | Buildable `tools/ai-code-reviewer/` + `ReviewResult` schema + scoped ignores | gitignore collision with Python `dist/` |
| 2. Review agent | Scorer: input/diff sanitization, criteria prompt, Vertex agent, CLI, unit tests | Vertex auth in Node; criteria fidelity |
| 3. Composite action | `action.yml` building + running the package at run time | Build-in-action (no committed `dist/`); SHA-pinning |
| 4. Workflow + gate | First PR workflow, comment, labels, fail-closed merge gate | Live PR behavior; idempotent comment/status; fail-closed correctness |

**Prerequisites:** `puls_gpw_secret` present (it is — used by `deploy.yml`); the four `ai-cr:*` labels are created idempotently by the workflow itself (not a manual step); ability to open a throwaway PR for full end-to-end verification (dispatch alone can't exercise the PR side-effects).
**Estimated effort:** ~3–4 sessions across 4 phases.

## Open Risks & Assumptions

- `@ai-sdk/google-vertex` picks up ADC exported by `google-github-actions/auth@v2` the same way the Python SDK does — verify early in Phase 2/4.
- Large diffs must travel as a **file path**, not inline through `$GITHUB_OUTPUT` (multiline truncation).
- Prompt-injection via PR title/body — mitigated by treating all PR content as untrusted data and gating on structured fields only.
- Without promptfoo (deferred), prompt-quality regressions aren't caught automatically until the follow-up change lands.

## Success Criteria (Summary)

- A throwaway PR produces a comment + correct label + a working merge gate, all on `puls_gpw_secret`/Gemini Flash with no new secret.
- A deliberately bad diff (unbackticked reserved-keyword BQ column) is scored low and **blocks merge**; `ai-cr:override` unblocks it.
- A simulated agent failure fails the gate closed rather than passing silently.
