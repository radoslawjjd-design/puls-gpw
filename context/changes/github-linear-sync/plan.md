# GitHub ↔ Linear Sync — Implementation Plan

## Overview

Create a GitHub Actions workflow that polls Linear every 5 minutes and keeps GitHub issue
states in sync: Done/Canceled in Linear → closed on GitHub; any other status → reopened on
GitHub. Triggered also via `workflow_dispatch` with a `dry_run` mode for safe manual testing.

## Current State Analysis

- One existing workflow: `.github/workflows/deploy.yml` (build + deploy on push to master).
- Linear already stores GitHub issue URLs as attachments on every PUL-X issue — no separate
  mapping config needed.
- `GITHUB_TOKEN` is automatically provided by GitHub Actions and has `issues: write` permission
  when declared in the workflow.
- `LINEAR_API_TOKEN` does not yet exist in GitHub Secrets — must be created manually (Phase 2).

## Desired End State

`.github/workflows/sync-linear-to-github.yml` runs every 5 minutes and on manual dispatch.
For every Linear issue in team `PUL` that has a GitHub attachment:
- state type `completed` or `cancelled` → GitHub issue closed (idempotent if already closed)
- any other state type → GitHub issue reopened (idempotent if already open)

Dry-run mode logs what would change without modifying GitHub.

### Key Discoveries

- Linear GraphQL endpoint: `https://api.linear.app/graphql` — auth via `Authorization: <token>` header.
- Linear state types: `completed`, `cancelled`, `started`, `unstarted`, `backlog`.
- GitHub attachment URL format on Linear issues: `https://github.com/radoslawjjd-design/puls-gpw/issues/N` — issue number is the last path segment.
- GitHub REST: `PATCH /repos/{owner}/{repo}/issues/{number}` with `{"state": "closed"}` or `{"state": "open"}`.
- `github.repository` in the workflow already contains `radoslawjjd-design/puls-gpw`.

## What We're NOT Doing

- GitHub → Linear sync (we close Linear via MCP in Claude Code sessions).
- Label or milestone sync.
- Handling issues that have no GitHub attachment in Linear (skipped silently).
- Hard fail on transient API errors (soft fail with warning log).
- Unit tests for the workflow script (manual dry-run is the test strategy).
- Separate config/mapping file — Linear attachments are the source of truth.

## Implementation Approach

Single new workflow file with an inline Python 3 heredoc script (stdlib only — `urllib`,
`json`, `os`). Python is pre-installed on `ubuntu-latest`; no `uv` or package install needed.

The script:
1. Calls Linear GraphQL to fetch all team-PUL issues with their attachments and state type.
2. For each issue with a GitHub attachment URL, extracts the issue number.
3. Checks the current GitHub issue state via `GET /repos/.../issues/N`.
4. If state diverges from expected — closes or reopens via `PATCH`.
5. In dry-run mode: logs what it would do, skips the PATCH call.
6. On any exception: logs a warning, continues to the next issue.

## Phase 1: Sync workflow

### Overview

Create `.github/workflows/sync-linear-to-github.yml`. This is the only deliverable in Phase 1.

### Changes Required

#### 1. New workflow file

**File**: `.github/workflows/sync-linear-to-github.yml`

**Intent**: Define the schedule, triggers, permissions, and inline Python script that drives
the Linear → GitHub sync.

**Contract**:

```yaml
name: Sync Linear → GitHub

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Log changes without applying them"
        type: boolean
        default: false

permissions:
  issues: write

jobs:
  sync:
    name: Linear → GitHub
    runs-on: ubuntu-latest
    steps:
      - name: Sync
        env:
          LINEAR_API_TOKEN: ${{ secrets.LINEAR_API_TOKEN }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DRY_RUN: ${{ inputs.dry_run || 'false' }}
          REPO: ${{ github.repository }}
        run: python3 - << 'PYEOF'
        # inline Python script (see Intent above)
        PYEOF
```

The inline Python script logic (to implement in the `run:` block):

