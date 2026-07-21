---
date: 2026-07-21T12:32:10+02:00
researcher: Radek
git_commit: ab2e11e4a88fde4cf2ced9b2adbce7132cbdfed1
branch: master
repository: radoslawjjd-design/puls-gpw
topic: "Account settings page + email-notifications opt-in (PUL-81 slice a)"
tags: [research, codebase, notifications, settings, spa-routing, bigquery, smtp, double-opt-in, auth]
status: complete
last_updated: 2026-07-21
last_updated_by: Radek
---

# Research: Account settings page + email-notifications opt-in (PUL-81 slice a)

**Date**: 2026-07-21T12:32:10+02:00
**Researcher**: Radek
**Git Commit**: ab2e11e4a88fde4cf2ced9b2adbce7132cbdfed1
**Branch**: master
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

How to implement slice (a) of PUL-81 "FARO-2: Watchlist email notifications": a new "Ustawienia" (Settings) item in the top-right profile menu → an account-settings view → a "Powiadomienia" (Notifications) option → a panel with a single "Powiadomienia email" toggle + description; backed by a BQ subscriptions table, `GET/POST/DELETE /api/notifications/settings` endpoints, and a double-opt-in confirmation email with token. Cron delivery + dedup is slice (b), out of scope here.

## Summary

The app is a full FastAPI service (`src/api.py`, `src/auth.py`, entrypoint `api_main.py`) with a **single-file vanilla-JS SPA** (`static/index.html`, ~3600 lines, inline CSS+JS). Every layer needed already has a close, copyable precedent:

- **Frontend**: profile menu + a JWT-only lazy-built view + `?view=` URL-state routing. The **My Wallet view** is the cleanest copy template. No toggle-switch UI exists anywhere — it must be built from scratch (a real labeled control for a11y/testability).
- **Backend**: routes inline in `create_app()`; per-user identity via `Depends(_get_user_id)` (JWT-cookie-only, PUL-74); BQ tables via ensure/DDL-on-startup (no migrations); MERGE upsert + parameterized queries; branded SMTP in `src/notifier.py`.
- **Tokens**: **no self-issued token pattern exists yet.** PUL-85/86 used Firebase-native oobCodes, which are NOT reusable for "set `confirmed_at` in BQ on click." Recommend a self-issued short-lived HS256 JWT with a distinct `purpose:"notif_confirm"` claim, signed with the existing `JWT_SECRET` (reuses PyJWT, zero new deps/env).
- **Tests**: pytest-playwright (Python), one `conftest.py`. Every BQ function is patched at its `src.api.*` import site; a new BQ-backed endpoint must (1) be imported into `src/api.py` and (2) have ALL its `db.bigquery.*` functions patched in conftest — including the DDL helpers that run at `create_app` startup, or the whole session fixture fails to boot. Mailer is patched at its call-site module. `uv run pytest` is the single CI command.

**Biggest open question (flag for planning):** the account email is *already verified at registration* (PUL-86). Is a second double-opt-in for notifications actually needed, or is it redundant? The ticket asks for double opt-in; the verified-email reality may let us simplify. Decision needed before planning the token/confirm flow. See Open Questions.

## Detailed Findings

### Frontend — SPA structure & where to hook in (`static/index.html`)

**Profile menu** — `#profile-menu` `<ul role="menu" hidden>` at **`static/index.html:1106`**, inside `.profile-menu-wrap` (1100), trigger `#profile-menu-btn` (1101).
```
1107  <li role="none"><button id="theme-toggle-btn" role="menuitem">Tryb ciemny</button></li>
1108  <li role="none"><button id="logout-btn" role="menuitem">Wyloguj</button></li>
```
Listeners wired by id: `#logout-btn` at `:1629` (`closeProfileMenu(); doLogout();`), `#theme-toggle-btn` at `:1641`. Open/close: `openProfileMenu()` `:1662`, `closeProfileMenu()` `:1668`; toggle click `:1674`, outside-click `:1678`, Escape `:4049`. CSS `.profile-menu button` `:308` (new item needs no CSS).
→ **Add**: `<li role="none"><button id="settings-btn" role="menuitem">Ustawienia</button></li>` between 1107–1108; listener near 1641 calling `closeProfileMenu(); _navigateToView('settings')`.

