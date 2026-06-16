# Bump GitHub Actions to Node 24-compatible versions — Implementation Plan

## Overview

GitHub forces Node 24 as the default JavaScript-action runtime from 2026-06-16 and removes Node 20 from runners on 2026-09-16. Our CI/CD actions are pinned to Node-20-era majors and currently emit deprecation warnings only. This change bumps every action across the deploy and AI-review pipelines to its latest Node 24-native major, preserving each file's existing pinning style, so the pipelines keep running clean past the September removal.

## Current State Analysis

Three files reference GitHub Actions:

- **`.github/workflows/deploy.yml`** — runs on `push: branches: [master]`. Tag-pinned (`@vN`):
  - `actions/checkout@v4`
  - `google-github-actions/auth@v2`
  - `google-github-actions/setup-gcloud@v2`
  - `astral-sh/setup-uv@v6`
- **`.github/workflows/ai-code-review.yml`** — runs on PRs; produces the `ai-code-review/verdict` status that branch protection requires. SHA-pinned with `# vX.Y.Z` comments:
  - `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2` (line 99)
  - `google-github-actions/auth@c200f3691d83b41bf9bbd8638997a462592937ed # v2.1.13` (line 111)
  - `uses: ./.github/actions/ai-reviewer` (line 147) — local composite action.
- **`.github/actions/ai-reviewer/action.yml`** — the local composite action. SHA-pinned with comment:
  - `actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0` (line 43; Node 20)

`deploy.yml` has **no PR or `workflow_dispatch` trigger** — it can only be exercised by a push to master.

## Desired End State

Every action reference resolves to a Node 24-native major. `deploy.yml` keeps `@vN` tag pins; `ai-code-review.yml` and the composite action keep full-SHA pins with refreshed `# vX.Y.Z` comments. The post-merge Deploy run on master completes with no Node-version deprecation warnings, and the AI-review gate is green on the PR that delivers this change.

### Key Discoveries:

- The "both workflows" intent actually spans **three files** — the composite action `.github/actions/ai-reviewer/action.yml` carries a Node-20 `setup-node` that is invisible from the workflow YAML alone.
- Two pinning conventions coexist by design: `deploy.yml` uses tags, the review pipeline uses SHA pins. The plan preserves both rather than unifying them.
- Resolved latest majors (via `gh api`, 2026-06-16):
  - `actions/checkout` → **v6** (v6.0.3, SHA `df4cb1c069e1874edd31b4311f1884172cec0e10`)
  - `google-github-actions/auth` → **v3** (SHA `7c6bc770dae815cd3e89ee6cdf493a5fab2cc093`)
  - `google-github-actions/setup-gcloud` → **v3** (v3.0.1)
  - `astral-sh/setup-uv` → **v8** (v8.2.0)
  - `actions/setup-node` → **v6** (v6.4.0, SHA `48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e`)

## What We're NOT Doing

- Not converting `deploy.yml` to SHA pins — it stays tag-pinned per its existing style.
- Not unifying the two pinning conventions across the repo.
- Not changing any action **inputs**, job logic, Python version (`3.13`), or deploy commands — only version pins move.
- Not adding a `workflow_dispatch` trigger to `deploy.yml`.
- Not bumping the Anthropic SDK / reviewer code inside `tools/ai-code-reviewer` — only the `setup-node` action version.

## Implementation Approach

Two phases split by pinning style and blast radius. Phase 1 is the four tag bumps in `deploy.yml`. Phase 2 is the three SHA bumps across the review pipeline (workflow + composite action). The PR that carries both phases exercises the bumped review-pipeline actions through the `ai-code-review/verdict` gate; the post-merge Deploy run on master is the acceptance signal for the deploy-pipeline bumps.

## Critical Implementation Details

- **SHA pins must keep the trailing `# vX.Y.Z` comment** — it's the only human-readable record of what the SHA is. Update both the SHA and the comment together; a stale comment is worse than none.
- **`setup-node@v6` still supports `node-version-file`** — the composite action's `node-version-file: tools/ai-code-reviewer/.nvmrc` input is unchanged across v4→v6, so only the `uses:` line moves.

## Phase 1: Bump `deploy.yml` (tag-pinned)

### Overview

Move the four tag-pinned actions in the deploy workflow to their latest Node 24-native majors.

### Changes Required:

#### 1. Deploy workflow action pins

**File**: `.github/workflows/deploy.yml`

**Intent**: Bump each tag-pinned action to its latest major so the deploy job runs on Node 24 with no deprecation warnings. No inputs change.

**Contract**: Four `uses:` lines change tag only:
- `actions/checkout@v4` → `@v6`
- `google-github-actions/auth@v2` → `@v3`
- `google-github-actions/setup-gcloud@v2` → `@v3`
- `astral-sh/setup-uv@v6` → `@v8.2.0` (exact version — see Migration Notes: setup-uv dropped moving major tags at v8, so `@v8` does not resolve)

### Success Criteria:

#### Automated Verification:

- `deploy.yml` parses as valid YAML (e.g. `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml'))"`).
- No remaining Node-20-era tags in the file: a search for `@v4`/`@v2`/`setup-uv@v6` in `deploy.yml` returns nothing.
- Every pin in `deploy.yml` resolves to a real ref: for each `uses:` tag, `gh api repos/<owner>/<repo>/git/refs/tags/<tag>` returns a single tag (catches the dropped-major-tag trap that YAML-parse + string-absence checks miss).

#### Manual Verification:

