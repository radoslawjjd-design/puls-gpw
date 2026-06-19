---
date: 2026-06-18T00:00:00+02:00
researcher: Claude (Sonnet 4.6)
git_commit: 3e84340b8786c3d330385a15e4b9e79eca67da04
branch: radoslawjjd/pul-39-portfolio-status-xpost-generator-skill
repository: puls-gpw
topic: "Panel UI/UX redesign — login screen, autocomplete, GDPR notice, visual polish (PUL-25)"
tags: [research, codebase, panel, ui, fastapi, bigquery, autocomplete, gdpr]
status: complete
last_updated: 2026-06-18
last_updated_by: Claude (Sonnet 4.6)
---

# Research: Panel UI/UX redesign (PUL-25)

**Date**: 2026-06-18  
**Researcher**: Claude (Sonnet 4.6)  
**Git Commit**: 3e84340b8786c3d330385a15e4b9e79eca67da04  
**Branch**: radoslawjjd/pul-39-portfolio-status-xpost-generator-skill  
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

What is the current state of the panel (frontend + backend + BQ layer) and what exactly needs to change to implement PUL-25: login screen redesign, autocomplete dropdowns, GDPR/cookie consent, and visual polish?

## Summary

The panel is a **485-line single-file SPA** (`static/index.html`) with inline CSS + vanilla JS, backed by FastAPI (`src/api.py`) and BigQuery (`db/bigquery.py`). The scope of PUL-25 is well-contained:

- **Login screen**: has a basic layout — needs centered card, branding, and error-state polish
- **Autocomplete**: all 3 filter fields are plain `<input type="text">` — no datalist, no dropdown; 3 new BQ queries + 3 new FastAPI endpoints needed
- **GDPR**: zero cookies currently used; sessionStorage auth is GDPR-light already; the consent banner is purely a UI addition (no backend needed unless we persist consent)
- **Visual polish**: CSS is 153 lines, inline, Tailwind-like — extensible without external deps

No framework dependencies exist. All changes stay in `static/index.html` + `src/api.py` + `db/bigquery.py`.

---

## Detailed Findings

### 1. Frontend — static/index.html (485 lines)

**File**: `static/index.html`  
**CSS**: embedded `<style>` block, lines 7–159 (153 lines)  
**JS**: embedded `<script>` block, lines 219–482  
**No external assets** — zero linked CSS/JS files in `static/`

#### Login Screen (current state)
- `#login-screen` div at lines 164–172
- `.login-box` — 360px centered box with blue button (`#2563eb`)
- `<h1>puls-gpw</h1>`, `<input type="password" id="api-key-input">`, `<button id="login-btn">`, `<div id="login-error">`
- Auth flow: POST fetch to `/auth/role` (line 239) with `X-API-Key` header → stores role + key in `sessionStorage`
- Error shown at line 303: `showLoginError()` makes `#login-error` visible
- **Missing**: no logo/branding beyond text h1, no visual hierarchy, no "what is this?" hint text

#### Filter Fields (current state, lines 184–192)
| ID | Type | Placeholder | Notes |
|----|------|-------------|-------|
| `f-ticker` | text | "Ticker (np. PKO)" | plain input |
| `f-company` | text | "Spółka" | plain input |
| `f-event-type` | text | "Typ (ESPI/EBI)" | plain input |
| `f-from` | text→datetime-local | "Analizy od" | toggles on focus |
| `f-to` | text→datetime-local | "Analizy do" | toggles on focus |
| `f-page-size` | select | — | options: 20/50/100 |

**No autocomplete/datalist anywhere in the file.**

#### Storage (complete inventory)
| Store | Operations | Lines |
|-------|-----------|-------|
| `sessionStorage` | getItem('apiKey'), getItem('role') | 223–224 |
| `sessionStorage` | setItem('apiKey'), setItem('role') | 242–243 |
| `sessionStorage` | clear() on logout | 254 |
| `sessionStorage` | clear() on 401 | 361 |
| `localStorage` | **not used** | — |
| Cookies | **not used** | — |

**GDPR implication**: No cookies = no consent banner technically required by ePrivacy Directive. However, the PUL-25 scope explicitly calls for it. Recommend a simple `localStorage` flag (`gdpr_consent_v1`) — no backend needed.

#### All API Calls from Frontend
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/role` | GET | Validate API key, get role |
| `/announcements` | GET | Paginated filtered list |
| `/announcements/{id}` | DELETE | Admin delete |

**Missing autocomplete calls** — to be added: `/autocomplete/tickers`, `/autocomplete/companies`, `/autocomplete/event-types`

#### JavaScript Global Functions
`$`, `init`, `showLogin`, `showLoginError`, `showDashboard`, `renderHeaders`, `fetchAnnouncements`, `renderTable`, `deleteRow`, `openModal`, `closeModal`, `esc`, `parseDateOrNull`

**Key constraint**: `init()` must be called as the **last statement** of `<script>` (PUL-37 bug fix) — any JS additions must preserve this ordering.

#### CSS Color Palette (existing)
- Primary: `#2563eb` (blue), hover `#1d4ed8`
- Background: `#f4f6f8` (body), `#f9fafb` (table header)
- Error: `#dc2626` (red), Success: `#16a34a` (green), Warning: `#d97706` (orange)
- Mobile breakpoint: `max-width: 640px` (hides Score, Analysis, URL columns)

