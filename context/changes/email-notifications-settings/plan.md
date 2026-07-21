# Account settings page + email-notifications opt-in (PUL-81 slice a) — Implementation Plan

## Overview

Add an account-settings surface to the Faro SPA: a "Ustawienia" item in the top-right profile menu opens a new JWT-only settings view whose first section, "Powiadomienia", reveals a panel with a single "Powiadomienia email" toggle plus a muted description. The toggle is a per-user preference (opt-in) persisted in a new BigQuery table via `GET`/`POST /api/notifications/settings`. This is slice (a) of PUL-81 — **no confirmation email, no token, no confirm endpoint, no cron delivery** (all deferred to slice b). Since actual delivery is slice b, this change sends zero emails.

## Current State Analysis

- The SPA is a single file `static/index.html` (~3600 lines, inline CSS+JS), no framework. Views are sibling `<div id="X-view">` toggled by inline `style.display`; the central switcher is `_navigateToView(view)` (`static/index.html:2348`), with `?view=` URL-state routing via `_writeUrl` (`:2450`) and `_applyUrlState` (`:2410`). The profile menu (`#profile-menu`, `:1106`) holds only theme-toggle (`:1641`) and logout (`:1629`).
- Per-user features are **JWT-only**: gated on the frontend by `!apiKey` (nav gating `:2021-2025`, url-state branches `:2426`) and on the backend by `Depends(_get_user_id)` (`src/api.py:149`, JWT-cookie only, returns `payload["user_id"]`).
- Authenticated fetch has **no wrapper** — calls inline `fetch(url, { headers: { 'X-API-Key': apiKey } })`; in a JWT session `apiKey === null` so auth rides the session cookie automatically. Canonical GET+401-guard pattern at `:3610-3623`.
- **No toggle/switch UI exists anywhere** in the app (verified — no `.switch`, `type=checkbox`, `role=switch`). Must be built from scratch.
- Backend routes are inline in `create_app()` (`src/api.py:284`); the startup DDL hook (`:298`) calls every `create_*_table_if_not_exists()` + `ensure_*_schema_current()`. BQ tables use the ensure/DDL-on-startup pattern (no migrations); MERGE-upsert template is `upsert_user_login` (`db/bigquery.py:990`); dataset via `BIGQUERY_DATASET` (`:44`), table names as module-level snake_case constants.
- Tests: pytest-playwright (Python), one `tests/e2e/conftest.py`. Every BQ function is patched at its `src.api.*` import site; a new BQ-backed endpoint must be imported into `src/api.py` AND have all its `db.bigquery.*` functions (data + DDL) patched in conftest, or the session fixture fails to boot. BQ-layer unit tests patch `db.bigquery._get_client`. `uv run pytest` is the single CI command (`.github/workflows/tests.yml:44`).

Full grounding: `context/changes/email-notifications-settings/research.md`.

## Desired End State

A logged-in (JWT) user sees a "Ustawienia" item in the profile menu. Clicking it navigates to a settings view (URL `?view=settings`) showing a left-hand section list whose first (active) entry is "Powiadomienia"; its panel shows a "Powiadomienia email" switch with the description "Po włączeniu będziesz otrzymywać powiadomienia na swój adres email o nowych oświadczeniach twoich obserwowanych spółek." Toggling the switch immediately persists the preference; on failure the switch reverts and an inline message appears. The preference survives reload and is stored per-user in BigQuery keyed on `user_id`. API-key (admin-tool) sessions do not see the entry. Verified by: unit tests (BQ + endpoints), an E2E test driving the toggle, and manual check that the preference round-trips.

### Key Discoveries:

