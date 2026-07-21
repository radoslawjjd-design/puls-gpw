# Account settings page + email-notifications opt-in (PUL-81 slice a) — Plan Brief

> Full plan: `context/changes/email-notifications-settings/plan.md`
> Research: `context/changes/email-notifications-settings/research.md`

## What & Why

Give users a place to control account settings, starting with one preference: whether they receive email notifications about new announcements from their watched companies. This is slice (a) of PUL-81 (FARO-2) — the settings UI + persistence. Actual email delivery (cron, dedup) is slice (b) and out of scope here.

## Starting Point

The Faro SPA is a single `static/index.html` file with a profile menu holding only theme-toggle + logout, `?view=`-based routing, and JWT-only per-user features (watchlist, portfolio) gated by `!apiKey` on the frontend and `Depends(_get_user_id)` on the backend. There is no settings page and no toggle/switch UI anywhere in the app yet.

## Desired End State

A logged-in user opens "Ustawienia" from the profile menu, lands on a settings view (`?view=settings`) whose first section "Powiadomienia" shows a "Powiadomienia email" switch with a description. Toggling it immediately persists an opt-in preference (per-user, in BigQuery) that survives reload; a failed save reverts the switch and shows an inline error. Admin-tool (API-key) sessions don't see the entry.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Double opt-in | **Dropped** for slice (a) | Account email already verified (PUL-86); toggle is just a preference on that address | Research |
| Endpoint surface | `GET` + `POST` (upsert) | Toggle-off = `enabled=false` via upsert; `DELETE` unnecessary | Plan |
| Identity / address | `user_id` key; email from JWT claim | Matches current convention (`client_id` legacy); never trust client-supplied address | Research/Plan |
| Toggle UI | Native `<input type=checkbox role=switch>` | Real accessible control → `get_by_role("switch")`; no test-ids needed | Plan |
| Save behavior | Optimistic, revert + inline error on failure | Matches "click the switch" UX with truthful state on error | Plan |
| Settings layout | Section-list + panel | Matches described flow and scales to future sections/channels | Plan |
| Email delivery infra | Deferred to slice (b) | No emails sent in slice (a); ESP + authenticated own-domain is a slice-b infra decision | Research |

## Scope

**In scope:** BQ `notification_subscriptions` table + read/upsert functions; `GET`/`POST /api/notifications/settings`; profile-menu "Ustawienia" item; `#settings-view` with section-list + notifications panel; the switch + description + optimistic save; unit + E2E tests.

**Out of scope:** confirmation email / token / confirm endpoint; cron delivery, watchlist join, dedup / sent-log; `DELETE` endpoint; `min_score` UI; subscription/entitlement gating; email ESP/infra; toast framework.

## Architecture / Approach

Bottom-up across three cohesive phases, each following an existing in-repo pattern verbatim. BQ layer (table via ensure-DDL-on-startup + MERGE-upsert) → API (two endpoints inline in `create_app`, wired into the startup hook and conftest mocks) → single-file frontend (menu item, view, `?view=settings` routing, switch with optimistic save). Client payload is just `{enabled}`; email and `min_score` are derived/defaulted server-side.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BQ data layer | Table + `get`/`upsert` functions, unit-tested | SQL/MERGE correctness (mitigated by `upsert_user_login` template) |
| 2. API endpoints | `GET`/`POST` + startup wiring + conftest mocks | Unpatched new BQ fn breaks the E2E session fixture boot |
| 3. Frontend | Menu item, settings view, switch, optimistic save, E2E | Routing edge cases (deep-link/back-forward/logout); building a switch from scratch |

**Prerequisites:** none beyond the existing JWT auth + BQ setup. No new env vars, no new GCP client.
**Estimated effort:** ~1 focused session across 3 phases.

## Open Risks & Assumptions

- Assumes the notification address is always the account email (true for slice a); a future "different address" feature would reintroduce double opt-in.
- The conftest mocking pitfall (patch ALL new `db.bigquery.*` incl. DDL, at the `src.api` import site) is the most likely thing to break tests — called out explicitly in the plan.
- Building the first switch component from scratch means new CSS; must be checked in both light and dark themes.

## Success Criteria (Summary)

- A JWT user can toggle email notifications on/off from Ustawienia → Powiadomienia and the choice persists across reload.
- The preference is stored per-user in BigQuery keyed on `user_id`, with the address taken from the verified account email.
- Unit tests (BQ + endpoints) and an E2E test pass; full `uv run pytest` suite stays green.
