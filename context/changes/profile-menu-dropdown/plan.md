# Profile Menu Dropdown Implementation Plan

## Overview

Turn the static `👤 admin` / `👤 user` role badge in the dashboard topbar into a clickable button that opens a dropdown ("hamburger"-style) menu, and move the existing "Wyloguj" (logout) button inside that menu as its first (and currently only) entry. This is a foundation shell — PUL-43, PUL-44, PUL-45, and PUL-28 will each add their own menu entry on top of it later.

## Current State Analysis

`static/index.html` is a single-file vanilla HTML/CSS/JS dashboard with no build step and no frontend test framework. The topbar (`static/index.html:236-242`) currently renders:

```html
<div class="topbar">
  <h1>puls-gpw</h1>
  <div>
    <span id="role-badge"></span>
    <button id="logout-btn">Wyloguj</button>
  </div>
</div>
```

`role-badge` is a plain, non-interactive `<span>`; `logout-btn` is a standalone sibling button bound directly to `doLogout()`.

## Desired End State

The role badge is a clickable trigger button (keeps the `👤 admin` / `👤 user` text, gains a hamburger glyph). Clicking it opens a popover menu anchored under the button, containing "Wyloguj" as a `role="menuitem"` entry. The menu closes on Escape, on outside click, or after activating an item. No standalone logout button remains in the topbar. Behavior and layout are identical on mobile and desktop (single anchored-popover implementation, no separate mobile layout). Verify by: logging in as both `admin` and `user`, opening/closing the menu via mouse and keyboard, confirming logout still works, and confirming no second logout control exists outside the menu.

### Key Discoveries:

- `static/index.html:236-242` — current topbar markup to replace.
- `static/index.html:90-103` (`.ac-dropdown`) — existing dropdown precedent in this codebase; toggles via `style.display`, no ARIA roles.
- `static/index.html:122-156` and `:734-740` (`.modal-overlay` / `closeModal`) — existing precedent for Escape-closes + click-outside-closes, and the only place in the app using ARIA (`role="dialog" aria-modal="true"`).
- `static/index.html:501-503` — `logout-btn` click handler calls `doLogout()`; must keep working unchanged.
- `static/index.html:555` — `showDashboard()` sets `$('role-badge').textContent`; `role-badge` must keep its id and remain a text-bearing element for this to keep working.
- `static/index.html:740` — existing global `keydown` listener for Escape closes the modal; the new Escape handling for the profile menu is additive, not a replacement.

## What We're NOT Doing

- Not adding menu entries for PUL-43 (XTB upload), PUL-44 (X post history), PUL-45 (treemap), or PUL-28 (My Wallet) — those land in their own tickets.
- Not changing `doLogout()` or any auth/session logic.
- Not adding open/close animations or transitions — matches the plain `display` toggle used by `.ac-dropdown` and `.modal-overlay`.
- Not implementing full WAI-ARIA roving-tabindex / arrow-key menu navigation — Tab-order navigation only (see Critical Implementation Details).
- Not building a separate mobile bottom-sheet layout — the same anchored popover renders at every viewport width.

## Implementation Approach

Single phase, single file. Wrap the existing `role-badge` span inside a new trigger `<button>` (hamburger glyph + existing role text), add a `role="menu"` list as a sibling containing the relocated `logout-btn` as a `role="menuitem"`, and wrap both in a positioning container so the menu can be anchored under the trigger with plain CSS `position: absolute`. JS adds an open/close toggle that manages `aria-expanded`, moves focus into the menu on open and back to the trigger on close, and closes on Escape or outside click.

## Critical Implementation Details

**Timing & lifecycle**: the trigger's own click both opens the menu and is itself a "click" — if the outside-click-closes listener isn't scoped correctly, the same click event will immediately close the menu it just opened. Don't rely on event timing tricks; have the outside-click handler explicitly ignore clicks whose target is inside `#profile-menu-btn` or `#profile-menu` (the trigger's own handler already owns toggling) so the two handlers can't fight over the same click.

