---
change_id: watchlist-admin-score-sentiment-drilldown
title: Watchlist admin view — score column, sentiment bar period info, and drill-down popup
status: implemented
created: 2026-07-20
updated: 2026-07-21
archived_at: null
tracking:
  linear: PUL-87
  github: 155
---

## Notes

Admin gets full analytical context in Obserwowane (My Wallet). Three parts:

1. **Score column for admin** — the my-wallet table renders hardcoded as the user
   variant (`renderTable(data, 'user', …)` + `_USER_COLS`, static/index.html:2400-2418)
   even though `/announcements/my-wallet` already returns `analysis_score` for admins.
   Make the render role-aware (same pattern as the announcements table).
2. **Sentiment bar period info** — replace the hardcoded "Ostatnie 7 dni" label
   (static/index.html:2449) with real metadata from the summary endpoint: explicit
   date range and the number of days with data.
3. **Drill-down popup** — clicking Pozytywny / Neutralny / Negatywny in the bar opens
   a modal listing the matching watchlist announcements from the same time window.
   Needs: BQ function (watchlist join + sentiment filter + window, bounded), admin-gated
   endpoint, modal UI (reuse the existing announcement-modal pattern), cache with the
   existing per-user key shapes.

Sentiment bar stays admin-only ([[PUL-82]] convention: sentiment/score never reach the
user role). User role must never see the bar or endpoint data (403/strip).

Estimate: Medium — ~1-2 sessions.