- Copy template for the view: `_buildMyWalletViewContent` (`static/index.html:2261`) + `_showMyWalletViewDom` (`:2317`) + `showMyWalletView` (`:2333`); lazy-build guard idiom `_myWalletViewBuilt` (`:2259`).
- Routing hook points: add a `settings` branch in `_navigateToView` (`:2348`) and `_applyUrlState` (`:2410`, gate `&& !apiKey`); `_setActiveNavItem('settings')` (`:1655`) harmlessly clears topbar highlights (Settings lives in the profile menu).
- MERGE-upsert template `upsert_user_login` (`db/bigquery.py:990`); simple SELECT-with-default `get_user_role` (`:1027`); watchlist table create/ensure pair (`:472`/`:485`) wired at `src/api.py:298`.
- JSON-body POST template `POST /api/portfolio/positions` (`src/api.py:675`, Pydantic `PortfolioPositionIn`).
- Email claim: the session JWT payload carries `email` (`src/auth.py` claims: `user_id, email, auth_type, iat, exp, login_at, role`); `session_payload_from_request` (`src/auth.py:169`) returns the full payload.
- BQ-layer unit-test mocks: `_mock_bq_client(affected_rows)` (`tests/test_bigquery.py:54`), `_mock_bq_client_with_rows(rows)` (`:65`), `patch("db.bigquery._get_client", ...)` (`:83`).

## What We're NOT Doing

- No confirmation email, opt-in token, or confirm endpoint (email already verified at registration, PUL-86; notifications go to the account email).
- No cron/polling job, no watchlist join, no actual notification delivery, no dedup / sent-log table (all slice b).
- No `DELETE` endpoint — toggle-off is `enabled=false` via upsert.
- No `min_score` UI control — the column is stored with a default for slice b but not exposed.
- No subscription/entitlement gating (future direction; schema stays generic but no logic built now).
- No new env vars, no new GCP client, no email ESP/infra work (slice b infra decision).
- No toast/notification framework — inline error only.

## Implementation Approach

Bottom-up: land the BQ data layer first (table + functions, unit-tested in isolation), then the API endpoints (wired into `src/api.py`, startup hook, and conftest mocks, unit-tested), then the single-file frontend (menu item, view, routing, toggle, optimistic save) with an E2E test. Each layer follows an existing in-repo pattern verbatim to minimize risk. The notification address and `min_score` are derived/defaulted server-side, so the client payload is just `{enabled: bool}`.

## Critical Implementation Details

- **Conftest boot dependency**: the new `create_notification_subscriptions_table_if_not_exists` / `ensure_notification_subscriptions_schema_current` run inside `create_app()` at test startup. They MUST be patched (bare no-op) in `tests/e2e/conftest.py`, and the new data functions must be patched too (stateful in-memory store like `_watchlist_store`), or the session fixture fails at server boot / hits real BQ. The functions must first be imported into `src/api.py`'s `from db.bigquery import (...)` block — the `patch("src.api.<fn>")` target only resolves if bound there.
- **Email source**: `_get_user_id` returns only the user_id. The `POST` handler needs the account email to store in the row — derive it from the session payload (email claim) via `session_payload_from_request` or a small sibling dependency, not from the request body. Never trust a client-supplied address in this slice.

## Phase 1: BigQuery data layer

### Overview

Create the `notification_subscriptions` table definition and the read/upsert functions, keyed on `user_id`, following the watchlist/upsert patterns. Unit-test in isolation with mocked BQ client.

### Changes Required:

#### 1. Table schema + DDL helpers

**File**: `db/bigquery.py`

**Intent**: Define the new table and its create/ensure pair so it self-provisions on startup, matching every other table in the module.

**Contract**: Add module-level constant `_NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME = "notification_subscriptions"` and `_NOTIFICATION_SUBSCRIPTIONS_SCHEMA` with fields: `user_id` STRING REQUIRED, `email` STRING NULLABLE, `min_score` INT64 NULLABLE (default handled in code, stored 0), `enabled` BOOL REQUIRED, `confirmed_at` TIMESTAMP NULLABLE, `updated_at` TIMESTAMP NULLABLE. Add `create_notification_subscriptions_table_if_not_exists()` (get_table/create-on-NotFound, mirror `:472`) and `ensure_notification_subscriptions_schema_current()` (thin binding over `ensure_schema_current`, mirror `:485`).

#### 2. Read + upsert functions

**File**: `db/bigquery.py`

**Intent**: One SELECT to read the current preference (returning a sensible default when no row exists) and one MERGE-upsert to persist it.

