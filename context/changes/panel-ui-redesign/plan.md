# Panel UI/UX Redesign Implementation Plan

## Overview

Redesign the puls-gpw admin/user panel to improve user experience in four areas: login screen visual polish, autocomplete dropdowns for filter fields, GDPR consent banner, and general visual improvements to the dashboard.

## Current State Analysis

The panel is a 485-line single-page HTML file (`static/index.html`) with 153 lines of inline CSS and vanilla JS — no external dependencies. FastAPI serves it as a static `HTMLResponse` from `create_app()`. BigQuery powers the data layer.

**What's missing:**
- Login screen: basic centered box, no branding beyond `<h1>puls-gpw</h1>`, no hint text
- Filters: three plain `<input type="text">` fields, no autocomplete suggestions
- GDPR: no consent banner (zero cookies/localStorage currently used)
- Visual: serviceable but unpolished — filters, table, modal, pagination all need CSS attention

## Desired End State

A polished, user-friendly panel where:
- First-time visitors see a clearly branded login screen with hint text
- A GDPR notice banner appears on first load and is dismissed via one click
- The Ticker and Company filter fields offer autocomplete suggestions (native `<datalist>`)
- The Event Type field offers Polish-labelled suggestions from a static JS map
- The dashboard, table, filters, and modal look visually consistent and refined

### Key Discoveries

- `static/index.html:482` — `init()` MUST remain the last `<script>` statement (PUL-37 constraint)
- `src/api.py:77-88` — HTML loaded from disk at startup; no StaticFiles mount; logo must be CSS-only or inline SVG
- `src/analyzer.py:15-20` — event_type values are hardcoded Python constants; autocomplete uses a static JS map (no BQ query needed)
- `db/bigquery.py:42` — `_DATASET` reads env at import time; `load_dotenv()` must precede any `db.*` import
- `db/bigquery.py:82-102` — BQ client: thread-safe singleton with `with_quota_project` guard; follow same pattern for new functions

## What We're NOT Doing

- No external CSS framework (Bootstrap, Tailwind) — stays inline CSS per established pattern
- No JS bundler or build step — single-file philosophy maintained
- No StaticFiles mount — no image files; branding is CSS/text only
- No backend GDPR persistence — consent flag lives in `localStorage` only
- No BQ query for event_types — they're defined in code, served as a static JS map
- No cursor-based pagination or virtual scroll — existing offset-based pagination is sufficient
- No server-side BQ query caching library — simple in-memory Python dict with TTL

## Implementation Approach

Work in five sequential phases with one clear dependency: Phase 3 (autocomplete frontend) needs Phase 1 (autocomplete endpoints) to be deployed or running locally. Phases 2 and 4 (login/GDPR and visual polish) are independent of the backend work and can be developed in parallel with Phase 1.

## Critical Implementation Details

**In-memory autocomplete cache**: New endpoints in `src/api.py` need a module-level dict with TTL, not a library. Pattern:
```python
import time
_AC_CACHE: dict[str, tuple[list[str], float]] = {}
_AC_TTL = 300  # 5 minutes

def _ac_get(key: str) -> list[str] | None:
    if key in _AC_CACHE:
        data, ts = _AC_CACHE[key]
        if time.time() - ts < _AC_TTL:
            return data
    return None

def _ac_set(key: str, data: list[str]) -> None:
    _AC_CACHE[key] = (data, time.time())
```

**Event type label mapping + reverse lookup**: Because the `<datalist>` shows Polish labels but the API expects raw codes, the submit handler must translate back. Keep two dicts in JS (labels → codes, codes → labels). On `fetchAnnouncements()`, translate the event_type value before building `URLSearchParams`.

**`init()` ordering**: Any new JS function that should run at startup (e.g., `initGdpr()`, `loadAutocomplete()`) must be called from inside `showDashboard()` or `init()`, not as a top-level call — to preserve the "init() last" invariant.

---

## Phase 1: Backend — BQ + FastAPI Autocomplete Endpoints

### Overview

Add two new BQ query functions and two new FastAPI endpoints that serve distinct ticker and company name lists for autocomplete. Includes a simple 5-minute in-memory cache to avoid BQ round-trips on every dashboard load.

### Changes Required

