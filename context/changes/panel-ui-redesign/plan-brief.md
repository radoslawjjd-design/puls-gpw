# Panel UI/UX Redesign — Plan Brief

> Full plan: `context/changes/panel-ui-redesign/plan.md`
> Research: `context/changes/panel-ui-redesign/research.md`

## What & Why

Redesign the puls-gpw admin/user panel (PUL-25) to improve user experience in four areas: a polished login screen, autocomplete dropdowns for filter fields, a GDPR consent banner, and general visual polish. This change is a prerequisite for PUL-28 (User profile + My Wallet) — the panel must be visually mature before expanding it with profile features.

## Starting Point

The panel is a 485-line single-file SPA (`static/index.html`) with inline CSS and vanilla JS — no external dependencies. Three filter fields (Ticker, Company, Event Type) are plain text inputs with no suggestions. The login screen shows only `<h1>puls-gpw</h1>` with no branding, hint text, or visual hierarchy. Zero cookies or localStorage are used; no GDPR banner exists.

## Desired End State

A visually polished panel where first-time visitors see a clearly branded login card with hint text, a GDPR notice banner appears and can be dismissed with one click, and Ticker/Company filter fields show native browser autocomplete suggestions populated from BigQuery. Event Type shows five Polish-labelled options. The overall dashboard — filters, table, pagination, and modal — looks refined and consistent.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Autocomplete UI | HTML `<datalist>` (native) | Zero JS library, accessible, preserves single-file philosophy | Plan |
| Branding approach | CSS/text only (no images) | No StaticFiles mount needed; avoids `create_app()` changes | Research |
| GDPR scope | Minimal — sessionStorage notice only | Technically sufficient (no cookies/trackers); simple `localStorage` flag | Research |
| Event type labels | Polish labels with JS bidirectional map | UX-friendly display; reverse-map translates to API code on submit | Plan |
| Autocomplete data loading | Fetch once at `showDashboard()` | Minimises BQ round-trips; two calls at login is acceptable | Plan |
| Event types source | Static JS map (no BQ query) | Values are hardcoded in `src/analyzer.py`; won't change without a code deploy | Research |
| Server-side cache | Simple in-memory dict + TTL (5 min) | No library needed; autocomplete data is stable within a session | Plan |

## Scope

**In scope:**
- Login screen visual redesign (card, branding, hint text, error state)
- GDPR fixed-bottom banner (`localStorage` flag, client-side only)
- Autocomplete for Ticker, Company (from BQ), Event Type (static JS map)
- Visual polish: filters, table rows, pagination, modal content structure
- Two new BQ functions + two new FastAPI endpoints + in-memory cache
- Playwright E2E tests for all new UI behaviour

**Out of scope:**
- External CSS framework or JS bundler
- StaticFiles mount or image files
- Backend GDPR persistence (no new table or endpoint)
- Session inactivity timeout (PUL-32)
- User profile / My Wallet (PUL-28 — unblocked by this change)

## Architecture / Approach

All changes span three layers in isolation: (1) `db/bigquery.py` gets two new DISTINCT query functions; (2) `src/api.py` gets two new GET endpoints with a module-level cache dict; (3) `static/index.html` gets CSS/DOM/JS additions. No schema migrations, no API contract changes, no infrastructure changes. The single-file frontend philosophy is preserved throughout.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BQ + FastAPI autocomplete | Two endpoints returning distinct tickers and companies | BQ returns too many companies (cap at 500); reserved keyword check for any new column name |
| 2. Login redesign + GDPR | Polished login card, hint text, GDPR localStorage banner | `init()` ordering constraint — new functions must be called from inside `init()`, not top-level |
| 3. Dashboard autocomplete | `<datalist>` for 3 fields; Polish event type labels with reverse-map | Datalist styling is browser-native (cannot be fully styled); `fetchAnnouncements()` must translate labels back to codes |
| 4. Visual polish | CSS improvements to filters, table, pagination, modal sections | Modal `structured_analysis` rendering: fallback to raw text if JSON parse fails |
| 5. E2E tests | Playwright coverage for login UX, GDPR lifecycle, autocomplete datalist | Playwright cannot interact with native `<datalist>` dropdowns; test population count + network request inspection instead |

**Prerequisites:** Phase 3 requires Phase 1 endpoints to be running (locally or deployed).  
**Estimated effort:** ~2-3 sessions across 5 phases.

## Open Risks & Assumptions

- `structured_analysis` in `openModal()` arrives as a `data-*` string attribute: must parse via `JSON.parse()` — if the shape changes (new keys), the modal sections need updating
- Event type codes in `src/analyzer.py` are assumed stable; if they change, the static JS map in `static/index.html` must be updated manually in sync
- Production BQ company count unknown: LIMIT 500 is a safeguard, but if the actual count is much higher the autocomplete will silently truncate

## Success Criteria (Summary)

- Login screen: branded, clear hint text, error state styled — verifiable in < 30 seconds by a new user
- GDPR banner: appears once, dismissed permanently with one click
- All three filter fields have working autocomplete suggestions; Event Type submits the correct API code regardless of which label the user selects