**View system** — sibling `<div id="X-view">` inside `#dashboard-screen` (`:1090`), shown/hidden via inline `style.display`. Central switcher **`_navigateToView(view)` `:2348`** (sets `currentView`, calls per-view `show*View()`, writes URL once). Per-view show fns: `showAnnouncementsView()` `:2224`, `showMyWalletView()` `:2333`, `showPortfolioPositionsView()` `:3523`. Active-nav highlight `_setActiveNavItem(view)` `:1655` (harmless no-op for a menu-based Settings route — it just clears topbar highlights). Lazy-build guard pattern: `_myWalletViewBuilt` `:2259` / `ppView.dataset.built` `:3529`.

**URL-state routing (PUL-84)** — logged-in dashboard uses `?view=` query + `history.pushState/replaceState`:
- Write: `_writeUrl(view, params, push)` `:2450` (omits `view=` for default `announcements`, gated on `role`).
- Restore: `_applyUrlState()` `:2410` — reads `location.search`, dispatches on `params.get('view')`; my-wallet branch at `:2426` gated `&& !apiKey`. Called from `showDashboard()` `:2029` and popstate `:1686`.
- Logout reset: `doLogout()` `:1312` → `history.replaceState({}, '', '/')`.
→ **Add**: a `settings` branch in both `_navigateToView()` (`:2348`) and `_applyUrlState()` (`:2410`, gated `&& !apiKey`), mirroring my-wallet.

**Auth-guarded rendering** — module vars `apiKey`, `role` (`:1215-1216`) from `sessionStorage`. Two modes: API-key session (`apiKey` set) vs **JWT-cookie session** (`apiKey === null`, real user account; `_enterUserSession()` `:1522`, boot probe `_bootProbeSession()` `:1421` → `/api/auth/me`). **JWT-only per-user views are gated by `!apiKey`** — `#nav-my-wallet-btn`/`#nav-portfolio-positions-btn` hidden when `apiKey` is set (`:2021-2025`). **Settings/Notifications must follow the same `!apiKey` gate** (needs a real account) and hide its menu entry for API-key sessions.

**Fetch auth** — **no wrapper**; every call inlines `fetch(url, { headers: { 'X-API-Key': apiKey } })`. In a JWT session `apiKey === null` so the header is absent and **auth rides the session cookie automatically** (default `credentials: 'same-origin'`). Backend accepts either. Canonical GET (portfolio treemap `:3610-3623`): fetch → `if (r.status === 401) { doLogout(); return; }` → `if (!r.ok) throw` → `r.json()`. Write adds `'Content-Type': 'application/json'` + `body: JSON.stringify(...)` (`:2873`, `:3209`, `:3976`). New `/api/notifications/settings` calls copy this verbatim incl. the 401 guard.

**Toggle UI** — **none exists** (no `.switch`, `type="checkbox"`, `role="switch"` anywhere). Build from scratch: a real labeled control (`<input type="checkbox" role="switch">` or `<button aria-pressed>`) so tests can use `get_by_role("switch"/"checkbox", name=...)`. Description-below-toggle idiom: `color: var(--text-muted)` (token `:29`) at ~`.8rem`, like `.login-hint` (`:138`).

**Copy template** — **My Wallet** (simplest JWT-only string-innerHTML view): placeholder `<div id="settings-view" style="display:none">` near 1154-1155; builder modeled on `_buildMyWalletViewContent()` `:2261`; DOM show on `_showMyWalletViewDom()` `:2317` + `showMyWalletView()` `:2333`. The "Powiadomienia → reveal panel" interaction can copy the `#pp-form-wrap` show/hide toggle idiom (`:3264-3266`).

### Backend — routes, auth, BQ, SMTP (`src/api.py`, `src/auth.py`, `db/bigquery.py`, `src/notifier.py`)

**Routes** — all main routes inline in `create_app()` `src/api.py:284` via `@app.<method>`. Auth router `src/auth.py:351` (`APIRouter(prefix="/api/auth")`), registered `src/api.py:288`. Entrypoint `api_main.py`: `load_dotenv()` (line 3, MUST precede db imports), env guard for `ADMIN_API_KEY`/`USER_API_KEY`/`JWT_SECRET` (line 12). **Startup DDL hook** `@app.on_event("startup")` `src/api.py:298` calls every `create_*_table_if_not_exists()` + `ensure_*_schema_current()` — **new tables register here.**

