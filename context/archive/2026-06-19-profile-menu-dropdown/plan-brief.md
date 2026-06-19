# Profile Menu Dropdown — Plan Brief

> Full plan: `context/changes/profile-menu-dropdown/plan.md`

## What & Why

Turn the static `👤 admin` / `👤 user` role badge in the dashboard topbar into a clickable button that opens a hamburger-style dropdown menu, with "Wyloguj" (logout) moved inside as its first entry. This is a foundation shell: PUL-43, PUL-44, PUL-45, and PUL-28 will each add their own entry to this same menu later, instead of each inventing its own ad-hoc dropdown.

## Starting Point

`static/index.html` is a single-file vanilla HTML/CSS/JS dashboard (no build step, no frontend test framework). Today the role badge is a non-interactive `<span>` and "Wyloguj" is a separate standalone button next to it — both static, no menu exists.

## Desired End State

Clicking the role badge opens a small popover menu under it, containing "Wyloguj". Escape, outside click, or activating an item closes it. Keyboard and screen-reader users can reach and operate it. Identical behavior on mobile and desktop. No second logout control exists anywhere else in the topbar.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Trigger interaction | Click-only toggle | Matches existing dropdown/modal precedents in the file; no hover-intent logic needed for a 1-item menu | Plan |
| Mobile presentation | Same anchored popover at all widths | One implementation for mobile and desktop satisfies the acceptance criteria without a second layout to maintain | Plan |
| Accessibility depth | `role="menu"`/`menuitem` + Escape + outside-click + Tab order (no arrow-key roving focus) | Satisfies the ticket's literal "keyboard-accessible" bar with the minimum code; matches the rest of the app, which has no roving-focus pattern anywhere | Plan |
| Focus management | Focus moves to "Wyloguj" on open, back to trigger on close | Standard expected behavior for a dropdown menu; meaningful accessibility improvement, not just decoration | Plan |
| Trigger visual indicator | Hamburger icon (☰) next to the role label | User's explicit preference; also matches the ticket's own "hamburger-style" wording | Plan |
| Phase structure | Single phase | Entire change lives in one file with no independent sub-systems — splitting would add overhead without reducing risk | Plan |

## Scope

**In scope:**
- Converting the role badge into a clickable trigger button
- A dropdown menu (`role="menu"`) anchored under it, containing the relocated "Wyloguj" entry
- Open/close via click, Escape, outside click
- Focus management on open/close
- Hamburger icon on the trigger

**Out of scope:**
- Any new menu entries (PUL-43/44/45/28 add their own later)
- Changes to logout/auth logic itself
- Animations/transitions
- Full WAI-ARIA roving-tabindex / arrow-key navigation
- A separate mobile bottom-sheet layout

## Architecture / Approach

Everything lives in `static/index.html`: markup wraps the existing `role-badge` span in a new trigger button and adds a sibling `<ul role="menu">` holding the relocated `logout-btn`. CSS anchors the menu under the trigger using `position: absolute`, reusing the visual chrome already established by `.ac-dropdown` and `.modal-box`. JS adds toggle/open/close functions with `aria-expanded` and focus management, plus an outside-click handler that's careful not to fight with the trigger's own click (see plan's Critical Implementation Details).

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Profile menu dropdown shell | Clickable trigger + accessible popover menu hosting "Wyloguj" | Outside-click handler racing with the trigger's own click (see plan for the guard) |

**Prerequisites:** None — self-contained change to one existing file.
**Estimated effort:** Single short session, one phase.

## Open Risks & Assumptions

- Assumes no existing automated frontend test suite to extend (confirmed — none exists for `static/`); verification is manual plus a later `/10x-e2e` pass.
- Assumes the hamburger glyph (☰) is acceptable as plain Unicode text rather than an SVG/icon-font asset — consistent with the rest of the file's emoji-as-icon approach (👤, ✕, ⌕).

## Success Criteria (Summary)

- Role badge is a working clickable trigger that opens/closes an accessible dropdown menu on both roles, both mobile and desktop
- "Wyloguj" works exactly as before, now from inside the menu, with no standalone logout button left in the topbar
- Keyboard-only users can open the menu, reach "Wyloguj", and close the menu without losing focus