#### 1. BQ autocomplete functions

**File**: `db/bigquery.py`

**Intent**: Add `list_distinct_tickers()` and `list_distinct_companies()` following the existing parameterized query pattern. Both return sorted `list[str]`, cap companies at 500 rows to bound response size.

**Contract**:
```python
def list_distinct_tickers() -> list[str]: ...
def list_distinct_companies() -> list[str]: ...
```
Queries: `SELECT DISTINCT ticker/company FROM ... WHERE ticker/company IS NOT NULL ORDER BY ticker/company [LIMIT 500]`. Use `ScalarQueryParameter` for any filter params (none needed for these full-list queries). Follow `_get_client()` pattern exactly.

#### 2. FastAPI autocomplete endpoints + cache

**File**: `src/api.py`

**Intent**: Add `GET /autocomplete/tickers` and `GET /autocomplete/companies`, both requiring `_get_role` auth and returning `list[str]`. Add module-level cache dict with 5-minute TTL (see Critical Implementation Details above).

**Contract**:
```python
@app.get("/autocomplete/tickers")
async def autocomplete_tickers(role: Role = Depends(_get_role)) -> list[str]: ...

@app.get("/autocomplete/companies")
async def autocomplete_companies(role: Role = Depends(_get_role)) -> list[str]: ...
```
On each call: check cache → return cached if fresh → otherwise call BQ function, store in cache, return.

#### 3. Unit tests for BQ functions

**File**: `tests/test_bigquery.py` (or new `tests/test_autocomplete.py`)

**Intent**: Add tests for `list_distinct_tickers()` and `list_distinct_companies()` following the existing mock-client pattern. Verify correct SQL structure (SELECT DISTINCT, ORDER BY, IS NOT NULL) and empty-result handling.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ --tb=short` passes
- `GET /autocomplete/tickers` with valid `X-API-Key` header returns 200 + JSON array

#### Manual Verification

- Start server locally (`uv run python api_main.py`), call `GET /autocomplete/tickers` with `curl` or browser → list of strings returned
- Call twice within 5 minutes → second call returns identical result (cache hit — verify via server log absence of second BQ call)

**Implementation Note**: After automated tests pass, confirm manual endpoint call works before proceeding to Phase 3 (which depends on these endpoints).

---

## Phase 2: Login Screen Redesign + GDPR Banner

### Overview

Independent of Phase 1. Restyle the login card for better visual hierarchy and add hint text for new users. Add a GDPR consent banner that appears on first load and is dismissed via `localStorage`.

### Changes Required

#### 1. Login screen CSS

**File**: `static/index.html` (CSS block, lines 7–159)

**Intent**: Polish `.login-box` and related selectors. Improve padding, shadow, typography. Add `.login-brand`, `.login-hint`, and `.login-error` styled variants.

**Contract**:
- `.login-box`: `max-width: 420px`, `padding: 2.5rem`, `border-radius: 12px`, `box-shadow: 0 4px 24px rgba(0,0,0,0.1)`, `background: #fff`
- `.login-brand h1`: larger font size (2rem), primary color `#2563eb`, `margin-bottom: 0.25rem`
- `.login-brand p` (subtitle): small grey text, `font-size: 0.85rem`, `color: #6b7280`
- `.login-hint`: muted text below input, `font-size: 0.8rem`, `color: #6b7280`, `margin-top: -0.25rem`
- Input: `width: 100%`, `padding: 0.75rem`, `border-radius: 8px`, `border: 1.5px solid #d1d5db`, focus ring `#2563eb`
- Button: `width: 100%`, `padding: 0.75rem`, more prominent shadow on hover

#### 2. Login screen HTML

**File**: `static/index.html` (DOM, lines 164–172)

**Intent**: Add `.login-brand` div with subtitle and `.login-hint` paragraph below the input. Preserve all existing IDs (`api-key-input`, `login-btn`, `login-error`) — JS depends on them.

**Contract**:
```html
<div class="login-box">
  <div class="login-brand">
    <h1>puls-gpw</h1>
    <p>Analizator komunikatów ESPI / EBI</p>
  </div>
  <label>Klucz API</label>
  <input id="api-key-input" type="password" placeholder="Wpisz klucz API" autocomplete="current-password">
  <p class="login-hint">Klucz API otrzymasz od administratora systemu.</p>
  <button id="login-btn">Zaloguj się</button>
  <div id="login-error" style="display:none">Nieprawidłowy klucz API</div>
</div>
```

