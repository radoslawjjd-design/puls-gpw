# Faro Graphics Integration — Plan Brief

> Full plan: `context/changes/faro-graphics-integration/plan.md`

## What & Why

Wire up the existing `static/img/faro-banner.jpg` hero image into the login screen. This is the explicit follow-up to PUL-57 (rebrand to Faro, favicon/manifest, PR #95), which dropped the banner asset into the repo but deliberately left its UI placement out of scope.

## Starting Point

`static/index.html` is a single inline-CSS file. The login screen (`.login-box`) shows only a text brand mark — `<h1>Faro</h1>` + a subtitle — with no imagery anywhere in the app. The banner file (1280×720, lighthouse illustration + tagline) already sits in `static/img/`, served but unreferenced.

## Desired End State

The login screen shows the banner image flush with the top of the login card, rounded to match the card's corners. The redundant text `<h1>Faro</h1>` is removed since the banner now carries the brand name visually; the functional subtitle ("Analizator komunikatów ESPI / EBI") stays. Scales responsively on mobile with zero new image assets.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Placement | Login screen only | Only place with spare whitespace today; avoids any risk to data-table readability/performance | Plan |
| Mobile handling | CSS scaling of the one existing file | No extra asset-generation work; box already shrinks responsively | Plan |
| Performance budget | Use 349KB JPEG as-is | Single one-time load on login, out of scope to compress | Plan |
| Accessibility | `alt=""` (decorative) | Purely visual/brand image; functional text already exists separately | Plan |
| Layout | Banner edge-to-edge above fields, box widened ~420px → 480px | Lets the 16:9 banner display without heavy cropping | Plan |
| Subtitle text | Kept | Different information (what the app does) than the banner's brand tagline | Plan |
| `<h1>Faro</h1>` | Removed | Banner already displays "FARO" visually — avoids a literal duplicate within ~100px | Plan |

## Scope

**In scope:**
- `static/index.html` — banner `<img>`, login-box/login-brand CSS restructure, h1 removal
- `tests/e2e/test_login_ux.py` — fix the one assertion that targets the removed `<h1>`

**Out of scope:**
- Table backdrop/watermark on any `.table-wrap` (announcements, x-history, my-wallet)
- New image assets (compressed/resized/cropped variants)
- Any other "Faro" text elsewhere in the app (topbar, title)

## Architecture / Approach

Single HTML file, no build step. The banner becomes the first child of `.login-box`; padding moves from the box onto a new inner `.login-box-body` wrapper so the image can sit flush with the box edges, clipped to the existing `border-radius` via `overflow: hidden`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Faro banner on login screen | Banner wired into login DOM/CSS + fixed E2E assertion | Removing `<h1>` breaks `test_login_ux.py:9` if the test isn't updated in the same phase |

**Prerequisites:** None — PUL-57 (favicon/manifest/branding) already merged.
**Estimated effort:** Single session, one phase, two files (`static/index.html`, `tests/e2e/test_login_ux.py`).

## Open Risks & Assumptions

- Assumes `faro-banner.jpg`'s 16:9 aspect ratio reads well at the chosen 480px box width without further cropping — verify visually in Phase 1's manual check.

## Success Criteria (Summary)

- Banner visible on login screen, responsive on mobile, no visual regressions to the login form
- `uv run pytest tests/e2e/test_login_ux.py` (and full `tests/e2e/`) green
- No duplicate "Faro" brand text visible in the login card