**Contract**:
- `get_notification_settings(user_id: str) -> dict` — parameterized SELECT on `user_id`; when no row, return a default dict `{"enabled": False, "email": None, "min_score": 0, "confirmed_at": None}` (COALESCE/empty-result handling like `get_user_role` `:1027`). Wrap errors in `BigQueryError`.
- `upsert_notification_settings(user_id: str, email: str | None, enabled: bool, min_score: int = 0) -> None` — `MERGE ... ON user_id` setting `enabled`, `email`, `min_score`, `updated_at = CURRENT_TIMESTAMP()`, and `confirmed_at = CURRENT_TIMESTAMP()` when `enabled` is true (email already verified — no separate opt-in). Mirror `upsert_user_login` (`:990`). Parameterized, `BigQueryError`-wrapped.

#### 3. Unit tests

**File**: `tests/test_bigquery.py` (add cases; new file `tests/test_notifications_bigquery.py` acceptable if preferred)

**Intent**: Verify the SQL functions behave against a mocked BQ client — default-on-empty read, upsert affected-rows, error wrapping.

**Contract**: Use `patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(...))` for the read (both empty → default, and populated cases) and `_mock_bq_client(affected_rows=1)` for the upsert; assert `BigQueryError` on client failure.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -q` (or the new test module)
- Full suite still green: `uv run pytest --tb=short`

#### Manual Verification:

- `get_notification_settings` returns the opt-in default (`enabled=false`) for an unknown user without raising.

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 2.

---

## Phase 2: API endpoints

### Overview

Expose `GET` and `POST /api/notifications/settings`, wire the new DDL into the startup hook, import the new functions into `src/api.py`, and add all required conftest mocks. Unit-test the endpoints.

### Changes Required:

#### 1. Import + startup registration

**File**: `src/api.py`

**Intent**: Bind the new BQ functions into `src.api` (required for both use and test-patching) and self-provision the table on startup.

**Contract**: Extend the `from db.bigquery import (...)` block (`:18-56`) with the four new names. In the startup hook (`:298`) add `create_notification_subscriptions_table_if_not_exists()` + `ensure_notification_subscriptions_schema_current()` alongside the existing pairs.

#### 2. Endpoints

**File**: `src/api.py`

**Intent**: Read and write the per-user email-notification preference, deriving identity and address server-side.

**Contract**:
- `GET /api/notifications/settings` — `Depends(_get_user_id)`; returns `get_notification_settings(user_id)` as JSON (`{enabled, email, min_score, confirmed_at}`). `BigQueryError` → HTTP 500.
- `POST /api/notifications/settings` — Pydantic body `NotificationSettingsIn { enabled: bool }` (model style per `PortfolioPositionIn` `:675`); resolve `user_id` via `Depends(_get_user_id)` and the account email from the session payload (email claim via `session_payload_from_request` `src/auth.py:169`, or a small sibling dependency — never from the body); call `upsert_notification_settings(user_id, email, enabled, min_score=0)`; return the updated settings (echo `get_notification_settings`) or `204`. `BigQueryError` → HTTP 500.

#### 3. Conftest mocks

**File**: `tests/e2e/conftest.py`

**Intent**: Keep the E2E app bootable and stateful without real BQ.

**Contract**: In `live_server_url` add bare-no-op `patch("src.api.create_notification_subscriptions_table_if_not_exists")` and `patch("src.api.ensure_notification_subscriptions_schema_current")`; add stateful `patch("src.api.get_notification_settings", side_effect=...)` and `patch("src.api.upsert_notification_settings", side_effect=...)` backed by an in-memory dict keyed on user_id (mirror `_watchlist_store` `:219`, defaulting to `enabled=false`).

#### 4. Endpoint unit tests

**File**: `tests/test_api.py` (or `tests/test_auth_api.py` if a JWT cookie is easier there)

**Intent**: Verify auth-gating, default read, and round-trip write at the HTTP layer.

**Contract**: `TestClient` with a minted JWT cookie (`client.cookies.set("session", create_session_token(uid, email, "firebase", role=...))`, pattern `tests/test_auth_api.py:435`) and `patch("src.api.get_notification_settings" / "upsert_notification_settings", ...)`. Assert: unauthenticated → 401; GET default `enabled=false`; POST `{enabled:true}` calls upsert with the email from the token and echoes `enabled=true`.

### Success Criteria:

#### Automated Verification:

- Endpoint unit tests pass: `uv run pytest tests/test_api.py -q` (and/or `tests/test_auth_api.py`)
- E2E session fixture still boots (no unpatched BQ): `uv run pytest tests/e2e -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- `curl`/HTTP against a locally-run app with a valid session cookie: GET returns default, POST toggles and persists (against real or mocked BQ as available).

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 3.

