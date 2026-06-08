# Plan Brief — github-linear-sync

## Goal

Keep GitHub issue states in sync with Linear: Done/Canceled in Linear → closed on GitHub; any other status → reopened on GitHub. Runs every 5 minutes via GitHub Actions + manual dispatch with dry-run mode.

## What this builds

Single new file: `.github/workflows/sync-linear-to-github.yml`

Inline Python 3 script (stdlib only) inside the workflow:
1. Query Linear GraphQL for all PUL-team issues + state type + attachment URLs
2. Extract GitHub issue number from attachment URL (already set up on every PUL-X issue)
3. Compare desired state (completed/cancelled → closed; else → open) vs. current GitHub state
4. Close or reopen via GitHub REST API; log all actions; soft-fail per issue

## Phases

| # | Deliverable | Effort |
|---|-------------|--------|
| 1 | `.github/workflows/sync-linear-to-github.yml` | ~30 min |
| 2 | Manual: add `LINEAR_API_TOKEN` secret + dry-run verification | ~15 min |

## Secrets

- `LINEAR_API_TOKEN`: Personal API key from Linear Settings → API → Personal API keys (manual setup in Phase 2)
- `GITHUB_TOKEN`: Provided automatically by GitHub Actions

## Key decisions locked in

- **Direction**: Linear → GitHub only (one-way)
- **Trigger**: `schedule: "*/5 * * * *"` + `workflow_dispatch` with `dry_run` boolean
- **Mapping**: GitHub issue number from Linear attachment URL (no config file needed)
- **Error handling**: soft fail (log warning, continue per issue; exit 0)
- **Testing**: manual dry-run only — no unit tests
- **Scope**: Done/Canceled → close; all other states → reopen (if currently closed)

## Not in scope

- GitHub → Linear direction
- Label/milestone sync
- Unit tests
- Issues with no GitHub attachment in Linear