Watchlist endpoints (closest per-user GET/POST/DELETE analog):
- `GET /watchlist` `src/api.py:439` — `Depends(_get_role)` + `Depends(_get_user_id)`.
- `POST /watchlist/{ticker}` `:451`, `DELETE /watchlist/{ticker}` `:468` (`status_code=204`). `BigQueryError` → HTTP 500.
- For a **JSON-body** POST (our settings payload), model on `POST /api/portfolio/positions` `src/api.py:675` (Pydantic body `PortfolioPositionIn`).

**Auth dependency** — reuse **`_get_user_id(request)` `src/api.py:149`**: JWT-cookie-only (PUL-74 retired the anonymous `X-Client-Id` path), reads signed `session` cookie, 401 if absent, returns `payload["user_id"]`. `_get_role` `:123` = JWT claim OR `X-API-Key` env match. Settings endpoints need a concrete user → `user_id: str = Depends(_get_user_id)`. JWT machinery in `src/auth.py`: cookie `session` (`:31`), `create_session_token` (`:89`, HS256), `decode_session_token` (`:121`, never raises), secret `_jwt_secret()` = `os.environ["JWT_SECRET"]` (`:84`).

**BigQuery layer** (`db/bigquery.py`) — dataset `_DATASET = os.environ.get("BIGQUERY_DATASET", "espi_ebi")` (`:44`); ref helper `_table_ref` (`:108`); table names as module-level snake_case constants (`_WATCHLIST_TABLE_NAME="watchlist"` `:460`, etc.). **Per-table pattern**: `_<NAME>_SCHEMA` field list → `create_<name>_table_if_not_exists()` (get_table/create on NotFound, watchlist `:472`) → `ensure_<name>_schema_current()` (thin binding over generic `ensure_schema_current(table, schema)` `:149`, additively adds missing columns; watchlist `:485`) → both wired at `src/api.py:298`. Query templates: insert-if-not-exists `add_watchlist_ticker` `:1056`; DELETE `remove_watchlist_ticker` `:1089`; SELECT `list_watchlist_tickers` `:1115`; **MERGE upsert `upsert_user_login` `:990`** (best template for settings upsert); COALESCE default `get_user_role` `:1027`. Client singleton w/ `with_quota_project` guard `_get_client` `:85`. All fns plain module-level `def`, imported into `src/api.py:18-56`.
→ **New tables**: `notification_subscriptions` + `notification_sent_log` (or `_send_log`). **Identity column: use `user_id` (STRING)** to match current convention — `client_id` is legacy, DROP pending (PUL-88/GH#166). `confirmed_at` → `TIMESTAMP NULLABLE` (NULL = unconfirmed).

**SMTP** (`src/notifier.py`) — low-level `_send(subject, body, html=False, to=None, from_name=None)` `:133` (`smtplib.SMTP` + starttls + login + send_message `:150`); creds `_smtp_creds()` `:12` from `SMTP_HOST/PORT/USER/PASSWORD`, `OWNER_EMAIL` (`.strip().lstrip("﻿")` guards Secret-Manager BOM). Branded HTML templates `_verification_html` `:227`, `_password_reset_html` `:183` (navy `#14304A` header, gold `#b8964f` CTA, logo `{origin}/static/img/faro-mark.png`, Polish; **HTML-escape link+origin** `html.escape(..., quote=True)` — AI-sec finding PR #159). Public senders `send_verification_email` `:272`, `send_password_reset_email` `:287`, owner `send_alert` `:302`.
→ **Add** `_notification_confirm_html` + `send_notification_confirmation_email(to_email, confirm_link, origin)`. Send in `BackgroundTasks` after response, failures → `send_alert` (pattern `src/auth.py:406`, wired `:466`).

**Opt-in token** — **no self-issued token pattern in repo** (no `itsdangerous`, no `secrets.token_urlsafe`). PUL-85/86 used **Firebase-native** `generate_password_reset_link` (`src/auth.py:523`) / `generate_email_verification_link` (`:414`) = oobCodes, Firebase-hosted, **NOT reusable** (no callback into our BQ). PyJWT IS present (`pyproject.toml:22`) and used for sessions.
→ **Recommended**: self-issue a short-lived HS256 JWT signed with `JWT_SECRET`, distinct claim `{"purpose":"notif_confirm", "user_id":..., "exp": now+Ndays}`, mirroring `create_session_token`/`decode_session_token` (`src/auth.py:89`/`121`). The distinct `purpose` prevents replaying a session cookie as a confirm token. Confirm endpoint decodes → BQ `UPDATE ... SET confirmed_at = CURRENT_TIMESTAMP()`. Zero new deps/env. (Alt: opaque `secrets.token_urlsafe(32)` stored in the row — stdlib, but no in-repo precedent.)

**Config** — `load_dotenv()` at `api_main.py:3`; **no central settings module**, env read ad-hoc via `os.environ.get`. Canonical list in `.env.example`. Reusable: `JWT_SECRET` (opt-in token), `SMTP_*`+`OWNER_EMAIL` (mail), `BIGQUERY_DATASET`, `GOOGLE_CLOUD_PROJECT`. **No new env var required.**

### Tests (`tests/`, `tests/e2e/conftest.py`)

**E2E boot & BQ mocking** — `live_server_url` session fixture `tests/e2e/conftest.py:471-596`: sets `ADMIN_API_KEY`/`USER_API_KEY`/`JWT_SECRET` (`:473`), enters an `ExitStack` of ~55 `patch()` (`:477-581`), boots real app `uvicorn.Server(...create_app()...)` on a daemon thread (`:582`). **BQ is NOT real** — every function is patched **at its `src.api` import site** (`patch("src.api.<fn>", ...)`), NOT at `db.bigquery`. Two flavors: DDL/startup → bare no-op `patch(...)`; data fns → `return_value=` (static rows) or `side_effect=` (stateful in-memory dict, e.g. `_watchlist_store` `:219`, `:251-264`). Auth-side fns patched at `src.auth.*` (`:536-577`).
→ **A new BQ-backed endpoint MUST**: (1) be imported into `src/api.py`'s `from db.bigquery import (...)` block (`:19-56`) — the `src.api.<fn>` patch target only resolves if bound there; (2) patch in conftest **every** `db.bigquery.*` fn the endpoints call — **including the DDL helpers `create_..._table_if_not_exists`/`ensure_..._schema_current`** run at `create_app` startup (bare no-op), or the whole fixture fails at boot; plus data fns (`get/upsert/delete_notification_settings`, confirm helper) via `side_effect=` fakes over an in-memory dict like `_watchlist_store`. This is the recurring "mock ALL db.bigquery.*, not just startup hooks" lesson.

**SMTP stubbing** — patch the **high-level send fn at its caller module**, never `smtplib`. E2E: `patch("src.auth.send_password_reset_email")` `:567`, `patch("src.auth.send_verification_email")` `:575` (bare no-ops); Firebase link-gen faked `:564`,`:572`. Unit (`tests/test_auth_api.py`): `patch("src.auth.send_verification_email")` + `assert_called_once_with(email, link, origin)` (`:82-95`); failure path `side_effect=OSError` → assert silent 2xx + `send_alert` (`:164-177`). → For the confirm email: add send fn in `notifier.py`, call from endpoint module, patch at **that call-site module** in conftest (no-op) + unit (assert-called). Background-task send → follow `test_register_*` pattern (`:71-199`).

**Auth in tests** — E2E: `e2e_login_email(page, base_url, email=None)` `conftest.py:45-58` (real login form → JWT cookie; `verify_password_rest` faked to accept anything `:61`; unique email `e2e_unique_email()` `:41`; admin via `email=E2E_ADMIN_EMAIL` `:27`). Unit: `TestClient` + either `X-API-Key` header (`tests/test_api.py:39`) or minted cookie `client.cookies.set("session", create_session_token(uid, email, "firebase", role=...))` (`tests/test_auth_api.py:435`). Settings are per-user (`_get_user_id`) → use the JWT-cookie path.

**Test layout** — ~30 pytest modules + `tests/e2e/`. Endpoint tests: `tests/test_api.py` (`api_client` `:28`, `with patch("src.api.<fn>", return_value=...)`), `tests/test_auth_api.py` (respx for Firebase REST). **BQ-layer unit tests**: `tests/test_bigquery.py` — import fns from `db.bigquery`, `patch("db.bigquery._get_client", return_value=_mock_bq_client())` (`:83`), helpers `_mock_bq_client(affected_rows)` `:54`, `_mock_bq_client_with_rows(rows)` `:65`. → Replicate both layers: (a) `test_bigquery.py`-style unit for new `db.bigquery` fns; (b) `test_api.py`/`test_auth_api.py`-style endpoint tests.

**Playwright locators** — **zero `data-testid`** in repo. In practice: `get_by_role("button", name=...)` dominant; `get_by_label("E-mail"/"Hasło", exact=True)`; `get_by_placeholder`; `get_by_text`; `#id` only to **scope** a region or assert visibility (e.g. `#my-wallet-view`, `#page-label`, `#role-badge`), never as primary click target. → New settings view: stable `#settings-view` container + real labeled toggle → `get_by_role("switch"/"checkbox", name=...)`.

**Runner** — `uv run pytest` (unit + integration + Playwright E2E, all pytest). CI `.github/workflows/tests.yml:44` `uv run pytest --tb=short` after `playwright install chromium --with-deps` (`:41`). Config `pyproject.toml:36-40` (`pythonpath=["."]`, marker `gdpr`). All E2E env set in-fixture → no GCP creds needed. No `justfile`/`Makefile`; `AGENTS.md` stale (advertises `npx playwright test` — ignore).

## Code References

- `static/index.html:1106-1109` — profile menu list (insert "Ustawienia" here)
- `static/index.html:1629,1641` — menu-item listener pattern
- `static/index.html:2348` — `_navigateToView` central switcher
- `static/index.html:2410-2426` — `_applyUrlState` (add `settings` branch, gate `!apiKey`)
- `static/index.html:2261,2317,2333` — My Wallet view builder/show (copy template)
- `static/index.html:2021-2025` — JWT-only nav gating (`!apiKey`)
- `static/index.html:3610-3623` — canonical authenticated fetch pattern + 401 guard
- `src/api.py:284,288,298` — `create_app`, auth router include, startup DDL hook
- `src/api.py:149,123` — `_get_user_id` (reuse), `_get_role`
- `src/api.py:439,451,468` — watchlist GET/POST/DELETE analog
- `src/api.py:675` — JSON-body POST template (`/api/portfolio/positions`)
- `db/bigquery.py:44,108,149,460-525,990` — dataset/ref/ensure-schema/watchlist-table/MERGE-upsert
- `src/notifier.py:133,227,272` — `_send`, branded HTML template, `send_verification_email`
- `src/auth.py:89,121,406,466` — token create/decode, background-send pattern
- `tests/e2e/conftest.py:45-58,219,471-596` — login helper, in-memory store, live_server fixture
- `tests/test_bigquery.py:54,65,83` — BQ-layer unit-test mocks
- `tests/test_auth_api.py:82-95,435` — endpoint test + minted-cookie auth
- `.github/workflows/tests.yml:41,44` — CI test command

## Architecture Insights

- **Single-file SPA, string-innerHTML views, no framework** — new UI = one placeholder div + one builder fn + one show fn, mirroring My Wallet. Keep it in `static/index.html`.
- **Two auth modes, per-user = JWT-only** — anything tied to a real account (settings, watchlist, portfolio) uses `Depends(_get_user_id)` on the backend and the `!apiKey` gate on the frontend. Follow both.
- **No migrations; ensure-DDL-on-startup** — additive schema evolution via `ensure_schema_current`; every new table registers its create+ensure pair in the startup hook. Cold-start-safe.
- **`user_id` is the canonical per-user key; `client_id` is legacy** (DROP pending, PUL-88). New tables should key on `user_id`.
- **SMTP is centralized + branded** — reuse `_send` + a new branded HTML template; always HTML-escape user-influenced values; send in BackgroundTasks with `send_alert` on failure.
- **Testability is a11y-driven** — no test-ids; real roles/labels. The toggle must be a genuine labeled control.
- **Patch at import site** — mock `src.api.<fn>` / `src.auth.<fn>`, not `db.bigquery.<fn>`; wire new fns into `src/api.py` first.
- **Lesson priors that apply** (`context/foundation/lessons.md`): GCP client init needs `load_dotenv` before db imports + `with_quota_project` guard (already satisfied by reusing existing client); no new GCP client here so the risk is low.

## Historical Context (from prior changes)

- `context/archive/2026-07-19-email-verification-registration/` — double-opt-in-ish flow, but **Firebase-native** (oobCode), explicitly "no pending-registrations table or custom token flow." Confirms our token must be self-issued, not Firebase.
- `context/archive/2026-07-19-password-reset-firebase/` — branded SMTP from own domain (gpw.okiem.ai), HTML-escape fix (PR #159). The mail-template + BackgroundTasks precedent to clone.
- `context/archive/2026-06-22-my-wallet-watchlist/` — the per-user BQ + view precedent (watchlist table, JWT-only view). Primary structural template.
- PUL-74 (JWT-only endpoints, `watchlist.user_id`) — established `_get_user_id` and the `!apiKey` gate we must follow.
- PUL-84 (URL-state) — the `?view=` routing we extend for the settings route.

## Related Research

- None prior for this change. Closest: the archived plans above.

## Decisions (post-research, 2026-07-21)

- **No double opt-in in slice (a).** Email is already verified at registration (PUL-86) and notifications go to that same account email. The toggle is purely a user *preference* (want emails / don't). `enabled=true` is immediately effective; store `confirmed_at = CURRENT_TIMESTAMP()` on enable (column kept for future). **The confirmation email, confirm endpoint, and self-issued token all DROP OUT of slice (a).** Since actual delivery is slice (b), **slice (a) sends zero emails** — no SMTP work here at all.
- **Slice (a) endpoint surface** shrinks to: `GET /api/notifications/settings` (read current pref) + `POST` (upsert `enabled`). `DELETE` from the ticket is optional (row-delete) — decide in planning; a POST-upsert toggle likely covers it.
- **Settings payload** = effectively `{enabled: bool}`; `email` derived server-side from `_get_user_id` → account email (not sent by client). `min_score` stored with a default (`0`) for slice (b)'s cron; not exposed in UI.
- **Forward-looking (product direction, informs schema, not built now):** notifications will later become subscription-gated with multiple channels (email + others). Keep the schema generic enough that a `channel` / entitlement dimension can be added later without a rewrite — but do NOT build subscription/entitlement logic in slice (a). A single `enabled` email pref is the whole surface now.
- **Email deliverability at scale (slice (b) / infra decision, NOT slice (a)):** sending bulk/transactional notifications from a personal Gmail will hit send limits (~500/day free, ~2000/day Workspace) and risk spam-flagging/suspension. When user volume grows, move delivery to a dedicated transactional ESP with the **own domain authenticated (SPF + DKIM + DMARC)** — own domain alone via Gmail does NOT fix volume/reputation. Candidates: Amazon SES (cheapest), Postmark / Resend / Mailgun (strong transactional deliverability). This belongs in slice (b)'s infra/`/10x-infra-research`, not here.

## Open Questions

1. **[RESOLVED — see Decisions] Is double opt-in redundant?** The account email is already verified at registration (PUL-86). A second confirmation click for notifications may be unnecessary friction. The ticket asks for double opt-in, but "email already verified" could let us treat `enabled=true` as immediately active (still storing `confirmed_at` = now). **Decide before planning the token/confirm endpoint** — this materially changes scope (whole confirm-email + confirm-endpoint + token may drop out of slice a). *Recommendation to weigh in planning: since email is verified, consider single-step enable with `confirmed_at` auto-set, and defer/drop the double-opt-in token flow — or keep it only if we later allow notifications to a different address than the account email.*
2. **`min_score` in slice (a)?** The described UI is a plain on/off toggle with no threshold control. `min_score` (used by the slice-b cron filter) is not exposed in the UI. Store a default (e.g. `0`, or a product-chosen threshold) now, or omit the column until slice (b)? Table shape should still include it per the ticket; UI ignores it for now.
3. **Is the notification address always the account email?** If yes, the settings payload is effectively just `{enabled: bool}` and `email` is derived from `_get_user_id` → user's account email (simpler, and strengthens the "double opt-in is redundant" case). If we ever allow a different address, double opt-in becomes justified. Assume account email for slice (a) unless product says otherwise.
4. **Menu entry visibility** — hide "Ustawienia" for API-key (admin-tool) sessions, or show it always? Precedent (my-wallet/portfolio) hides per-user views when `apiKey` is set. Recommend same: show only in JWT sessions.
