---
change_id: my-wallet-watchlist
title: My Wallet — personal watchlist on a lightweight client identity
status: impl_reviewed
created: 2026-06-22
updated: 2026-06-23
tracking:
  linear: PUL-28
  github: 24
---

## Notes

Reframed from PUL-28 ("User profile + My Wallet, per-ticker tiers, usage stats")
via `/10x-frame`. See `frame.md` for the full investigation.

Scope narrowed to: a minimal, registration-free per-user identity (browser-persisted
client id) + the "My Wallet" personal watchlist view on top of it. Per-ticker
freemium tiers and usage stats are deferred — they depend on real registration,
which doesn't exist yet and isn't urgent per the product owner.