#### 3. GDPR banner CSS

**File**: `static/index.html` (CSS block)

**Intent**: Fixed bottom bar that overlays content, with text + dismiss button.

**Contract**:
- `#gdpr-banner`: `position: fixed; bottom: 0; left: 0; right: 0; background: #1e293b; color: #e2e8f0; display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1.5rem; z-index: 200; font-size: 0.85rem;`
- `#gdpr-accept`: button styled differently from main buttons — white text, transparent bg, border, small

#### 4. GDPR banner HTML + JS

**File**: `static/index.html` (DOM + script block)

**Intent**: Add `#gdpr-banner` div (initially `display:none`). Add `initGdpr()` function: checks `localStorage.getItem('gdpr_consent_v1')`; if null → show banner. Accept button sets localStorage flag + hides banner. Call `initGdpr()` from inside `init()` (not as a standalone top-level call — preserve `init()` last rule).

**Contract**:
```html
<div id="gdpr-banner" style="display:none">
  <span>Ta strona używa sessionStorage do przechowywania klucza sesji. Nie stosujemy plików cookie ani śledzenia.</span>
  <button id="gdpr-accept">Rozumiem</button>
</div>
```
```js
function initGdpr() {
  if (!localStorage.getItem('gdpr_consent_v1')) {
    $('gdpr-banner').style.display = 'flex';
    $('gdpr-accept').addEventListener('click', () => {
      localStorage.setItem('gdpr_consent_v1', 'accepted');
      $('gdpr-banner').style.display = 'none';
    });
  }
}
// Called inside init() before showDashboard()
```

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ --tb=short` passes (no regressions)

#### Manual Verification

- Login screen: branded header visible, hint text below input, button full-width, error state shows styled message
- GDPR: clear `localStorage`, reload → banner appears at bottom; click "Rozumiem" → banner disappears; reload → banner absent
- Responsive: login card looks correct on 375px viewport

---

## Phase 3: Dashboard Autocomplete

### Overview

Add `<datalist>` elements to the Ticker, Company, and Event Type filter fields. Fetch ticker + company lists from Phase 1 endpoints on `showDashboard()`. Populate Event Type list from a static JS map with Polish labels. Update `fetchAnnouncements()` to reverse-translate Polish event type label back to API code.

### Changes Required

#### 1. Datalist HTML elements

**File**: `static/index.html` (filter form, lines 184–192)

**Intent**: Add `list="..."` attribute to `f-ticker`, `f-company`, `f-event-type` inputs; add corresponding `<datalist>` elements immediately after each input inside the filter form.

**Contract**:
```html
<input id="f-ticker" ... list="dl-tickers">
<datalist id="dl-tickers"></datalist>

<input id="f-company" ... list="dl-companies">
<datalist id="dl-companies"></datalist>

