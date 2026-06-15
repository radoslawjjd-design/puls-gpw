# CI/CD AI Code-Review Pipeline Implementation Plan

## Overview

Introduce the project's **first `pull_request` automation**: a standalone Node/TS code-review agent (Vercel AI SDK 6 + Gemini via **Vertex AI**) wrapped in a **local composite action** and driven by a GitHub Actions workflow that runs on every PR to `master`. The agent scores the diff against 6 project-specific criteria, the workflow posts a PR comment + `ai-cr:passed`/`ai-cr:failed` label, and a **hard merge gate** fails the check when the verdict is `fail` or any single criterion scores below 4 — bypassable only via an explicit override label.

The agent is a **pure one-shot scorer**: it takes `{pr-title, pr-body, diff}`, returns a structured `ReviewResult` (per-criterion scores + binding verdict + Markdown summary), and does nothing else. All side-effects (comment, labels, gate, error handling) live in the workflow, in deterministic, testable steps.

## Current State Analysis

- **Zero Node infrastructure** in the repo (clean Python 3.13 / FastAPI / uv monolith). No `package.json`, `tsconfig.json`, `.nvmrc`, lockfile, or `tools/`/`packages/` dir. A Node/TS package drops in fully isolated from the Python build/test/deploy paths (`pyproject.toml`, `tach.toml`, `Dockerfile` untouched). — research §1.
- **CI today** is a single `deploy.yml` triggered **only on push to `master`** (no PR gate exists). GCP auth uses `google-github-actions/auth@v2` with `credentials_json: ${{ secrets.puls_gpw_secret }}` (a service-account JSON). Runner `ubuntu-latest`; `PROJECT_ID: puls-gpw`, `REGION: europe-central2`. — research §2, `.github/workflows/deploy.yml:3-5,20-23`.
- **Gemini auth reality:** the project authenticates to Gemini via **Vertex AI + ADC** (`genai.Client(vertexai=True, …)` in `src/gemini_client.py:21-25`), **not** a Gemini API key. `GEMINI_API_KEY` in `.env.example` is legacy/unused. The agent must therefore authenticate to **Vertex AI Gemini with the existing `puls_gpw_secret` SA** via `@ai-sdk/google-vertex` — reuses an existing credential, same trust boundary, **no new secret**. — research §3.
- **`.gitignore`** does not yet ignore `node_modules/`; it has a top-level `dist/` rule (Python) that must not be confused with the Node package's `dist/`. — research §1.
- **Project conventions** the review criteria must encode (all file-grounded): json5-tolerant parsing of model JSON, secrets-in-env-only, **human-only destructive infra**, and the **BigQuery reserved-keyword + mocked-test blind spot** (PUL-29's `x_posts.window` bug passed all mocked unit tests, surfaced only on a real round-trip — the single sharpest project-specific check). — research §4, `context/foundation/lessons.md:211-236`.

## Desired End State

When this plan is complete:

- Opening or updating any PR to `master` triggers **AI Code Review**. Within a couple of minutes the PR shows: a comment with a Markdown summary + per-criterion scores (1–10) + verdict, an `ai-cr:passed` (green) or `ai-cr:failed` (red) label, and a commit status `ai-code-review/verdict`.
- The status is a **real merge gate**: red when verdict = `fail` or any criterion < 4; green otherwise. Adding the `ai-cr:override` label turns a red gate green.
- Adding the `ai-cr:review` label re-runs the review on demand. `workflow_dispatch` runs it manually for testing.
- A technical failure of the agent (Vertex down, timeout, malformed output) is **fail-closed**: the gate goes red with a clear "review failed technically" message, bypassable via the override label.
- The agent runs on **Gemini Flash via Vertex** (model swappable through one env var), consuming existing GCP credits — no new vendor, no new secret, no extra cash cost.

**Verification:** `workflow_dispatch` on a throwaway branch produces the comment + label + status; a deliberately bad diff (e.g. an unbackticked reserved-keyword BQ column) is scored low and blocked; the override label unblocks it.

### Key Discoveries:

- Vertex auth via existing SA is the only honest "no new secret" path (`src/gemini_client.py:21-25`; research §3).
- The reusable `10x-impl-review-ci` workflow template (`.claude/skills/10x-impl-review-ci/references/workflow-template.yml`) is the reference pattern for: manual commit-status gate, `if: always()` verdict re-evaluation, override-label bypass, fork-PR guard (`head.repo.full_name == github.repository`), and concurrency grouping per PR. We mirror its **gate mechanics** but NOT its Claude-Action runtime.
- The lesson (m5l3) prescribes: composite action with `using: composite`, every `run` step needs explicit `shell`, `${{ github.action_path }}` to locate scripts, pin third-party actions to `@<sha>`, `fetch-depth: 0` for the diff, and `stopWhen: stepCountIs(n)` as a hard cost bound.
- The lesson's `git diff ... >> $GITHUB_OUTPUT` is fragile for large/multiline diffs — we write the diff to a **file** and pass the path, not the content, through `inputs`.

## What We're NOT Doing

- **No promptfoo eval suite** in this change — deferred to its own follow-up change (`code-review-evals`), which will use **Vertex direct** (Gemini Flash vs Pro on existing credits, no new secret). Decision locked here for that change.
- **No plan-adherence review** (`/10x-impl-review-ci`) — it requires Claude Code Action + `ANTHROPIC_API_KEY` (different vendor, real cost), conflicting with the Gemini-only/no-cost constraint. Parked.
- **No model auto-escalation to Pro** — Flash only, swappable via `GEMINI_MODEL`. Escalation heuristics are out of scope (and best designed once evals exist).
- **No agent agency / write-tools** — the agent is a pure scorer; it does not call the GitHub API. No `tools` array beyond the implicit `Output.object`.
- **No changes** to `pyproject.toml`, `tach.toml`, `Dockerfile`, or the Python app. The Node package is not co-deployed into the app image.
- **No separate action repo** — local `.github/actions/` only.
- **No committed build artifacts** — `dist/` is built inside the action, never committed.

## Implementation Approach

Four phases, each independently verifiable. Phases 1–2 build and unit-test the Node package locally (no CI needed). Phase 3 wraps it as a composite action. Phase 4 wires the workflow, side-effects, and gate — the only phase that exercises the live PR flow, verified via `workflow_dispatch` on a throwaway branch before relying on it.

The agent is deliberately a **single-shot scorer** (`ToolLoopAgent` with `Output.object`, no tools, `stepCountIs` cap) — the lesson's MVP shape. This keeps side-effects in deterministic YAML/bash that can be reasoned about and re-run, and leaves a clean upgrade path (add `tools` later) without re-architecting.

## Critical Implementation Details

- **Build location.** The composite action must `npm ci && npm run build` at run time (it has `setup-node` + install + build steps, then `node ${{ github.action_path }}/dist/review.js`). `dist/` is gitignored and never committed — so the action cannot assume a prebuilt `dist/` like the lesson's snippet does. This is the one place the lesson's example diverges from our gitignore decision.
- **Diff transport.** Compute the diff on the runner into a **file** (`git diff origin/<base>...HEAD`), strip generated artifacts (`uv.lock`, `tach_module_graph.dot`, and any `*.lock`) before the agent reads it, and pass the **file path** through `inputs`, not the content — GitHub Action `inputs`/`$GITHUB_OUTPUT` mishandle large multiline diffs. The agent reads the file.
- **Untrusted inputs.** `pr-title`/`pr-body` are attacker-controllable (prompt-injection surface). The system prompt must instruct the model to treat title/body/diff as untrusted data to review, never as instructions to obey, and the gate relies on the structured `score`/`verdict` fields — not on free-text the model echoes.
- **Fork-PR guard.** Fork PRs can't access repo secrets and shouldn't receive commits-back; gate the job on `github.event.pull_request.head.repo.full_name == github.repository` (mirrors the impl-review-ci template).
- **Idempotent comment.** Re-runs (label `ai-cr:review`, new pushes) must **update** a single marker-tagged comment rather than spawn duplicates; the commit-status `context` string is stable (`ai-code-review/verdict`) so later runs update the same status.

---

## Phase 1: Scaffold the `tools/ai-code-reviewer/` package

### Overview

Stand up an isolated, buildable Node/TS package with the result schema, dependencies, and scoped ignores — no agent logic yet. Establishes a green `npm run build` checkpoint.

### Changes Required:

#### 1. Package manifest + toolchain

**File**: `tools/ai-code-reviewer/package.json`, `tools/ai-code-reviewer/tsconfig.json`, `tools/ai-code-reviewer/.nvmrc`

**Intent**: Create a self-contained TS package (ESM, Node 22 LTS) with a `build` script (tsc → `dist/`) and a `test` script. Deps: `ai` (Vercel AI SDK 6), `@ai-sdk/google-vertex`, `zod`, `json5`. Dev: `typescript`, `vitest` (or `node:test`), `@types/node`. `.nvmrc` pins the Node version the workflow's `setup-node` reads.

**Contract**: `package.json` exposes `scripts.build`, `scripts.test`; `"type": "module"`; bin/entry at `dist/review.js`. `tsconfig` targets ES2022, `outDir: dist`, `strict: true`.

#### 2. Result schema

**File**: `tools/ai-code-reviewer/src/schema.ts`

**Intent**: Define the Zod `ReviewResult` that `Output.object` enforces — the contract the whole gate stands on. Six criteria each scored 1–10, a binding boolean/enum verdict, and a Markdown summary usable directly as the PR comment.

**Contract**: `ReviewResult = { scores: { correctness, idiomaticity, complexity, testCoverageVsRisk, security, dataInfraSafety }: int 1–10, verdict: "pass" | "fail", summary: string (markdown) }`. Exported type + schema. Criterion keys map 1:1 to `requirements.md` criteria 1–6.

#### 3. Scoped ignores

**File**: `.gitignore`

**Intent**: Add the Node ignores the repo lacks. Note: the existing `.gitignore` line 5 `dist/` is **unanchored**, so it already matches `tools/ai-code-reviewer/dist/` at any depth — the only genuinely-missing ignores are `node_modules/` and `*.tsbuildinfo`.

**Contract**: Append `tools/**/node_modules/` and `tools/**/*.tsbuildinfo`. A scoped `tools/**/dist/` line is optional (redundant with the existing `dist/` rule) — add only for explicitness, not necessity.

### Success Criteria:

#### Automated Verification:

- Dependencies install: `cd tools/ai-code-reviewer && npm ci`
- Build passes: `cd tools/ai-code-reviewer && npm run build`
- Type checking passes: `cd tools/ai-code-reviewer && npx tsc --noEmit`
- `git status` shows no `node_modules/` or `dist/` tracked

#### Manual Verification:

- `ReviewResult` schema's six criteria correspond exactly to `requirements.md` criteria 1–6
- No change to `pyproject.toml`, `tach.toml`, or `Dockerfile`

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 2.

---

## Phase 2: Build the review agent (scorer)

### Overview

Implement the single-shot scorer: input builder, the system prompt encoding the 6 criteria + project conventions, the Vertex-authenticated `ToolLoopAgent` with `Output.object` + step cap, json5-tolerant handling, and a CLI entry. Unit-tested without hitting the network.

### Changes Required:

#### 0. Verify SDK API surface + Vertex ADC (spike — do this first)

**File**: throwaway `tools/ai-code-reviewer/spike.ts` (deleted after) + `package.json` pinned versions

**Intent**: De-risk the whole phase before writing real code. The agent API (`ToolLoopAgent`, `Output.object`, `stopWhen: stepCountIs`) and the `@ai-sdk/google-vertex` ADC behavior are grounded **only** in the m5l3 course material — no Node code exists in this repo to cross-check, and SDK 6 class/method names can shift between versions. Confirm them against current docs before committing to `agent.ts`.

**Contract**: Pin exact `ai` and `@ai-sdk/google-vertex` versions in `package.json`. Verify against current docs (Context7 / web): the real class/method names for a single-shot structured-output agent, and the env var `@ai-sdk/google-vertex` reads for ADC (expected `GOOGLE_APPLICATION_CREDENTIALS`, which `google-github-actions/auth@v2` exports). A ~10-line spike making one real Vertex Gemini call that returns an `Output.object`-shaped result confirms both auth and API before the phase proceeds. If the API names differ from the lesson, update §3's contract to match the installed version.

#### 1. Input builder + diff sanitization

**File**: `tools/ai-code-reviewer/src/input.ts`

**Intent**: Read `{pr-title, pr-body, diff-file path}` from CLI args/env, load the diff from file, strip generated artifacts, and assemble the user prompt. Wrap untrusted fields explicitly as data-to-review.

**Contract**: `buildReviewPrompt({ title, body, diffPath }) -> string`. Strips hunks for paths matching `uv.lock`, `tach_module_graph.dot`, `*.lock`. Title/body fenced and labelled untrusted.

#### 2. System prompt (review criteria + conventions)

**File**: `tools/ai-code-reviewer/src/instructions.ts`

**Intent**: The domain knowledge — the 6 scored criteria with their 1-vs-10 anchors from `requirements.md`, plus the project-specific checks an automated reviewer must encode. Must instruct the model to treat all PR content as untrusted.

**Contract**: Exported `REVIEW_INSTRUCTIONS` string. Must explicitly encode, at minimum: BigQuery reserved-keyword columns (`window`, `range`, …) backticked in hand-built SQL **and** that mocked BQ tests don't prove SQL syntax (criterion 6 / test-vs-risk); `json5.loads` (not stdlib `json.loads`) on Gemini responses; secrets in env only; **destructive infra (drop BQ table / delete Cloud Run job / rotate secret) is human-only, never automated**; uv-only, Python 3.13 type annotations, Conventional Commits. Anchored in `requirements.md` §"Code Review Criteria" + research §4.

#### 3. Agent + Vertex provider

**File**: `tools/ai-code-reviewer/src/agent.ts`

**Intent**: Construct the Vertex Gemini provider (project/location from env, ADC from the SA the workflow sets up), build a `ToolLoopAgent` with `Output.object({ schema: ReviewResult })`, no tools, and a hard `stopWhen: stepCountIs(8)` cost bound (the lesson's recommended review-session cap; the no-tool scorer finishes in 1 step, so this is a safety net). Model id from `GEMINI_MODEL` (default `gemini-2.5-flash`), read once.

**Contract**: `runReview(input) -> ReviewResult`. Vertex provider via `@ai-sdk/google-vertex` using `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_REGION` (default `europe-central2`, mirroring `src/gemini_client.py`). `GEMINI_MODEL` default = `gemini-2.5-flash` (full Flash, not flash-lite — code review needs more reasoning than the app's news classification; swappable via env). `stopWhen: stepCountIs(8)`. Defensive json5-tolerant fallback if structured output returns text.

#### 4. CLI entry

**File**: `tools/ai-code-reviewer/src/review.ts` (compiles to `dist/review.js`)

**Intent**: Glue — parse inputs, run the agent, emit the `ReviewResult` as JSON to stdout (and/or `$GITHUB_OUTPUT`-friendly form) for the action to capture. Non-zero exit only on technical failure (so the workflow can fail-closed), never on a `fail` verdict (that's the gate's job downstream).

**Contract**: Reads env (`PR_TITLE`, `PR_BODY`, `DIFF_PATH`, `GEMINI_MODEL`, GCP envs); writes JSON `ReviewResult` to stdout; exit 0 on a produced verdict (pass or fail), exit non-zero only on agent/infra error.

#### 5. Unit tests

**File**: `tools/ai-code-reviewer/test/*.test.ts`

**Intent**: Test the pure, deterministic surfaces without network: diff sanitization strips the right artifacts; prompt builder fences untrusted input; schema validation accepts a well-formed result and rejects out-of-range scores; the instructions string contains the load-bearing project checks (BQ reserved-keyword, json5, human-only infra). Mock the model call to assert wiring (model id, step cap), not Gemini itself.

**Contract**: `npm test` green. Coverage targets `input.ts`, `schema.ts`, and instruction-content assertions. The agent's network call is mocked.

### Success Criteria:

#### Automated Verification:

- Build passes: `cd tools/ai-code-reviewer && npm run build`
- Unit tests pass: `cd tools/ai-code-reviewer && npm test`
- Type checking passes: `npx tsc --noEmit`
- Lockfile-stripping test asserts `uv.lock`/`*.lock` hunks removed

#### Manual Verification:

- Spike confirmed the SDK agent API + Vertex ADC env var against current docs; `package.json` versions pinned
- A real `git diff` piped through the CLI locally (with ADC for Vertex) returns a well-formed `ReviewResult`
- A deliberately bad diff (unbackticked reserved-keyword BQ column) scores `dataInfraSafety` low and trends toward `fail`
- The summary field reads cleanly as a standalone PR comment

**Implementation Note**: After automated verification passes, pause for human confirmation (the manual Vertex round-trip needs the human's ADC) before Phase 3.

---

## Phase 3: Composite action wrapper

### Overview

Wrap the package as a local composite action so the consumer workflow stays a one-liner and the action is reusable/extractable later. The action builds the package at run time (no committed `dist/`).

### Changes Required:

#### 1. Composite action definition

**File**: `.github/actions/ai-reviewer/action.yml`

**Intent**: Define a `using: composite` action that sets up Node (from `.nvmrc`), installs + builds the package, runs the CLI with inputs mapped to env, and exposes the verdict/scores as outputs for the gate step.

**Contract**: `inputs`: `pr-title`, `pr-body`, `diff-path`, `model` (optional, default Flash). `outputs`: `result` (full JSON), `verdict`, `min-score`. `runs.using: composite`; steps use `actions/setup-node@<sha>` with `node-version-file`, then `npm ci`, `npm run build`, then `node ${{ github.action_path }}/dist/review.js`. Every `run` step declares `shell: bash`. GCP/Vertex env (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_REGION`) passed through; ADC comes from the workflow's auth step (not an action input). Third-party actions pinned to `@<sha>`.

**Contract (snippet — output extraction is the non-obvious part the gate depends on):**
```yaml
# inside the run step that invokes the CLI, after capturing JSON into $RESULT:
echo "verdict=$(echo "$RESULT" | node -e 'process.stdin.on("data",d=>console.log(JSON.parse(d).verdict))')" >> "$GITHUB_OUTPUT"
echo "min-score=$(echo "$RESULT" | node -e 'process.stdin.on("data",d=>{const s=JSON.parse(d).scores;console.log(Math.min(...Object.values(s)))})')" >> "$GITHUB_OUTPUT"
# full result written to a file path output to avoid multiline-in-$GITHUB_OUTPUT issues
```

### Success Criteria:

#### Automated Verification:

- `action.yml` parses: `cd .github/actions/ai-reviewer && npx js-yaml action.yml` (or any YAML lint)
- `actionlint` passes on the action (if available)
- Referenced third-party actions are SHA-pinned (grep shows no `@v` floating tags)

#### Manual Verification:

- `using: composite`, every `run` step has explicit `shell: bash`
- Action builds `dist/` at run time and does not assume a committed `dist/`
- Inputs cover `pr-title`/`pr-body`/`diff-path`/`model`; outputs cover `verdict`/`min-score`/`result`

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 4.

---

## Phase 4: Workflow, side-effects, and merge gate

### Overview

The only phase exercising the live PR flow. Add the first `pull_request` workflow: trigger, GCP auth, diff computation, action call, then deterministic side-effect steps (comment, label, commit-status gate) with fail-closed error handling.

**Verification split (important):** `workflow_dispatch` has **no PR context** — no `github.event.pull_request.*`, no `base_ref`, no PR number — so it can only exercise the agent/diff/JSON in isolation, NOT the comment/label/gate (which all need a PR). The full side-effect + gate loop must be verified on a **throwaway PR**. All PR-dependent steps must be guarded so a dispatch run doesn't error on empty PR fields.

### Changes Required:

#### 1. Workflow file

**File**: `.github/workflows/ai-code-review.yml`

**Intent**: Trigger on `pull_request: [master]` (`types: [opened, synchronize, reopened, labeled]`), `workflow_dispatch`, and label `ai-cr:review`. Checkout with `fetch-depth: 0`, authenticate to GCP via the existing SA, compute + sanitize the diff to a file, call the composite action, then run the side-effect/gate steps. **Guard all PR-dependent steps (diff-vs-base, comment, label, status) on `github.event_name == 'pull_request'`** so a `workflow_dispatch` run exercises only the agent and exits cleanly without touching PR APIs. Guard against fork PRs and recursion; group concurrency per PR.

**Contract**: `permissions:` minimal at top, elevated in-job to `pull-requests: write` (comment + labels), `statuses: write` (commit status), `contents: read`. GCP auth: `google-github-actions/auth@<sha>` with `credentials_json: ${{ secrets.puls_gpw_secret }}`, exporting ADC (`GOOGLE_APPLICATION_CREDENTIALS`) for `@ai-sdk/google-vertex`. Env `GOOGLE_CLOUD_PROJECT: puls-gpw`, `GOOGLE_CLOUD_REGION: europe-central2`. Fork guard `head.repo.full_name == github.repository`. `concurrency: ai-code-review-${{ github.event.pull_request.number }}` with `cancel-in-progress`. Diff step (PR only): `git diff origin/${{ github.base_ref }}...HEAD` to a file. PR title/body from `github.event.pull_request.*`. On `workflow_dispatch`, title/body/diff come from a small fixture or dispatch inputs — the run stops after producing the JSON (no PR side-effects).

#### 2. Diff computation + sanitization step

**File**: `.github/workflows/ai-code-review.yml` (step)

**Intent**: Produce a sanitized diff file the action consumes; strip lockfiles/generated artifacts at the workflow level too (defense in depth with the package's stripping).

**Contract**: Writes `diff.txt`; excludes `uv.lock`, `tach_module_graph.dot`, `*.lock` (e.g. `git diff ... -- . ':(exclude)uv.lock' ':(exclude)*.lock'`). Passes the path as `diff-path` input.

#### 3. Side-effects: comment + labels

**File**: `.github/workflows/ai-code-review.yml` (steps)

**Intent**: Ensure the labels exist, post/update a single marker-tagged PR comment with the agent's `summary` + a per-criterion score table, and set exactly one of `ai-cr:passed` / `ai-cr:failed` (removing the other).

**Contract**: First an idempotent label-ensure step — `gh label create <name> --color <hex> --force` for all four (`ai-cr:passed`, `ai-cr:failed`, `ai-cr:review`, `ai-cr:override`) — because `gh pr edit --add-label` **errors on a missing label** (it does not auto-create). Then the comment: in-place update is **not** `gh pr comment` (which only appends) — list comments via `gh api repos/{owner}/{repo}/issues/{n}/comments`, find the one bearing the HTML marker, and `PATCH` it (or POST if absent). Labels applied via `gh pr edit --add-label/--remove-label`. Comment driven by the action's `result` output.

#### 4. Merge gate (commit status)

**File**: `.github/workflows/ai-code-review.yml` (step, `if: always()`)

**Intent**: POST a commit status `ai-code-review/verdict` to HEAD: success when verdict=`pass` AND `min-score >= 4`; failure when verdict=`fail` OR `min-score < 4`; override label `ai-cr:override` forces success. Mirrors the impl-review-ci verdict-check pattern.

**Contract**: Stable status `context: "ai-code-review/verdict"`. `gh api --method POST repos/{owner}/{repo}/statuses/{sha}`. Override via `gh pr view --json labels` check for `ai-cr:override`. Runs `if: always()` so it re-evaluates on label-only re-triggers.

#### 5. Fail-closed error handling

**File**: `.github/workflows/ai-code-review.yml` (gate step logic)

**Intent**: If the action step failed (Vertex down, timeout, malformed output → CLI non-zero / missing outputs), post a `failure` status with a "review failed technically — re-run with `ai-cr:review`" message instead of silently passing. Override label still bypasses.

**Contract**: Detect missing/empty `verdict` output or failed action step → `post_status failure "AI review failed technically — re-run via ai-cr:review label"`, unless `ai-cr:override` present. The gate step is `if: always()` and reads the action's `outcome`.

### Success Criteria:

#### Automated Verification:

- Workflow lints: `actionlint .github/workflows/ai-code-review.yml`
- No floating action tags: grep shows third-party `uses:` pinned to `@<sha>`
- YAML parses cleanly

#### Manual Verification:

- `workflow_dispatch` (no PR) runs the agent on a fixture/inputs and produces a well-formed JSON result, exiting cleanly without touching PR APIs
- A **throwaway PR** produces: a PR comment, one `ai-cr:passed`/`ai-cr:failed` label, and the `ai-code-review/verdict` status
- A deliberately bad diff (unbackticked reserved-keyword BQ column) → `ai-cr:failed` + red gate
- Adding `ai-cr:override` flips the red gate to green
- Adding `ai-cr:review` re-runs the review and **updates** (not duplicates) the comment + status
- Simulated agent failure (e.g. bad model id) → red gate with the "failed technically" message, not a silent pass
- Run consumes Gemini Flash on Vertex — no new secret used, `puls_gpw_secret` is the only credential

**Implementation Note**: This phase changes externally visible PR behavior. The agent-only path is verified via `workflow_dispatch`; the full comment + label + gate loop is verified on a **throwaway PR** (dispatch has no PR context). Verify both before announcing the gate as binding. Pause for human confirmation.

---

## Testing Strategy

### Unit Tests (Phase 2):

- Diff sanitization strips `uv.lock`, `tach_module_graph.dot`, `*.lock`
- Prompt builder fences untrusted `pr-title`/`pr-body`
- `ReviewResult` schema accepts well-formed results, rejects out-of-range scores
- `REVIEW_INSTRUCTIONS` contains the load-bearing project checks (BQ reserved-keyword + mocked-test blind spot, json5, human-only infra)
- Agent wiring (model id from env, `stepCountIs` cap) asserted with the model call mocked

### Integration / Manual:

- Local CLI round-trip against Vertex (human ADC) returns a valid `ReviewResult`
- Agent-only run via `workflow_dispatch` (no PR) produces valid JSON and exits without PR side-effects
- End-to-end on a **throwaway PR** (the full comment + label + gate loop)
- Adversarial cases: reserved-keyword BQ column (low `dataInfraSafety`), heavily-mocked test of a risky path (low `testCoverageVsRisk`), a planted secret (low `security`)

### Manual Testing Steps:

1. Open a **throwaway PR** with a small clean diff → expect `ai-cr:passed` + green gate. (Use `workflow_dispatch` separately to smoke-test the agent on a fixture without a PR.)
2. Push a diff with an unbackticked `window` BQ column → expect `ai-cr:failed` + red gate, summary names the issue.
3. Add `ai-cr:override` → gate goes green.
4. Add `ai-cr:review` → review re-runs, comment + status update in place (no duplicates).
5. Set an invalid `GEMINI_MODEL` to simulate failure → expect red gate with "failed technically".

## Performance Considerations

- Single-shot agent with `stepCountIs` cap bounds tokens/latency per PR; Flash keeps cost (credit consumption) minimal. The cap is the primary runaway-cost guard (lesson §"Sprawczość pod kontrolą kosztów").
- `npm ci` + build inside the action adds ~30–60s per run; acceptable for a per-PR gate. Optional later optimization: cache `node_modules` or prebuild — not required for correctness.
- Concurrency group per PR with `cancel-in-progress` avoids stacked runs on rapid pushes.

## Migration Notes

- This is the repo's **first PR-triggered workflow**; it does not touch `deploy.yml` (push-to-master deploy) and adds no PR gate to deployment. Adopting PR-flow is a team-process change tracked separately.
- Labels `ai-cr:passed`, `ai-cr:failed`, `ai-cr:review`, `ai-cr:override` must exist before the first label-apply. They are **not** auto-created by `gh pr edit --add-label`; the workflow's label-ensure step (`gh label create … --force`, Phase 4 §3) creates them idempotently. Document them in the workflow header.
- **Action-pinning divergence (conscious decision):** this workflow pins third-party actions to `@<sha>` (per the m5l3 security guidance), whereas the existing `deploy.yml` uses floating majors (`auth@v2`, etc.). The new workflow is intentionally the stricter going-forward standard; `deploy.yml` is left unchanged for now. Revisit `deploy.yml` pinning separately if desired.
- **Credential-dump guard (Phase 1, beyond plan §1.3):** an exported Cloud Run service-account key (`puls-gpw-api-*.json`) was found untracked at repo root during Phase 1. A `.gitignore` rule `puls-gpw-api-*.json` was added so the export can never be staged. File left on disk (human-owned); the rule is the only repo change. Recorded here for plan-vs-actual transparency.

## References

- Requirements: `context/changes/ci-cd-code-review/requirements.md`
- Research: `context/changes/ci-cd-code-review/research.md`
- Gate-mechanics reference: `.claude/skills/10x-impl-review-ci/references/workflow-template.yml`
- Source lesson (m5l3): `lessonMarkdawn/18. code-review-w-erze-ai-standardy-dod-i-agent-w-pipeline.md`
- Vertex auth pattern to mirror: `src/gemini_client.py:16-27`
- Existing CI to mirror: `.github/workflows/deploy.yml:3-5,20-23`
- BQ blind-spot lesson: `context/foundation/lessons.md:211-236`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Scaffold the `tools/ai-code-reviewer/` package

#### Automated

- [x] 1.1 Dependencies install (`npm ci`) — 72a7153
- [x] 1.2 Build passes (`npm run build`) — 72a7153
- [x] 1.3 Type checking passes (`tsc --noEmit`) — 72a7153
- [x] 1.4 No `node_modules/`/`dist/` tracked by git — 72a7153

#### Manual

- [x] 1.5 `ReviewResult` six criteria map 1:1 to `requirements.md` criteria 1–6 — 72a7153
- [x] 1.6 No change to `pyproject.toml`, `tach.toml`, `Dockerfile` — 72a7153

### Phase 2: Build the review agent (scorer)

#### Automated

- [ ] 2.1 Build passes (`npm run build`)
- [ ] 2.2 Unit tests pass (`npm test`)
- [ ] 2.3 Type checking passes (`tsc --noEmit`)
- [ ] 2.4 Lockfile-stripping test asserts `uv.lock`/`*.lock` removed

#### Manual

- [ ] 2.5 Local CLI round-trip against Vertex returns a valid `ReviewResult`
- [ ] 2.6 Bad diff (unbackticked reserved-keyword BQ column) scores `dataInfraSafety` low, trends `fail`
- [ ] 2.7 Summary reads cleanly as a standalone PR comment

### Phase 3: Composite action wrapper

#### Automated

- [ ] 3.1 `action.yml` parses (YAML lint)
- [ ] 3.2 `actionlint` passes (if available)
- [ ] 3.3 Third-party actions SHA-pinned (no floating `@v` tags)

#### Manual

- [ ] 3.4 `using: composite`, every `run` step has explicit `shell: bash`
- [ ] 3.5 Action builds `dist/` at run time (no committed `dist/`)
- [ ] 3.6 Inputs cover title/body/diff-path/model; outputs cover verdict/min-score/result

### Phase 4: Workflow, side-effects, and merge gate

#### Automated

- [ ] 4.1 `actionlint` passes on the workflow
- [ ] 4.2 Third-party `uses:` SHA-pinned
- [ ] 4.3 Workflow YAML parses cleanly

#### Manual

- [ ] 4.4 `workflow_dispatch` produces comment + one passed/failed label + verdict status
- [ ] 4.5 Bad BQ-column diff → `ai-cr:failed` + red gate
- [ ] 4.6 `ai-cr:override` flips red gate to green
- [ ] 4.7 `ai-cr:review` re-runs and updates (not duplicates) comment + status
- [ ] 4.8 Simulated agent failure → red gate with "failed technically" message
- [ ] 4.9 Run uses only `puls_gpw_secret` (Gemini Flash on Vertex, no new secret)
