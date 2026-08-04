---
change_id: listing-total-count
title: X-Total-Count header on the three listing endpoints
status: archived
created: 2026-08-04
updated: 2026-08-04
archived_at: 2026-08-04
tracking:
  linear: PUL-77
  github: 136
---

## Notes

PUL-77 — add `X-Total-Count` (row count after filters, before pagination) to:

- `GET /announcements` (`src/api.py:757`, admin and user branches)
- `GET /announcements/my-wallet` (`src/api.py:904`)
- `GET /admin/x-posts` (`src/api.py:985`)

Plus `Access-Control-Expose-Headers: X-Total-Count`. Unblocks "Strona 1 z N".

The ticket asks for `COUNT(*) OVER()` in the existing queries rather than a
second COUNT query, to avoid doubling BigQuery cost and latency.

## The pitfall the ticket does not mention

`COUNT(*) OVER()` rides on the returned rows. When a page is past the end of the
result set, **zero rows come back and the window value comes back with them** —
there is nothing to read the count off. Reporting 0 there would be silently wrong
in exactly the way a paginated UI notices: "Strona 5 z 0".

Handled with a fallback COUNT query used **only** when a page beyond the first
returns nothing — the rare path, so the cost the ticket is guarding against is
not paid on any normal request.

## Note on CORS

There is no `CORSMiddleware` in `src/api.py`; the UI is served from the same app
via `app.mount("/static")`, so the expose header is inert today. It is still
correct to send it — the moment the UI moves to another origin, its absence would
make the header unreadable in a way that is hard to diagnose from the browser.