<input id="f-event-type" ... list="dl-event-types">
<datalist id="dl-event-types"></datalist>
```

#### 2. Event type label map

**File**: `static/index.html` (script block, near top of JS, before function definitions)

**Intent**: Define two dicts — `EVENT_TYPE_LABELS` (code → Polish label) and `EVENT_TYPE_CODES` (Polish label → code). The labels dict drives datalist population; the codes dict translates user input back before API call.

**Contract**:
```js
const EVENT_TYPE_LABELS = {
  wyniki_sprzedazowe: 'Wyniki sprzedażowe',
  skup_akcji: 'Skup akcji',
  zmiana_zarzadu: 'Zmiana zarządu',
  compliance: 'Compliance',
  inne: 'Inne',
};
const EVENT_TYPE_CODES = Object.fromEntries(
  Object.entries(EVENT_TYPE_LABELS).map(([k, v]) => [v, k])
);
```

#### 3. `loadAutocomplete()` function

**File**: `static/index.html` (script block)

**Intent**: Fetch `/autocomplete/tickers` and `/autocomplete/companies` in parallel (Promise.all), populate datalists. Also populate `dl-event-types` from `EVENT_TYPE_LABELS`. Called from `showDashboard()`.

**Contract**: Two `fetch()` calls with `X-API-Key` header (same pattern as `fetchAnnouncements()`). On success, for each value create `<option value="...">` and append to the datalist. Populate `dl-event-types` from `Object.values(EVENT_TYPE_LABELS)`. On error: log to console, don't break dashboard.

#### 4. Update `fetchAnnouncements()` to translate event type

**File**: `static/index.html` (script block, `fetchAnnouncements()`, lines 341–377)

**Intent**: Before building `URLSearchParams`, translate the `f-event-type` value: if it matches a Polish label, convert to the raw code; otherwise pass through unchanged (handles both direct code entry and datalist selection).

**Contract**: One extra line before param building:
```js
const rawEvent = $('f-event-type').value.trim();
const eventParam = EVENT_TYPE_CODES[rawEvent] || rawEvent;
// use eventParam instead of rawEvent in URLSearchParams
```

#### 5. Call `loadAutocomplete()` from `showDashboard()`

**File**: `static/index.html` (`showDashboard()` function, lines 306–313)

**Intent**: Add `loadAutocomplete()` call at the end of `showDashboard()`, after `renderHeaders()` and before `fetchAnnouncements()`.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ --tb=short` passes
- E2E test `test_autocomplete.py` passes (see Phase 5)

#### Manual Verification

- Log in → open Ticker filter → type 1-2 letters → datalist suggestions appear with real tickers from BQ
- Company field: same experience with company names
- Event type field: dropdown shows "Wyniki sprzedażowe", "Skup akcji" etc.; selecting one and submitting filters table correctly

---

## Phase 4: Visual Polish

### Overview

CSS and JS improvements to the dashboard: filter form layout, table rows, pagination buttons, and modal content structure. No functional changes — purely presentational.

### Changes Required

#### 1. Filter form polish

**File**: `static/index.html` (CSS block)

**Intent**: Better visual grouping of filter inputs, subtle border radius, aligned labels, add a search icon to the submit button (Unicode ⌕ or 🔍 via CSS content).

**Contract**:
- `.filters input, .filters select`: `border-radius: 6px; padding: 0.45rem 0.65rem; border: 1.5px solid #d1d5db;`
- Filter submit button: add `::before` pseudo-element with `content: '⌕  '` for search icon, or just prepend a magnifier character to button text: `"🔍 Filtruj"`
- Filter form overall: `gap: 0.5rem` on flex container for consistent spacing

#### 2. Table + pagination polish

**File**: `static/index.html` (CSS block)

**Intent**: Increase row padding slightly, improve the alternating row stripe contrast, give pagination buttons a more modern look (pill-style, disabled state clearer).

**Contract**:
- `tbody tr td`: `padding: 0.6rem 0.75rem` (up from `0.4rem 0.5rem`)
- `tbody tr:nth-child(even)`: `background: #f8fafc` (subtler stripe)
- `#pagination-bar button`: `border-radius: 999px; padding: 0.4rem 1rem; font-weight: 500;`
- Disabled button: `opacity: 0.35; cursor: not-allowed;`
- `#page-label`: `font-size: 0.875rem; color: #64748b; font-weight: 500`

#### 3. Modal content structure

**File**: `static/index.html` (script block, `openModal()` function, lines 449–459; CSS block)

**Intent**: Instead of dumping raw JSON into `.modal-body`, render `structured_analysis` as readable sections: summary, sentiment badge, key numbers list. Fall back to raw text if parsing fails.

**Contract**: In `openModal(d)`, after parsing `d.structured_analysis` (already done via JSON5 in the API, arrives as an object):
```js
// structured_analysis is already a parsed dict from the dataset
let analysis;
try { analysis = JSON.parse(d.structuredAnalysis || 'null'); } catch { analysis = null; }

const bodyHtml = analysis ? `
  <div class="modal-section">
    <h4>Podsumowanie</h4>
    <p>${esc(analysis.summary_pl || '—')}</p>
  </div>
  <div class="modal-section">
    <h4>Sentyment</h4>
    <span class="sentiment-badge sentiment-${esc(analysis.sentiment || 'neutral')}">${esc(analysis.sentiment || '—')}</span>
  </div>
  <div class="modal-section">
    <h4>Kluczowe liczby</h4>
    <p>${esc(Array.isArray(analysis.key_numbers) ? analysis.key_numbers.join(' · ') : (analysis.key_numbers || '—'))}</p>
  </div>