---

## Phase 3: Frontend — settings view, menu, toggle

### Overview

Add the "Ustawienia" menu item, a JWT-only `#settings-view` with a section-list + panel layout, `?view=settings` routing, and the "Powiadomienia email" switch with optimistic save and inline error handling. Cover the flow with an E2E test.

### Changes Required:

#### 1. Profile-menu item

**File**: `static/index.html`

**Intent**: Add a "Ustawienia" entry between theme-toggle and logout that navigates to the settings view, shown only in JWT sessions.

**Contract**: New `<li role="none"><button id="settings-btn" role="menuitem">Ustawienia</button></li>` in `#profile-menu` (`:1106`); listener near `:1641` doing `closeProfileMenu(); _navigateToView('settings')`. Hide `#settings-btn` when `apiKey` is set (mirror nav gating `:2021-2025`).

#### 2. Settings view: shell + routing

**File**: `static/index.html`

**Intent**: A lazy-built view with a left section-list (first item "Powiadomienia", active) and a content panel, integrated into URL-state routing and gated to JWT sessions.

**Contract**: Add `<div id="settings-view" style="display:none"></div>` near the other view divs (~`:1154`). Add `_buildSettingsViewContent()` (model on `_buildMyWalletViewContent` `:2261`; header `<div class="view-header"><h2>Ustawienia</h2></div>`; section-list with "Powiadomienia" + panel container `#settings-panel`), `_showSettingsViewDom()` (model on `_showMyWalletViewDom` `:2317`, lazy-build guard, hide siblings, `closeProfileMenu()`), and `showSettingsView()`. Add a `settings` branch to `_navigateToView` (`:2348`) that pushes `?view=settings` and to `_applyUrlState` (`:2410`) gated `&& !apiKey`. Clicking "Powiadomienia" renders the notifications panel into `#settings-panel` (reuse the `#pp-form-wrap` show/hide idiom `:3264-3266`).

#### 3. Notifications panel: toggle + description + save

**File**: `static/index.html`

**Intent**: Render the switch and description, load current state on open, and persist changes optimistically with revert-on-error.

**Contract**: Panel contains a labeled `<input type="checkbox" role="switch" id="notif-email-toggle">` styled as a slider, label "Powiadomienia email", and a muted `<p>` (color `var(--text-muted)`, ~`.8rem`, like `.login-hint` `:138`) with the exact copy "Po włączeniu będziesz otrzymywać powiadomienia na swój adres email o nowych oświadczeniach twoich obserwowanych spółek." On panel open: `GET /api/notifications/settings` (fetch pattern `:3610-3623`, 401→`doLogout()`), set `checked` from `enabled`. On change: optimistically apply the new checked state, then **disable the switch for the duration of the in-flight `POST`** and re-enable it in a `finally` (this serializes rapid toggles and prevents out-of-order responses from desyncing UI vs. stored state — also makes the E2E deterministic). `POST {enabled}` (write pattern with `Content-Type: application/json`); on non-2xx revert `checked` to the prior value and show an inline error element under the toggle ("Nie udało się zapisać, spróbuj ponownie"); clear the error on the next successful save. Add minimal CSS for the switch appearance + the inline error.

#### 4. E2E test

**File**: `tests/e2e/test_notifications_settings.py` (new)

**Intent**: Drive the full user flow against the mocked backend.

**Contract**: `e2e_login_email(page, base_url)` (`tests/e2e/conftest.py:45`), open profile menu → click `get_by_role("menuitem", name="Ustawienia")`, assert URL `?view=settings` and `#settings-view` visible, click "Powiadomienia", locate the switch via `get_by_role("switch", name=...)`, assert default unchecked, click to enable, assert checked, reload / re-open and assert it persisted (backed by the in-memory conftest store). Unique email per run for isolation (`e2e_unique_email()`).

### Success Criteria:

#### Automated Verification:

