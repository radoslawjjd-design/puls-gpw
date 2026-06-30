---
change_id: pul-60
title: Performance — sub-second perceived load for announcements, watchlist, portfolio, treemap and calendar
status: plan_reviewed
created: 2026-06-30
updated: 2026-06-30
archived_at: null
tracking:
  linear: PUL-60
  github: null
---

## Notes

Noticeable wait before data appears across multiple views: Ogłoszenia (announcements table), Obserwowane (watchlist), Mój portfel + switching between wallets, Treemapa, Kalendarz + switching between wallets. Goal: sub-second perceived load on warm path.

Areas to audit: BQ query shapes for `/announcements`, `/watchlist`, `/portfolio`, `/treemap`, `/calendar` endpoints; caching opportunities (only autocomplete has a 5-min cache today); frontend sequential vs parallel fetches + unnecessary re-renders; Cloud Run min-instances=0 cold-start contribution.

Open questions to resolve before /10x-plan: (1) baseline — actual measured load times today; (2) target — concrete "instant" threshold (e.g. <300 ms perceived TTFB or <500 ms full render).