- After merge, the post-merge **Deploy** run on master completes successfully (build, push, all three Cloud Run updates).
- The Deploy run logs show no "Node.js 16/20 actions are deprecated" warnings.

**Implementation Note**: This phase's gate before proceeding to Phase 2 is **automated-only** (1.1–1.3). All manual verification for both phases (1.4/1.5 and 2.4/2.5) happens together once the single PR is merged — Phase 1's actions run only on the post-merge Deploy, Phase 2's on the PR — so do not wait on manual confirmation between phases. See Testing Strategy for the combined post-merge verification sequence.

---

## Phase 2: Bump AI-review pipeline (SHA-pinned)

### Overview

Bump the SHA-pinned actions in the AI-review workflow and its local composite action to their latest Node 24-native majors, refreshing both the SHA and the version comment on each line.

### Changes Required:

#### 1. AI-review workflow action pins

**File**: `.github/workflows/ai-code-review.yml`

**Intent**: Move the two SHA-pinned actions to their latest majors so every PR run is Node 24-native. Keep the SHA-pin + comment convention.

**Contract**: Two `uses:` lines change SHA + comment:
- line 99 `actions/checkout` → `df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3`
- line 111 `google-github-actions/auth` → `7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3`

#### 2. Composite action setup-node pin

**File**: `.github/actions/ai-reviewer/action.yml`

**Intent**: Move the composite action's `setup-node` off Node 20. This is the hidden third Node-20 reference behind `uses: ./.github/actions/ai-reviewer`.

**Contract**: line 43 `actions/setup-node` → `48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0`. The `node-version-file` input is unchanged.

### Success Criteria:

#### Automated Verification:

- Both files parse as valid YAML.
- No Node-20-era SHAs/comments remain: a search for `# v4.2.2`, `# v2.1.13`, `# v4.4.0` across the three files returns nothing.
- Each bumped SHA matches its `gh api repos/<owner>/<repo>/git/refs/tags/<tag>` resolution (re-verify the three SHAs at implementation time).

#### Manual Verification:

- The PR carrying this change produces a green `ai-code-review/verdict` status — this run exercises the bumped checkout, auth, and setup-node actions.
- The AI-review run logs show no Node deprecation warnings.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation before opening the PR for merge.

---

## Testing Strategy

### Unit Tests:

- None — this is a CI configuration change with no application code.

### Integration Tests:

- The CI workflows are the integration test. Phase 2's actions run on the PR; Phase 1's actions run post-merge on master.

### Manual Testing Steps:

1. Open a PR from `pul-36-ci-node24` with both phases committed.
2. Confirm the `ai-code-review/verdict` check goes green (validates Phase 2 actions) and its logs are warning-free.
3. Merge to master.
4. Watch the post-merge **Deploy** run; confirm it completes and logs are free of Node deprecation warnings (validates Phase 1 actions).
5. If Deploy fails on a bumped action, revert is a one-line-per-pin `git revert` of the merge.

## Performance Considerations

None — version pins only.

## Migration Notes

`setup-uv@v6→v8` crosses two majors and `checkout@v4→v6` crosses two majors; per the upstream changelogs these majors changed only the bundled Node runtime and default behaviors irrelevant to our usage (the `python-version` input we set survives in v8.2.0, verified; `credentials_json` survives in auth v3, verified).

**setup-uv tagging trap (verified 2026-06-16):** setup-uv stopped publishing moving major/minor tags at v8 — `@v8` and `@v8.0` no longer resolve; only exact `@v8.2.0` (or a SHA) works. deploy.yml therefore pins `setup-uv@v8.2.0` exactly, the one deviation from the file's `@vN` convention. Runtime check: `v6=node20`, `v7=node24`, `v8.2.0=node24` — the bump is required (v6 is the warning source). If a clean moving major tag is later preferred over an exact pin, `@v7` is a valid node24 fallback. checkout (`@v6`), auth (`@v3`), and setup-gcloud (`@v3`) all still publish moving major tags. Re-confirm against release notes during implementation if any step errors.

## References

- Change identity: `context/changes/ci-node24/change.md` (PUL-36, GH #39)
- Deploy workflow: `.github/workflows/deploy.yml`
- AI-review workflow: `.github/workflows/ai-code-review.yml:99`, `:111`, `:147`
- Composite action: `.github/actions/ai-reviewer/action.yml:43`
- GitHub Actions Node 24 transition: default from 2026-06-16, Node 20 removed 2026-09-16

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Bump deploy.yml (tag-pinned)

#### Automated

- [x] 1.1 deploy.yml parses as valid YAML — 773a031
- [x] 1.2 No remaining Node-20-era tags (`@v4`/`@v2`/`setup-uv@v6`) in deploy.yml — 773a031
- [x] 1.3 Every pin in deploy.yml resolves to a real ref via gh api — 773a031

#### Manual

- [ ] 1.4 Post-merge Deploy run on master completes successfully
- [ ] 1.5 Deploy run logs show no Node deprecation warnings

### Phase 2: Bump AI-review pipeline (SHA-pinned)

#### Automated

- [x] 2.1 ai-code-review.yml and action.yml parse as valid YAML
- [x] 2.2 No Node-20-era SHAs/comments remain (`# v4.2.2`, `# v2.1.13`, `# v4.4.0`)
- [x] 2.3 Each bumped SHA matches its tag resolution via gh api

#### Manual

- [ ] 2.4 PR produces a green ai-code-review/verdict status
- [ ] 2.5 AI-review run logs show no Node deprecation warnings
