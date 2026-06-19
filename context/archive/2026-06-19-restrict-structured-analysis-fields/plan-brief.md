# Restrict sentiment inside structured_analysis — Plan Brief

> Full plan: `context/changes/restrict-structured-analysis-fields/plan.md`
> Frame brief: `context/changes/restrict-structured-analysis-fields/frame.md`

## What & Why

The actual problem to plan around is: strip `sentiment` from
`structured_analysis` specifically in the `user`-role branch of `GET
/announcements`, applying the exact PUL-42 pattern — not designing a general
allowlist mechanism for an open-ended schema. The frontend already treats
`sentiment` as admin-only (`static/index.html:635-638`); the API hasn't
caught up.

## Starting Point

`AnnouncementUser.structured_analysis` is a `dict | None` field.
`extra="ignore"` on the Pydantic model only filters top-level model fields —
it does not prune keys inside that nested dict. So unlike PUL-42 (which
dropped a top-level `analysis_score` field), there's no model field to
delete here; `sentiment` has to be popped out of the parsed dict itself,
in the user branch only, before `AnnouncementUser` is constructed.

## Desired End State

A user-role `GET /announcements` call never returns `sentiment` inside any
item's `structured_analysis`. An admin-role call is completely unaffected —
`sentiment` still comes through when the underlying analysis produced one.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Scope of the fix | `sentiment` only, no general allowlist | Schema is closed (4 fields, one writer, `extra="ignore"`); user confirmed scope during framing | Frame |
| Where the fix lives | Inline `.pop("sentiment", None)` in the user branch of `src/api.py`, right after `_parse_structured_analysis` | Minimal diff; a one-field pop doesn't warrant a dedicated helper function | Plan |
| Test structure | Extend the two existing tests (`test_announcements_user_parses_structured_analysis`, `test_announcements_admin_returns_list`) rather than add new dedicated tests | Mirrors PUL-42's extend-don't-duplicate pattern; reuses existing mocks/fixtures | Plan |
| BQ/query layer | No change | `structured_analysis` is a single JSON string column fetched identically for both roles — there's no column to drop, only a key to drop after parsing | Frame |
| Frontend | No change | `static/index.html`'s `role === 'admin'` gate already hides `sentiment` visually; the `data-structured-analysis` DOM leak (`:570`) closes automatically once the API stops sending the key to user-role responses | Frame |

## Scope

**In scope:**
- Strip `sentiment` from `structured_analysis` in the user-role branch of `GET /announcements` (`src/api.py`)
- Regression tests locking stripped-for-user / present-for-admin behavior

**Out of scope:**
- General allowlist mechanism for `structured_analysis`
- Any change to `AnnouncementAdmin`, `list_announcements_admin`, or the BQ `SELECT`
- Any change to `static/index.html`

## Architecture / Approach

One inline change at the single point `AnnouncementUser` is constructed in
the user branch of the `/announcements` endpoint: pop `"sentiment"` from the
dict returned by `_parse_structured_analysis(...)` before it's passed into
the model. No new functions, no schema or query changes.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Strip sentiment + regression tests | `sentiment` removed from user-role `structured_analysis`; admin path untouched; tests lock both | Low — single-file, single-branch change with an exact precedent (PUL-42) to mirror |

**Prerequisites:** None — no dependencies on other in-flight work.
**Estimated effort:** ~30-60 minutes, single phase, single session.

## Open Risks & Assumptions

- None outstanding — frame brief confidence was HIGH and confirmed independently against the actual source files during planning (schema closed, frontend gate exists, no internal consumer reads `sentiment` via the API).

## Success Criteria (Summary)

- A user API key never sees `sentiment` in any `structured_analysis` object returned by `/announcements`.
- An admin API key's `/announcements` response is byte-for-byte unaffected (still includes `sentiment` when present).
- Full test suite (`uv run pytest`) passes.