` : `<pre>${esc(d.structuredAnalysis || '—')}</pre>`;
$('modal-body').innerHTML = bodyHtml;
```

Add CSS for `.modal-section`, `.modal-section h4`, `.sentiment-badge`:
- `.modal-section`: `margin-bottom: 1rem`
- `.modal-section h4`: `font-size: 0.75rem; text-transform: uppercase; color: #6b7280; margin-bottom: 0.25rem`
- `.sentiment-badge`: `display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.8rem; background: #e2e8f0`

### Success Criteria

#### Automated Verification

- `uv run pytest tests/e2e/ --tb=short` passes — all existing E2E tests still green (no regressions)

#### Manual Verification

- Dashboard filter form: inputs visually aligned and consistent, submit button has search icon
- Table: row padding is comfortable, hover state clear, alternating rows subtle
- Pagination: buttons rounded and styled, disabled state obvious
- Modal: structured analysis displays as sections (Summary, Sentiment, Key Numbers), not as raw JSON
- Mobile (375px): filter form still stacks correctly, modal still snaps to bottom

---

## Phase 5: E2E Tests

### Overview

Add Playwright E2E tests covering the three new UI behaviours: login screen quality, GDPR banner lifecycle, and autocomplete suggestions.

### Changes Required

#### 1. Login screen UX test

**File**: `tests/e2e/test_login_ux.py`

**Intent**: Verify the redesigned login screen has all expected elements: brand subtitle, hint text, and error state feedback.

**Contract**: Test checks `page.locator('.login-brand')` is visible, `page.locator('.login-hint')` contains hint text, entering a wrong API key shows `#login-error`. Use existing live-server fixture from `conftest.py`.

#### 2. GDPR banner lifecycle test

**File**: `tests/e2e/test_gdpr.py`

**Intent**: Verify the banner appears on first visit (no localStorage), is dismissed on click, and does not reappear after dismissal.

**Contract**:
- Step 1: clear localStorage → load page → `#gdpr-banner` visible
- Step 2: click `#gdpr-accept` → `#gdpr-banner` hidden
- Step 3: reload page → `#gdpr-banner` still hidden (localStorage flag persists)

Note: Use `page.evaluate("localStorage.clear()")` before the test to reset state. Clean up after test with `page.evaluate("localStorage.removeItem('gdpr_consent_v1')")`.

#### 3. Autocomplete datalist test

**File**: `tests/e2e/test_autocomplete.py`

**Intent**: Verify that after login, the ticker and company datalists are populated (options present), and that selecting an event type Polish label and submitting applies the correct API filter.

**Contract**:
- Login with test API key → wait for dashboard
- `page.evaluate("document.querySelector('#dl-tickers').options.length")` should be > 0
- `page.evaluate("document.querySelector('#dl-companies').options.length")` should be > 0
- `page.evaluate("document.querySelector('#dl-event-types').options.length")` should be == 5
- Fill `f-event-type` with "Wyniki sprzedażowe" → submit filter → verify network request contains `event_type=wyniki_sprzedazowe` (intercept via `page.on('request', ...)`)

### Success Criteria

#### Automated Verification

- `uv run pytest tests/e2e/ --tb=short -v` passes — all 3 new test files green
- Existing E2E tests still green

#### Manual Verification

- Run full E2E suite locally; all tests pass in Chromium

---

## Testing Strategy

### Unit Tests

- `test_bigquery.py`: `list_distinct_tickers()` — mock client returns rows → function returns sorted list; empty result → empty list
- `test_bigquery.py`: `list_distinct_companies()` — same pattern; verify LIMIT 500 in SQL string
- `test_api.py`: `GET /autocomplete/tickers` with valid key → 200; with no key → 401; cache hit (second call within TTL) → same result without second BQ call

### Integration Tests

- None beyond E2E — the autocomplete endpoints are thin wrappers around BQ; the E2E tests cover the full stack

### Manual Testing Steps