```
QUERY = """{ issues(filter: { team: { key: { eq: "PUL" } } }, first: 100) {
  nodes { identifier state { type } attachments { nodes { url } } } } }"""

After fetching: if len(nodes) == 100: print("WARNING: results may be truncated — add pagination")

For each issue node:
  - find attachment URL matching pattern: "github.com" + "/issues/" in URL
  - extract issue_number = re.search(r'/issues/(\d+)', URL).group(1)
  - GET https://api.github.com/repos/{REPO}/issues/{issue_number}
      Header: Authorization: Bearer {GH_TOKEN}, Accept: application/vnd.github+json
  - desired_state = "closed" if state.type in {"completed", "cancelled"} else "open"
  - if current_state == desired_state: log "already {state}, skip"
  - elif DRY_RUN == "true": log "[DRY RUN] would {close|reopen} #{issue_number}"
  - else:
      PATCH https://api.github.com/repos/{REPO}/issues/{issue_number}
        body: {"state": desired_state}
      log "{closed|reopened} #{issue_number} (Linear {identifier}: {state.type})"
  - wrap each issue in try/except: log warning, continue
```

Error handling:
- `LINEAR_API_TOKEN` missing → raise with clear message (hard fail — misconfiguration)
- Linear GraphQL error response → log warning, exit 0
- GitHub API error on individual issue → log warning, continue to next issue

### Success Criteria

#### Automated Verification

- 1.1 `uv run pytest --tb=short` passes (deploy.yml already runs this — no regression)
- 1.2 `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/sync-linear-to-github.yml'))"` exits 0

#### Manual Verification

- 1.3 Workflow file present at `.github/workflows/sync-linear-to-github.yml`
- 1.4 `workflow_dispatch` trigger visible in GitHub Actions UI after push

---

## Phase 2: Secret + live verification

### Overview

Add `LINEAR_API_TOKEN` to GitHub Secrets (manual step), then verify the sync works correctly
via dry-run and a real run.

### Changes Required

#### 1. GitHub Secret (manual, no code change)

**File**: GitHub → Settings → Secrets and variables → Actions → New repository secret

**Intent**: Provide the workflow with a Linear Personal API Key so it can call the GraphQL API.

**Contract**: Secret name must be exactly `LINEAR_API_TOKEN`. Value: a Personal API key from
Linear → Settings → API → Personal API keys → Create key.

### Success Criteria

#### Automated Verification

- 2.1 Workflow run triggered via `workflow_dispatch` with `dry_run: true` exits 0
- 2.2 Dry-run log shows expected output: `[DRY RUN] would close #...` or `already closed, skip` for known Done issues
- 2.3 After disabling dry_run: first scheduled run (or manual dispatch) exits 0 and logs sync activity

#### Manual Verification

- 2.4 Verify in GitHub that a Done-in-Linear issue is now closed after a non-dry run
- 2.5 Confirm no unintended reopens (open GH issues that should remain open stay open)
- 2.6 After 10 minutes: check Actions tab → sync runs appear every ~5 minutes with green status

---

## Testing Strategy

### Manual Testing Steps

1. Push Phase 1 commit; go to GitHub Actions → find "Sync Linear → GitHub" workflow.
2. Click "Run workflow" → enable `dry_run` → Run.
3. Open the run log; confirm each known Done/Canceled issue shows `[DRY RUN] would close #N`.
4. Re-run without `dry_run`; confirm GitHub issues are actually closed.
5. Wait 10 minutes; confirm scheduled runs appear in the Actions tab.
6. (Optional) move a Linear issue back to In Progress; wait for next cycle; confirm GH issue reopens.

## References

- Linear GraphQL API docs: https://developers.linear.app/docs/graphql/working-with-the-graphql-api
- GitHub Issues REST API: https://docs.github.com/en/rest/issues/issues#update-an-issue
- Existing workflow for pattern reference: `.github/workflows/deploy.yml`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Sync workflow

#### Automated

- [x] 1.1 `uv run pytest --tb=short` passes (no regression) — c5fa157
- [x] 1.2 `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/sync-linear-to-github.yml'))"` exits 0 — c5fa157

#### Manual

- [x] 1.3 Workflow file present at `.github/workflows/sync-linear-to-github.yml` — c5fa157
- [x] 1.4 `workflow_dispatch` trigger visible in GitHub Actions UI after push — c5fa157

### Phase 2: Secret + live verification

#### Automated

- [x] 2.1 Dry-run dispatch exits 0
- [x] 2.2 Dry-run log shows expected output for known Done issues
- [x] 2.3 Real run (no dry_run) exits 0 and logs sync activity

#### Manual

- [x] 2.4 Done-in-Linear issue is closed on GitHub after real run
- [x] 2.5 No unintended reopens
- [ ] 2.6 Scheduled runs appear every ~5 min in Actions tab