---

### 2. Backend — src/api.py

**FastAPI app factory**: `create_app()` at line 77  
**Static HTML**: loaded from disk at startup (`pathlib.Path("static/index.html").read_text()`), served as `HTMLResponse` at `GET /`  
**No StaticFiles mount** — single HTML file only

#### Auth mechanism (lines 18–33)
```python
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)  # line 18

def _get_role(key: str | None = Security(_API_KEY_HEADER)) -> Role:
    if key == os.environ.get("ADMIN_API_KEY"): return "admin"
    if key == os.environ.get("USER_API_KEY"):  return "user"
    raise HTTPException(status_code=401, ...)

def _require_admin(role: Role = Depends(_get_role)) -> Role: ...  # 403 if not admin
```

#### All current routes
| Route | Method | Auth | Line |
|-------|--------|------|------|
| `/health` | GET | None | 82 |
| `/` | GET | None | 86 |
| `/auth/role` | GET | `_get_role` | 90 |
| `/announcements` | GET | `_get_role` | 94 |
| `/announcements/{id}` | DELETE | `_require_admin` | 135 |

#### GET /announcements — query params (lines 94–133)
`page` (int, ≥1, default 1), `page_size` (int, 1–100, default 20), `ticker` (str|None), `company` (str|None), `event_type` (str|None), `from` (datetime|None), `to` (datetime|None)

**New endpoints needed for autocomplete** — none exist yet. Suggested pattern (matching existing conventions):
```python
@app.get("/autocomplete/tickers")
async def autocomplete_tickers(q: str | None = None, role: Role = Depends(_get_role)) -> list[str]:
    return await list_distinct_tickers(prefix=q)
```

**No CORS, no middleware** configured — not needed (single-origin app, same FastAPI serves HTML and API).

---

### 3. BigQuery layer — db/bigquery.py

#### Tables
| Table | Key columns |
|-------|------------|
| `announcements` | `ticker` (STRING, NULLABLE), `company` (STRING, NULLABLE), `event_type` (STRING, NULLABLE), `analysis_score` (FLOAT64), `structured_analysis` (STRING/JSON5), `analysis_approved` (BOOL) |
| `x_posts` | `x_post_id`, `window`, `post_text`, `tweet_ids`, `posted_at`, `x_publish_status` |
| `portfolio_snapshots` | `snapshot_id`, `wallet`, `snapshot_date`, `total_value` |

Dataset name: env var `BIGQUERY_DATASET` (read at module import time — `load_dotenv()` must come first).

#### Existing query patterns
- Parameterized via `QueryJobConfig(query_parameters=[ScalarQueryParameter(...)])` — consistent throughout
- `_build_filter_clauses()` (lines 514–541): builds WHERE for ticker/company/event_type/date
- Company filter: `LOWER(company) LIKE LOWER(@company)` with `%` prefix/suffix added by caller
- **No DISTINCT queries exist** — autocomplete needs 3 new functions

#### New BQ functions needed
```python
def list_distinct_tickers(prefix: str | None = None) -> list[str]:
    """SELECT DISTINCT ticker WHERE ticker IS NOT NULL [AND ticker LIKE @prefix] ORDER BY ticker"""

def list_distinct_companies(prefix: str | None = None) -> list[str]:
    """SELECT DISTINCT company WHERE company IS NOT NULL [AND LOWER(company) LIKE LOWER(@prefix||'%')] ORDER BY company LIMIT 50"""

def list_distinct_event_types() -> list[str]:
    """SELECT DISTINCT event_type WHERE event_type IS NOT NULL ORDER BY event_type"""
```

**Lesson from lessons.md**: `window` column in x_posts needs backticks in SQL — verify any new SQL column names against BigQuery reserved keywords list.

**No caching** in BQ layer. For autocomplete, recommend simple in-memory cache (TTL ~5 min) in `src/api.py` using `time.time()` — avoids repeated BQ round-trips on every keystroke.

#### Client init pattern (lines 82–102)
Thread-safe singleton with `with_quota_project` guard — existing pattern is correct, apply same to any new BQ function.

---

### 4. GDPR / Cookie Consent

**Current state**: zero cookies used; `sessionStorage` only. No consent mechanism exists.

**Recommended approach** (minimal, no backend):
- `localStorage.getItem('gdpr_consent_v1')` as consent flag
- Show banner on first load if flag absent
- On accept: `localStorage.setItem('gdpr_consent_v1', 'accepted')`, hide banner
- Banner: fixed bottom bar, simple "Ta strona używa sessionStorage..." + "Akceptuję" button
- **No new endpoint needed** for basic consent (stateless, client-side only)

If future PUL-28 (User profile) wants server-side consent tracking — that's out of scope for PUL-25.

---

### 5. Autocomplete — Design Options

