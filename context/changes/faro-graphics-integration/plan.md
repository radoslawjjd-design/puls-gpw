# Faro Graphics Integration — Implementation Plan

## Overview

Wire up the existing `static/img/faro-banner.jpg` (1280×720, lighthouse illustration with the tagline "FARO — Wskazuje drogę w gąszczu raportów") into the login screen, the only placement in scope per design decisions. The banner sits edge-to-edge at the top of `.login-box`, replacing the redundant text-only `<h1>Faro</h1>` brand mark while keeping the functional subtitle. No new image assets are produced; the existing file is referenced as-is.

## Current State Analysis

- `static/index.html` is a single file with inline `<style>` (lines 12–308+) — no separate CSS files, no CSS custom properties, hardcoded hex palette (primary blue `#2563eb`, white surfaces `#fff`, page background `#f4f6f8`).
- `static/img/faro-banner.jpg` was dropped into the repo in PR #95 (commit `de4141a`) alongside the favicon/manifest set, but is not referenced anywhere in HTML/CSS — explicitly out of scope for that PR.
- No `<img>` tags or `background-image` CSS rules exist anywhere in `static/index.html` today — this is the first usage of a content image, so there's no established pattern to follow, only the asset-path convention (`/static/img/...`, absolute path).
- Login screen markup (`static/index.html:312-325`):
  ```html
  <div id="login-screen">
    <div class="login-box">
      <div class="login-brand">
        <h1>Faro</h1>
        <p>Analizator komunikatów ESPI / EBI</p>
      </div>
      ...
    </div>
  </div>
  ```
- Login screen CSS (`static/index.html:21-27`):
  ```css
  .login-box {
    background: #fff; border-radius: 12px; padding: 2.5rem;
    box-shadow: 0 4px 24px rgba(0,0,0,.1); width: 100%; max-width: 420px;
  }
  .login-brand { margin-bottom: 1.75rem; }
  .login-brand h1 { font-size: 2rem; color: #2563eb; margin-bottom: 0.25rem; font-weight: 700; }
  .login-brand p { font-size: 0.85rem; color: #6b7280; }
  ```
- `tests/e2e/test_login_ux.py:9` asserts `.login-brand h1` has text `"Faro"` — this assertion will break once the `<h1>` is removed and must be updated in the same change.
- Other E2E tests that reference "Faro" headings (`test_x_post_history.py:60`, `test_profile_menu.py:46`, `test_url_routing.py:48`) target `#topbar-home` (post-login dashboard heading, `static/index.html:330`), not the login screen — unaffected by this change.
- Tests asserting `#login-screen` visibility (`test_idle_timeout.py`, `test_url_routing.py:118`) only check the container, not its internals — unaffected.

## Desired End State

The login screen shows the Faro banner image spanning the full width of `.login-box`, flush with its top edge and rounded corners. Below it, the existing functional copy ("Analizator komunikatów ESPI / EBI"), API key field, hint, and button remain unchanged in behavior. The redundant `<h1>Faro</h1>` text mark is gone — the banner itself carries the brand name. The banner scales responsively with the box on narrow viewports (no separate mobile asset, no media query needed beyond the existing fluid `width: 100%` box). `tests/e2e/test_login_ux.py` passes against the new DOM.

**Verification**: `uv run pytest tests/e2e/test_login_ux.py` passes; manual visual check at desktop and mobile (≤480px) viewport widths shows the banner sized correctly with no layout breakage or content overlap.

### Key Discoveries:

- `static/index.html:127-141` (`.table-wrap` rules) confirmed untouched — table backdrop placement was explicitly ruled out during planning, no risk to data-table readability/performance.
- `.login-box` currently applies padding uniformly (`padding: 2.5rem`); an edge-to-edge banner requires moving that padding onto an inner content wrapper while the box itself gets `overflow: hidden` to clip the banner to the box's `border-radius: 12px`.

## What We're NOT Doing

- No table backdrop / watermark on `.table-wrap` (announcements, x-history, my-wallet) — login screen only.
- No new image asset generation — no WebP conversion, no resized/cropped mobile variant, no compression pass on `faro-banner.jpg`. The 349KB JPEG is used as-is.
- No descriptive/tagline `alt` text — the banner is marked decorative (`alt=""`) since the brand name and tagline are visual-only additions; the functional subtitle text remains separately in the DOM.
- No changes to the topbar `#topbar-home` heading or any other "Faro" text elsewhere in the app.
- No changes to `static/img/site.webmanifest`, favicon set, or any PUL-57 deliverable.

## Implementation Approach

Single-file, single-phase change: restructure the login screen's HTML to introduce a banner `<img>` plus a padded inner content wrapper, adjust the `.login-box`/`.login-brand` CSS accordingly, remove the now-redundant `<h1>`, and fix the one E2E assertion that targets the removed element. All changes are additive/structural to existing inline CSS — no new files, no build step changes.

## Phase 1: Faro banner on the login screen

### Overview

Add the banner image to `.login-box`, restructure CSS so the image sits edge-to-edge above a padded content area, drop the duplicate `<h1>Faro</h1>`, and update the one E2E test asserting on it.

### Changes Required:

#### 1. Login screen markup

**File**: `static/index.html` (lines 312-325)

