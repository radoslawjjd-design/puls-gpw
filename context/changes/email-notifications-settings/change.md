---
change_id: email-notifications-settings
title: Account settings page with email notifications opt-in
status: implemented
created: 2026-07-21
updated: 2026-07-21
archived_at: null
tracking:
  linear: PUL-81
  github: 140
---

## Notes

Slice (a) of PUL-81 "FARO-2: Watchlist email notifications" — split per the ticket's own suggestion. Cron delivery + dedup (slice b) is a later change.

**Scope of this change:**

Frontend (UX described by user):
- Add a "Ustawienia" (Settings) item to the top-right profile menu, alongside the existing theme toggle and logout.
- Clicking it opens a new account-settings page/view.
- First option on that page: "Powiadomienia" (Notifications). Clicking it loads a settings panel on the same page.
- The panel's only option (for now): a toggle "Powiadomienia email" with smaller-font description underneath: "Po włączeniu będziesz otrzymywać powiadomienia na swój adres email o nowych oświadczeniach twoich obserwowanych spółek."

Backend (so the toggle actually persists):
- Subscriptions table in BQ: `client_id, email, min_score, enabled, confirmed_at` + sent-log table stub (dedup lives in slice b, but table shape decided here).
- Endpoints `GET/POST/DELETE /api/notifications/settings` — auth via existing JWT/X-API-Key + X-Client-Id pattern.
- Double opt-in: confirmation email with token, `confirmed_at` set on click. SMTP infra already exists (see `scripts/test_alert.py`).

Out of scope (slice b): the ~5-min cron polling job, join with watchlists, actual notification delivery, dedup via sent-log.

Integration points found in static/index.html:
- Profile menu: `#profile-menu` (li items `#theme-toggle-btn`, `#logout-btn`) around line 1106.
- Nav/views: topbar `.nav-item` with `data-view`, views as `<div id="X-view">`; URL-state routing (PUL-84).
