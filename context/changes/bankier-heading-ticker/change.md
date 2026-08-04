---
change_id: bankier-heading-ticker
title: Take the ticker from the ticker-shaped group of a bankier heading, and guard the write paths
status: impl_reviewed
created: 2026-08-04
updated: 2026-08-04
archived_at: null
tracking:
  linear: PUL-102
  github: 210
---

## Notes

PUL-102 — `_extract_heading` takes the **first** parenthesised group of a
bankier.pl heading as the ticker. When bankier renders a brand or a status marker
before the exchange abbreviation, the wrong token becomes the company's identity:

- `Zabka Group SA (Żabka) (ZAB)` → `Żabka` instead of `ZAB`
- `MegaPixel Studio SA (przejęty) (MPS)` → `przejęty` instead of `MPS`

Visible symptom: the 2026-07-30 evening X thread went out with `$Zabka`.

## Scope agreed with the owner (2026-08-04)

**In this change — phases 1-3 (code):**

1. Fix the extractor: take the **last** ticker-shaped group; return `None` rather
   than a brand word when nothing matches.
2. Guard the write boundary in `companies` and `announcements` — a future markup
   change on any upstream page must not be able to inject a brand word again.
3. Guard the post generator: no cashtag beats a malformed one. A missing cashtag
   costs reach; a wrong one is a public error.

**Prepared but NOT executed — phase 4 (data repair):** destructive operations on
production are human-only (project rule). The SQL is written out in
`data-repair.sql` for the owner to run and verify.

**Owner decisions taken before implementation:**

- **MegaPixel** → delete, not rename. Its price has been frozen since 2026-07-28
  (1.70 PLN, turnover 840.55, 11 transactions — identical every day), and the
  heading says `przejęty`. The instrument is off the market, so renaming to `MPS`
  would create a live-looking identity that keeps accruing frozen rows. Zero
  announcements reference it, so nothing is orphaned.
- **The published `$Zabka` thread** → left up. The cashtag is unresolvable rather
  than misleading; deleting costs the engagement the post already earned.

## Verified against production, 2026-08-04

Numbers have grown since the ticket was written:

| ticker | companies | announcements | company_daily_stats | source |
| -- | -- | -- | -- | -- |
| `ZAB` | yes, isin/hop_url NULL | 1 | 443 (from 2024-10-17) | gpw, archive |
| `Żabka` | yes, isin + hop_url set | **4** (ticket said 1) | **27** (from 2026-06-29) | bankier |
| `przejęty` | yes, isin + hop_url set | 0 | **27** | bankier |
| `MPS` | **absent** | 0 | 0 | — |

Two `Żabka` price dates still have no `ZAB` counterpart: **2026-07-27 (28.32)** and
**2026-07-28 (28.18)**.

## Out of scope

`CREOTECH-PDA` / `CRQUANTUM-PDA` — a different shape, correctly extracted from a
single-group heading. Separate ticket. The new validator must therefore accept a
hyphen, or it would reject rows that are not this bug.
