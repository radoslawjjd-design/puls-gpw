---
change_id: faro-graphics-integration
title: Faro graphics integration
status: implemented
created: 2026-06-24
updated: 2026-06-24
archived_at: null
tracking:
  linear: PUL-58
  github: null
---

## Notes

<!-- Free-form notes for this change: links, ad-hoc context, decisions that don't belong in research/frame/plan. -->

### Phase 1 design pivot (2026-06-24)

During manual verification of Phase 1, the user redirected the design away from the plan's
written contract. Plan said: banner `<img>` as edge-to-edge first child of `.login-box`,
`<h1>Faro</h1>` removed since the banner carries the brand name.

Actual implementation: banner is a full-viewport `background-image` on `#login-screen`
(with a dark overlay gradient for contrast), `.login-box` stays a plain solid white card on
top (z-index 1), and `<h1>Faro</h1>` stays removed (user confirmed no replacement heading —
branding is carried by the page background alone). `tests/e2e/test_login_ux.py` asserts the
background-image via `to_have_css("background-image", re.compile("faro-banner"))` instead of
an `<img class="login-banner">` locator.

Plan's Phase 1 "Changes Required" contracts (sections 1-2) describe the original in-box
banner approach and are now stale relative to the shipped code — left as-is per the
implement skill's read-only Phase-block rule; this note is the source of truth for what
actually shipped.

### Phase 1 design pivot #2 — revert + resize (2026-06-24)

User judged the full-screen background look (pivot #1 above) bad and reverted to the
original in-box banner approach from the plan's contract (`<img class="login-banner">` as
first child of `.login-box`, `.login-box-body` wrapper, no `<h1>`). On top of that, the user
felt the banner rendered too small and asked for the box to be ~10% bigger: `.login-box
max-width` raised from the plan's `480px` to `528px` (480 × 1.1). Banner scales with the box
via `width: 100%`, so it grows proportionally. `tests/e2e/test_login_ux.py` reverted to
asserting `.login-banner` visibility (as in the original plan contract, not the pivot-#1
background-image assertion).
