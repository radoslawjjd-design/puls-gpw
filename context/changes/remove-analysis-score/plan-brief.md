# Remove analysis_score from user-facing /announcements response — Plan Brief

> Full plan: `context/changes/remove-analysis-score/plan.md`
> Frame brief: `context/changes/remove-analysis-score/frame.md`

## What & Why

`GET /announcements` leaks the internal `analysis_score` scoring field to
non-admin API keys, even though it's never shown in the user-facing UI
table. The initial framing (ticket) was confirmed correct by direct code
read — no reframe. Fix: drop the field from `AnnouncementUser` and from the
`list_announcements_user` BQ query.

## Starting Point

`AnnouncementUser` (`src/api.py:89-96`) declares `analysis_score`, and
`list_announcements_user` (`db/bigquery.py:621-672`) selects and returns it.
Both have exactly one caller each — the non-admin branch of `GET
/announcements` — confirmed via grep, so there's no other internal consumer
at risk.

## Desired End State

A user-role `GET /announcements` response never contains `analysis_score`.
An admin-role response is unaffected. A future accidental field addition to
`AnnouncementUser` is caught by an allowlist test, not just this one field.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Reframe needed? | No | Direct code read confirmed ticket's stated cause and fix exactly. | Frame |
| Regression-test strategy | Allowlist of all keys in user response | Catches any future field leak, not just `analysis_score` specifically. | Plan |
| Test placement | Extend existing tests (`test_announcements_user_returns_subset_fields`, `test_announcements_admin_returns_list`, `test_list_announcements_user_only_approved`) | Same mock data already in place; avoids duplicated setup. | Plan |
| Frontend change | None | Role-gated rendering in `static/index.html` already hides the field visually; removing it from the API response also closes the `data-score` DOM-attribute leak as a side effect. | Frame |

## Scope

**In scope:**
- `src/api.py` — remove `analysis_score` from `AnnouncementUser`
- `db/bigquery.py` — remove `analysis_score` from `list_announcements_user`'s `SELECT` and returned dict
- Allowlist regression test on the user response + explicit admin-unaffected assertion + BQ-layer test

**Out of scope:**
- `AnnouncementAdmin` / `list_announcements_admin` (admin path, untouched)
- Nested `structured_analysis` field restrictions (e.g. `sentiment`) — separate decision per ticket
- `static/index.html` — no change needed
- The `analysis_score` BQ table column/schema — still written by the analyzer, still read by admin

## Architecture / Approach

Single atomic phase: the Pydantic model removal is the authoritative fix
(`extra="ignore"` already makes it sufficient alone); the BQ query removal
is defense-in-depth so the field never leaves BigQuery for a user request.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Remove field + tests | `analysis_score` gone from user response, admin unaffected, allowlist guard in place | None identified — single call site each, confirmed via grep |

**Prerequisites:** None — no migrations, no new dependencies.
**Estimated effort:** ~30 minutes, single phase, single commit.

## Open Risks & Assumptions

- None outstanding — frame confidence was HIGH, single call site confirmed
  for both leak points, frontend impact traced and shown to self-resolve.

## Success Criteria (Summary)

- A user API key never sees `analysis_score` in `/announcements`.
- An admin API key still sees it, unchanged.
- `uv run pytest` passes in full.