**Option A — HTML `<datalist>`** (simplest)
- Add `<datalist id="dl-tickers">` under `#f-ticker`; fetch all values once on dashboard load
- Pros: native browser UX, zero JS library, accessible
- Cons: no custom styling, limited filtering control

**Option B — Custom dropdown div** (more control)
- On input focus/keyup: fetch `/autocomplete/tickers?q=...`, render `<div class="ac-dropdown">` below input
- Pros: fully styleable, consistent with app's CSS approach
- Cons: ~50 lines JS per field, keyboard navigation to implement

**Recommendation**: Option A (`<datalist>`) for tickers and event_types (finite, small lists); Option A or simple static list for event_types. Keeps single-file philosophy intact.

**Event types** — can be a static JS array (no BQ call needed):
```js
const EVENT_TYPES = ["wyniki_sprzedazowe", "skup_akcji", "zmiana_zarzadu", "compliance", "inne"];
```
These are defined in `src/analyzer.py` lines 15–20 and won't change without a code change.

---

## Code References

- `static/index.html:7-159` — embedded CSS (153 lines)
- `static/index.html:164-172` — login screen DOM
- `static/index.html:175-205` — dashboard DOM
- `static/index.html:184-192` — filter form inputs
- `static/index.html:219-482` — JavaScript block
- `static/index.html:223-224` — sessionStorage reads on init
- `static/index.html:234-247` — login handler (fetch `/auth/role`)
- `static/index.html:259-267` — popstate (back button) handler
- `static/index.html:269-273` — date input type toggle
- `static/index.html:341-377` — `fetchAnnouncements()` function
- `static/index.html:482` — `init()` call (must remain last)
- `src/api.py:18-33` — auth mechanism (`_get_role`, `_require_admin`)
- `src/api.py:45-74` — Pydantic response models (Admin / User)
- `src/api.py:77-88` — `create_app()`, static HTML serving
- `src/api.py:90-133` — `/auth/role` and `/announcements` routes
- `db/bigquery.py:42` — `_DATASET` (reads env at import time)
- `db/bigquery.py:82-102` — `_get_client()` singleton with quota project guard
- `db/bigquery.py:514-541` — `_build_filter_clauses()`
- `src/analyzer.py:15-20` — valid `event_type` values (static list)

---

## Architecture Insights

1. **Single-file frontend** — all HTML/CSS/JS in one file. Deliberate choice from PUL-17. Keep it; adding a build step would overcomplicate the setup.

2. **No StaticFiles mount** — `GET /` returns the HTML via `HTMLResponse`. If we ever need to serve images or icons for the redesign (e.g., logo SVG), we'd need to either embed them as base64 or add `app.mount("/static", StaticFiles(...))` — a minor change to `create_app()`.

3. **Auth is header-based, not cookie-based** — GDPR ePrivacy applies only to cookies/trackers. `sessionStorage` is session-scoped and doesn't require consent under current interpretation. A consent banner for PUL-25 is a UX/compliance gesture, not a strict legal requirement given the current storage approach.

4. **BQ queries are parameterized** — no SQL injection risk; new autocomplete queries should follow the same `ScalarQueryParameter` pattern.

5. **Event types are code-defined** — `src/analyzer.py` defines them as a Python list. Autocomplete for event_type should use a static JS array mirroring this list, not a BQ query.

6. **No caching layer** — every filter change hits BQ. Autocomplete endpoints will be called on every keystroke if not debounced. Plan: debounce in JS + short in-memory TTL cache server-side (or fetch once on login and cache in JS).

---

## Historical Context (from prior changes)

- `context/archive/2026-06-11-auth-public-url/` — established RBAC, single-file panel, sessionStorage auth, `create_app()` factory, inline CSS approach. Decision: no external CSS framework.
- `context/archive/2026-06-12-pagination/` — added `page`/`page_size` params, pagination UI, History API integration, Playwright E2E infrastructure.
- `context/archive/2026-06-16-dashboard-refresh-bug/` — fixed script load order: `init()` must be called last; `parseDateOrNull()` guard added.

**Active E2E tests**: `tests/e2e/` — Playwright with live-server fixture. PUL-25 must not break existing E2E tests.

---

## Open Questions

1. **Logo/branding**: Is there an SVG logo to embed in the login screen? If yes, it needs to be embedded as inline SVG or base64 (no StaticFiles yet). If no, pure CSS/text branding is fine.

2. **Autocomplete caching strategy**: Fetch all tickers once on dashboard load (if count is small, <500) or on-demand with debounce? Need to estimate cardinality of `DISTINCT ticker` in production BQ.

3. **GDPR banner scope**: Just sessionStorage acknowledgement, or also mention that Gemini AI processes announcement text? The latter would be more thorough for a public-facing tool.

4. **Event type display names**: `wyniki_sprzedazowe`, `zmiana_zarzadu` etc. are internal codes. Should autocomplete show human-readable Polish labels ("Wyniki sprzedażowe", "Zmiana zarządu")? Or raw codes?

5. **StaticFiles mount**: If we add a logo image or font, we need `app.mount("/static", ...)` in `create_app()`. Plan for this upfront if branding is in scope.