**User experience spec**: on open, move focus to the `logout-btn` (the menu's first/only item). On close — whether via Escape, outside click, or item activation — return focus to `#profile-menu-btn`, so keyboard users land back where they started rather than losing focus to `<body>`.

## Phase 1: Profile menu dropdown shell

### Overview

Replace the static role badge + standalone logout button with a clickable trigger and a `role="menu"` popover hosting the relocated logout entry, with full mouse/keyboard open-close behavior.

### Changes Required:

#### 1. Topbar markup

**File**: `static/index.html` (~lines 236-242)

**Intent**: Replace the static `role-badge` span + standalone `logout-btn` with a trigger button and a menu list, wrapped in a positioning container.

**Contract**: Wrap the topbar's right-hand `<div>` content in a `.profile-menu-wrap` (or similar) that becomes the `position: relative` anchor. The trigger is a `<button id="profile-menu-btn" aria-haspopup="true" aria-expanded="false" aria-controls="profile-menu">` containing the existing `<span id="role-badge"></span>` plus a hamburger glyph (☰), per the user's explicit preference for a three-line hamburger icon over a chevron. The menu is `<ul id="profile-menu" class="profile-menu" role="menu" hidden>` with one entry: `<li role="none"><button id="logout-btn" role="menuitem">Wyloguj</button></li>`. The `role-badge` id and the `logout-btn` id + existing click binding (`static/index.html:501-503`) must be preserved exactly — no JS change required for either to keep working.

#### 2. Dropdown styling

**File**: `static/index.html` `<style>` block

**Intent**: Style the trigger to look like the existing topbar buttons, and the popover to look like the existing dropdown/modal chrome, anchored under the trigger at every viewport width.

**Contract**: `.profile-menu-wrap { position: relative; }`. `.profile-menu` is `position: absolute; top: 100%; right: 0; z-index: 50;` (matching the `z-index: 50` already used by `.ac-dropdown`, so every overlay-like element in the file relies on an explicit stacking value rather than implicit paint order) with the same visual chrome already used by `.ac-dropdown`/`.modal-box` (background, border, border-radius, box-shadow) so it reads as consistent with the rest of the app, `list-style: none`, hidden via the `hidden` attribute (toggled by JS, same mechanism the rest of the file uses for `style.display`). Menu item buttons override the inherited `.topbar button` pill styling so they render as full-width, left-aligned list rows instead of pills. No separate `@media (max-width: 640px)` block is needed for the menu itself — it's the same popover at all widths.

#### 3. Open/close/focus/keyboard behavior

**File**: `static/index.html` `<script>` block

**Intent**: Add the trigger's click-to-toggle behavior, Escape-to-close, outside-click-to-close, and focus management on open/close.

**Contract**: New `openProfileMenu()` / `closeProfileMenu()` (naming at implementer's discretion) toggle the menu's `hidden` attribute and the trigger's `aria-expanded`, and move focus per the Critical Implementation Details above. The trigger's click handler toggles the menu. A document-level outside-click handler closes the menu when the click target is outside both `#profile-menu-btn` and `#profile-menu` (see Critical Implementation Details for why this must explicitly exclude the trigger). The existing global `keydown` Escape handler (`static/index.html:740`) gets an additional branch that closes the profile menu when open — additive to, not a replacement of, the existing modal-close branch. The existing `logout-btn` click handler (`static/index.html:501-503`, calls `doLogout()`) is untouched.

### Success Criteria:

#### Automated Verification:

- No automated test suite exists for `static/` (vanilla JS, no build step, no frontend test framework configured in this repo) — verification is manual below, plus browser-level checks via `/10x-e2e` in the next workflow step.

#### Manual Verification:

- Clicking the trigger (role badge + hamburger icon) opens the menu, for both `admin` and `user` roles
- Clicking the trigger again, pressing Escape, or clicking outside the menu closes it
- Activating "Wyloguj" from inside the menu logs out exactly as before (session cleared, returns to login screen)
- No standalone logout button remains in the topbar outside the menu
- Keyboard: Tab reaches the trigger; Enter/Space opens the menu and moves focus to "Wyloguj"; Escape closes the menu and returns focus to the trigger
- Menu opens, closes, and is fully usable at both mobile (<640px) and desktop widths, for both roles
- Hamburger icon (☰) is visible on the trigger next to the role label

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Testing Strategy

### Unit Tests:

None — no test framework configured for the `static/` frontend.

### Integration Tests:

None — covered by manual verification and by `/10x-e2e` for browser-level risks (keyboard nav, outside-click, focus return) once this phase lands.

### Manual Testing Steps:

1. Log in as `admin`, click the trigger, confirm the menu opens showing "Wyloguj".
2. Press Escape, confirm the menu closes.
3. Reopen, click outside the menu, confirm it closes.
4. Reopen, click "Wyloguj", confirm logout works exactly as before.
5. Repeat steps 1-4 logged in as `user`.
6. Resize the viewport to <640px and repeat steps 1-4 for both roles.
7. Keyboard-only pass: Tab to the trigger, press Enter to open, confirm focus lands on "Wyloguj", press Escape, confirm focus returns to the trigger.

## Performance Considerations

Negligible — pure CSS/DOM state toggle, no new network calls, no new dependencies.

## Migration Notes

None — no data or schema changes involved.

## References

- Linear: PUL-47
- GitHub: #68
- Related future tickets that extend this menu: PUL-43, PUL-44, PUL-45, PUL-28

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Profile menu dropdown shell

#### Automated

- [x] 1.8 E2E: trigger click opens menu (focus → "Wyloguj") and Escape closes it (focus → trigger), aria-expanded toggles correctly
- [x] 1.9 E2E: clicking outside the menu closes it, while the opening click on the trigger itself does not (outside-click-guard regression)
- [x] 1.10 Fixed pre-existing `tests/e2e/test_idle_timeout.py::test_manual_logout_still_works` — it clicked "Wyloguj" directly, now must open the profile menu first

#### Manual

- [x] 1.1 Trigger (role badge + hamburger icon) opens the menu, both admin and user roles — 58b5074
- [x] 1.2 Trigger click again / Escape / outside-click closes the menu — 58b5074
- [x] 1.3 "Wyloguj" inside the menu logs out exactly as before — 58b5074
- [x] 1.4 No standalone logout button remains in the topbar — 58b5074
- [x] 1.5 Keyboard: Tab→trigger, Enter/Space opens + focuses "Wyloguj", Escape closes + returns focus to trigger — 58b5074
- [x] 1.6 Menu fully usable at mobile (<640px) and desktop widths, both roles — 58b5074
- [x] 1.7 Hamburger icon (☰) visible on the trigger next to the role label — 58b5074