**Intent**: Insert the banner image as the first child of `.login-box`, remove the `<h1>Faro</h1>` brand mark (redundant once the banner carries the name visually), keep the subtitle, and wrap the remaining content (brand subtitle, label, input, hint, button, error) in a new padded container so the banner can sit flush with the box edges.

**Contract**: `.login-box` gains a new first child `<img class="login-banner" src="/static/img/faro-banner.jpg" alt="">` (decorative, empty alt). A new wrapper `<div class="login-box-body">` contains everything currently inside `.login-box` except the removed `<h1>`. `.login-brand` keeps wrapping the `<p>` subtitle only.

#### 2. Login screen CSS

**File**: `static/index.html` (lines 21-27)

**Intent**: Move padding off `.login-box` onto the new `.login-box-body` wrapper, clip the banner's top corners to match the box's `border-radius`, size the banner responsively while reserving its 16:9 aspect ratio up front (avoids a layout jump once the 349KB image finishes loading), widen the box slightly to better fit the banner without excessive cropping, and drop the now-unused `.login-brand h1` rule.

**Contract**:
```css
.login-box {
  background: #fff; border-radius: 12px; overflow: hidden;
  box-shadow: 0 4px 24px rgba(0,0,0,.1); width: 100%; max-width: 480px;
}
.login-banner { width: 100%; height: auto; aspect-ratio: 1280 / 720; display: block; }
.login-box-body { padding: 2rem 2.5rem 2.5rem; }
.login-brand { margin-bottom: 1.5rem; }
.login-brand p { font-size: 0.85rem; color: #6b7280; }
```
(`.login-brand h1` rule removed — no longer has a matching element.)

#### 3. E2E test update

**File**: `tests/e2e/test_login_ux.py` (lines 6-12)

**Intent**: Replace the assertion on the now-removed `.login-brand h1` with an assertion that the banner image renders, keeping the rest of the test's coverage (brand block, subtitle, hint, hint text) intact.

**Contract**: `test_login_screen_has_brand_and_hint` drops `expect(page.locator(".login-brand h1")).to_have_text("Faro")` and adds `expect(page.locator(".login-banner")).to_be_visible()` before the existing `.login-brand` checks.

### Success Criteria:

#### Automated Verification:

- E2E login tests pass: `uv run pytest tests/e2e/test_login_ux.py`
- No regression in adjacent E2E suites that touch `#login-screen` or post-login "Faro" headings: `uv run pytest tests/e2e/test_idle_timeout.py tests/e2e/test_url_routing.py tests/e2e/test_x_post_history.py tests/e2e/test_profile_menu.py`
- Full E2E suite green: `uv run pytest tests/e2e/`

#### Manual Verification:

- Banner renders correctly on the login screen at desktop width (~1280px+ viewport)
- Banner scales down correctly on a narrow viewport (≤480px) without overflow, distortion, or overlapping the form fields below it
- Box corners remain visually rounded (banner doesn't bleed past the `border-radius`)
- Login flow still works end-to-end (valid key logs in, invalid key shows `#login-error`)
- No console errors/warnings related to the new `<img>` (e.g. broken path, layout shift)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding.

---

## Testing Strategy

### Integration Tests:

- `tests/e2e/test_login_ux.py::test_login_screen_has_brand_and_hint` — updated to assert banner visibility instead of the removed `<h1>` text.
- `tests/e2e/test_login_ux.py::test_wrong_api_key_shows_error` — unaffected, re-run as a regression check since it shares the login screen DOM.

### Manual Testing Steps:

1. Load the panel fresh (cleared session) at a desktop viewport — confirm the banner displays above the login form, rounded corners intact, no layout shift after image load.
2. Resize the browser down to a mobile width (e.g. 375px) — confirm the banner scales down proportionally with the box and the form remains usable below it.
3. Attempt login with a wrong API key — confirm `#login-error` still displays correctly beneath the (now repositioned) form content.
4. Attempt login with a valid key — confirm successful navigation to the dashboard is unaffected.

## Performance Considerations

The banner is served as-is (349KB JPEG, no compression or resizing pass — explicitly out of scope per planning decision). It loads once per login screen view and is not repeated elsewhere in the app, so the one-time transfer cost is accepted as-is.

## References

- Ticket: [PUL-58](https://linear.app/puls-gpw/issue/PUL-58/integrate-faro-graphics-into-the-ui-banner-table-backdrop-etc)
- Prerequisite: PUL-57 rebrand (commit `de4141a`, PR #95) — favicon/manifest/branding strings, explicitly left banner placement out of scope.
- Asset: `static/img/faro-banner.jpg`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Faro banner on the login screen

#### Automated

- [x] 1.1 E2E login tests pass: `uv run pytest tests/e2e/test_login_ux.py`
- [x] 1.2 No regression in adjacent E2E suites (`test_idle_timeout.py`, `test_url_routing.py`, `test_x_post_history.py`, `test_profile_menu.py`)
- [x] 1.3 Full E2E suite green: `uv run pytest tests/e2e/`

#### Manual

- [x] 1.4 Banner renders correctly at desktop width
- [x] 1.5 Banner scales correctly at narrow/mobile width without overflow or overlap
- [x] 1.6 Box corners remain rounded (banner doesn't bleed past border-radius)
- [x] 1.7 Login flow (valid key + invalid key error) still works end-to-end
- [x] 1.8 No console errors related to the new image
