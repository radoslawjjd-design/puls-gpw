# Frame Brief: Remove analysis_score from user-facing /announcements response

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

`GET /announcements` returns `analysis_score` to non-admin (user-role) API
keys, even though that column is not rendered in the user-facing UI table
(PUL-42 / GitHub #60).

## Initial Framing (preserved)

- **User's stated cause**: `AnnouncementUser` model (`src/api.py:89`)
  declares `analysis_score: float | None = None`, and `list_announcements_user`
  (`db/bigquery.py:621`) selects `analysis_score` from BigQuery and returns it
  in the row dict — both leak the field into the API JSON response.
- **User's proposed direction**: remove `analysis_score` from the
  `AnnouncementUser` model and from the `SELECT` clause in
  `list_announcements_user`.
- **Pre-dispatch narrowing**: not applicable — single observation, no
  scope/design ambiguity to disambiguate (see "No Dimension Map" below).

## No Dimension Map — mechanical fix, evidence is direct

This skill's own usage guidance lists "pure mechanical change" as a skip
case. Direct code reads confirm there is no hypothesis space worth a
parallel investigation:

- `AnnouncementUser` is instantiated in exactly one place:
  `src/api.py:148`, inside the `role != "admin"` branch of `GET /announcements`.
  No other consumer.
- `list_announcements_user` is called in exactly one place: `src/api.py:143`.
  Confirmed via project-wide grep — not used by x-publisher, portfolio-xpost,
  public-url access, or any test fixture beyond mocking it.
- `AnnouncementAdmin` (`src/api.py:67-86`) is a separate model with its own
  `analysis_score` field (line 86) — untouched by this change, so the admin
  response is structurally guaranteed to be unaffected, not just "expected to
  be."

Spawning sub-agents to investigate alternate "dimensions" here would be
hypothesis padding with no plausible target — there is exactly one cause and
one fix location, both verified directly.

## Additional Discovery (in scope, no extra fix needed)

`static/index.html:569` builds a `data-score="${esc(score)}"` DOM attribute
for **every** table row regardless of role (the visible `<td>` column and the
modal's score section are already correctly gated by `role === 'admin'` at
lines 573, 635, 639, 647 — confirmed). Today this means a non-admin's score
is present in the raw DOM (inspectable via dev tools) even though never
rendered as visible text. Removing `analysis_score` from the API response
closes this automatically — `row.analysis_score` becomes `undefined` for
user rows, `score` resolves to `'—'`. **No separate frontend change needed.**

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: exactly as stated in the ticket —
> drop `analysis_score` from `AnnouncementUser` (`src/api.py:95`) and from the
> `SELECT`/return dict in `list_announcements_user`
> (`db/bigquery.py:644-645`, `668`).

The initial framing was correct — proceed with the originally proposed
direction. No reframe. The single-call-site confirmation also resolves a
latent worry (would removing the BQ column break some other internal
consumer relying on `analysis_score` for scoring/selection?) — it doesn't;
`list_announcements_user` has no other caller.

## Confidence

**HIGH** — both claimed root-cause locations verified by direct read
(`src/api.py:89-96`, `db/bigquery.py:621-672`); single call site for each
confirmed via grep; admin path structurally isolated via a separate model;
frontend exposure path traced and shown to self-resolve once the API field
is removed.

## What Changes for /10x-plan

Nothing beyond the ticket's own fix description. The plan should cover:
1. Remove `analysis_score` from `AnnouncementUser` (`src/api.py:95`).
2. Remove `analysis_score` from the `SELECT` clause and returned dict in
   `list_announcements_user` (`db/bigquery.py:644-645`, `668`).
3. A test asserting `analysis_score` is absent from the user-role
   `/announcements` response, and an existing/extended admin-role test
   confirming `analysis_score` is still present there.

## References

- `src/api.py:67-96` (both models), `src/api.py:116-155` (`/announcements` endpoint)
- `db/bigquery.py:621-672` (`list_announcements_user`)
- `static/index.html:557-594, 621-653` (role-gated rendering, confirmed unaffected)
- Tracking: Linear PUL-42, GitHub #60
