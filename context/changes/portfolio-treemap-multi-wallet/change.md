---
change_id: portfolio-treemap-multi-wallet
title: Portfolio treemap — main + IKZE side-by-side with portfolio-share %
status: impl_reviewed
created: 2026-06-20
updated: 2026-06-20
archived_at: null
tracking:
  linear: PUL-50
  github: 73
---

## Notes

Follow-up to PUL-45 (admin-ui-portfolio-treemap, shipped — archived at
context/archive/2026-06-20-admin-ui-portfolio-treemap/). PUL-45's explicit
"What We're NOT Doing" excluded multi-wallet display; this change picks that
up as its own scoped unit of work, per the user's request during PUL-45's
manual verification.

Scope (user's exact ask):
- Show **both** wallets (`main` and `ikze`) side by side instead of the
  single auto-detected wallet.
- Header "Portfel główny" above the `main` treemap, "IKZE" above the `ikze`
  treemap.
- Each ticker cell additionally shows, on top of the existing daily %/PLN
  change: what % of that wallet's total portfolio value the position
  represents, and the position's absolute PLN value.

PUL-45's existing pieces to build on directly: `GET /admin/portfolio/treemap`
endpoint, `compute_treemap_positions()`, `static/js/treemap-layout.js`
(squarified layout), the profile-menu/view wiring in `static/index.html`, and
its E2E test pattern (`tests/e2e/test_portfolio_treemap.py`).