1. Login screen: open in incognito → verify branding, hint text, error message on wrong key
2. GDPR: clear localStorage → reload → banner shows; click dismiss → reload → no banner
3. Ticker autocomplete: type "PKO" → native datalist dropdown appears with matching tickers
4. Company autocomplete: type "Bank" → company suggestions appear
5. Event type: open field → see 5 Polish labels; select one → submit → table shows filtered results; check Network tab for `event_type=wyniki_sprzedazowe` (not the Polish label)
6. Modal: click any row → modal opens with Podsumowanie / Sentyment / Kluczowe liczby sections
7. Visual: compare all four polish focus areas against current state

## Performance Considerations

- Autocomplete fetch at login adds ~2 BQ round-trips; at ~100-300ms each, total login time increases by ~200-600ms — acceptable
- Server-side 5-minute TTL cache means BQ is queried at most 12×/hour for tickers, 12×/hour for companies — negligible cost
- Datalist with 500 entries (max companies) is fine for browser rendering

## Migration Notes

None. All changes are frontend (single HTML file) and new endpoints/functions — no schema changes, no data migrations, no breaking API changes.

## References

- Research: `context/changes/panel-ui-redesign/research.md`
- Auth/RBAC origin: `context/archive/2026-06-11-auth-public-url/`
- Pagination: `context/archive/2026-06-12-pagination/`
- PUL-37 script-order fix: `context/archive/2026-06-16-dashboard-refresh-bug/`
- `static/index.html:482` — `init()` must remain last

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — BQ + FastAPI Autocomplete Endpoints

#### Automated

- [x] 1.1 `uv run pytest tests/ --tb=short` passes — cc0ce4c
- [x] 1.2 `GET /autocomplete/tickers` with valid `X-API-Key` returns 200 + JSON array — cc0ce4c

#### Manual

- [x] 1.3 Server started locally; curl to `/autocomplete/tickers` returns list of strings
- [x] 1.4 Second call within 5 min returns identical result (cache hit confirmed)

### Phase 2: Login Screen Redesign + GDPR Banner

#### Automated

- [x] 2.1 `uv run pytest tests/ --tb=short` passes (no regressions) — ffa20df

#### Manual

- [x] 2.2 Login screen: branded header, hint text, full-width button, styled error message — ffa20df
- [x] 2.3 GDPR banner appears on first load, dismissed on click, does not reappear after reload — ffa20df
- [x] 2.4 Login card looks correct on 375px viewport — ffa20df

### Phase 3: Dashboard Autocomplete

#### Automated

- [x] 3.1 `uv run pytest tests/ --tb=short` passes — 866fb33
- [ ] 3.2 E2E test `test_autocomplete.py` passes

#### Manual

- [x] 3.3 Ticker field shows datalist suggestions from BQ — 866fb33
- [x] 3.4 Company field shows datalist suggestions from BQ — 866fb33
- [x] 3.5 Event type field shows 5 Polish labels; selection submits correct API code — 866fb33

### Phase 4: Visual Polish

#### Automated

- [x] 4.1 `uv run pytest tests/e2e/ --tb=short` passes — no regressions — b7425a4

#### Manual

- [x] 4.2 Filter form visually polished (consistent spacing, search icon) — b7425a4
- [x] 4.3 Table rows comfortable padding, hover state clear — b7425a4
- [x] 4.4 Pagination buttons rounded/styled, disabled state obvious — b7425a4
- [x] 4.5 Modal displays analysis as sections (not raw JSON) — b7425a4
- [x] 4.6 Mobile (375px) layout still correct — b7425a4

### Phase 5: E2E Tests

#### Automated

- [x] 5.1 `uv run pytest tests/e2e/test_login_ux.py --tb=short` passes — 2f1ed78
- [x] 5.2 `uv run pytest tests/e2e/test_gdpr.py --tb=short` passes — 2f1ed78
- [x] 5.3 `uv run pytest tests/e2e/test_autocomplete.py --tb=short` passes — 2f1ed78
- [x] 5.4 Full E2E suite still green: `uv run pytest tests/e2e/ --tb=short` — 2f1ed78

#### Manual

- [x] 5.5 All new E2E tests pass locally in Chromium — 2f1ed78