- E2E test passes: `uv run pytest tests/e2e/test_notifications_settings.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- Logged in (JWT), "Ustawienia" appears in the profile menu; API-key session does not show it.
- Settings view opens at `?view=settings`; deep-link `/?view=settings` restores it; back/forward and logout behave correctly.
- Toggle reflects stored state on open, saves optimistically, and reverts + shows the inline message on a forced failure.
- Description copy and switch styling look correct in light and dark themes.

**Implementation Note**: After automated verification passes, pause for human confirmation. This is the final phase.

---

## Testing Strategy

### Unit Tests:

- `db.bigquery` — `get_notification_settings` (empty→default, populated), `upsert_notification_settings` (affected rows, `confirmed_at` set when enabling), `BigQueryError` wrapping. Mock `db.bigquery._get_client`.
- API — auth-gating (401), GET default, POST round-trip using email from the JWT (not the body). `TestClient` + minted session cookie + `patch("src.api.*")`.

### Integration / E2E Tests:

- Full toggle flow (open menu → settings → Powiadomienia → toggle → persist across reload) via Playwright against the mocked live server.

### Manual Testing Steps:

1. Log in with a real (JWT) account; confirm "Ustawienia" in the profile menu.
2. Open Settings, click "Powiadomienia", toggle on; reload and confirm it stays on.
3. Toggle off; reload and confirm off.
4. Simulate a failed save (e.g. offline) and confirm the switch reverts + inline error shows.
5. Log in via API-key/admin-tool session; confirm "Ustawienia" is hidden.
6. Check light + dark theme rendering.

## Performance Considerations

Negligible — two low-frequency per-user queries; no new client, no hot path. Table is tiny and keyed on `user_id`.

## Migration Notes

Table self-provisions via the startup ensure/DDL hook (additive, cold-start-safe) — no manual migration. `confirmed_at` retained for future use. `min_score` stored (default 0) for slice b's cron; not surfaced in UI. `user_id` is the canonical key (`client_id` legacy column intentionally not added).

**Opt-in invariant (slice a↔b contract):** `enabled` is the authoritative opt-in flag — **slice (b)'s delivery cron MUST filter on `enabled = true`**, not on `confirmed_at`. Because this slice drops double opt-in (account email already verified, PUL-86), `confirmed_at` is *informational only*: set to `CURRENT_TIMESTAMP()` when enabling and left as-is otherwise. `enabled = false` keeps the row and means opted-out. Do not couple slice (b)'s send decision to `confirmed_at`.

## References

- Research: `context/changes/email-notifications-settings/research.md`
- Change identity: `context/changes/email-notifications-settings/change.md` (Linear PUL-81, GitHub #140)
- MERGE-upsert template: `db/bigquery.py:990`
- View copy template: `static/index.html:2261`, `:2317`, `:2333`
- JSON-body POST template: `src/api.py:675`
- Auth dependency: `src/api.py:149`; session payload: `src/auth.py:169`
- Conftest live-server + in-memory store: `tests/e2e/conftest.py:219`, `:471`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery data layer

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -q` (or new test module) — 27437b9
- [x] 1.2 Full suite still green: `uv run pytest --tb=short` — 27437b9

#### Manual

- [x] 1.3 `get_notification_settings` returns opt-in default (`enabled=false`) for unknown user without raising — 27437b9

### Phase 2: API endpoints

#### Automated

- [ ] 2.1 Endpoint unit tests pass: `uv run pytest tests/test_api.py -q` (and/or `test_auth_api.py`)
- [ ] 2.2 E2E session fixture still boots (no unpatched BQ): `uv run pytest tests/e2e -q`
- [ ] 2.3 Full suite green: `uv run pytest --tb=short`

#### Manual

- [ ] 2.4 GET returns default, POST toggles + persists against locally-run app with a valid session cookie

### Phase 3: Frontend — settings view, menu, toggle

#### Automated

- [ ] 3.1 E2E test passes: `uv run pytest tests/e2e/test_notifications_settings.py -q`
- [ ] 3.2 Full suite green: `uv run pytest --tb=short`

#### Manual

- [ ] 3.3 "Ustawienia" shows in JWT session, hidden in API-key session
- [ ] 3.4 `?view=settings` opens/deep-links/back-forward/logout behave correctly
- [ ] 3.5 Toggle reflects stored state, saves optimistically, reverts + inline error on forced failure
- [ ] 3.6 Description copy + switch styling correct in light and dark themes
