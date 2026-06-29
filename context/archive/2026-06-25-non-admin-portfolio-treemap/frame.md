# Frame Brief: Treemapa portfela for non-admin users

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

Non-admin users have no "Treemapa portfela" view today — `GET /admin/portfolio/treemap`
403s for any non-admin role (gated since PUL-45). PUL-63 (closed, superseded) originally
framed this as part of a broader nav redesign; PUL-64 split it out as a standalone "open
this up to non-admins" ask.

## Initial Framing (preserved)

- **User's stated cause/approach**: "open Treemapa portfela to non-admin users — must show
  their own data, never the admin's portfolio." Initially this read like a pure
  access-control change (revisit the 403 check so the existing view renders for both roles).
- **User's proposed direction**: add a non-admin treemap option, explicitly not sourced
  from the admin's `portfolio_snapshots` data.
- **Pre-dispatch narrowing** (this session): user clarified the non-admin treemap's content
  should be **user-entered positions** — search a ticker/company name, enter quantity of
  shares held — not a derivation from the existing watchlist. Price for those positions
  should come from PUL-61 (daily 17:05 closing-price job, not yet built) — user asked that
  PUL-61 be annotated to account for this new consumer. Confirmed: no place in the app today
  lets a user enter financial data/positions.

## Dimension Map

The observation ("no non-admin treemap") could originate at any of these dimensions:

1. **Access control only** — the view and data model already support non-admin use; only
   the 403 gate needs relaxing. ← initial framing (pre-narrowing)
2. **Data model** — there's no per-user data to show even if access were granted; a new
   per-user dataset (positions: ticker + quantity) needs to be built from scratch.
3. **Input/UI surface** — even with a data model, there's no entry point for users to add
   their own positions (search, quantity input, add/remove).
4. **Pricing/valuation** — the treemap renderer requires a $ value per position to compute
   anything (composition %, daily change); no per-ticker price source exists anywhere in
   the app today.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. Access control only | `GET /admin/portfolio/treemap` reads exclusively from `portfolio_snapshots` (admin's XTB-upload-derived data, `db/bigquery.py:189-201`). Relaxing the 403 alone would just expose the *admin's* portfolio to every user — directly violates "must not load admin's data." | NONE — ruled out by the user's own constraint |
| 2. Data model gap | No per-user positions table exists. The watchlist table (`db/bigquery.py:351-357`: `client_id`, `ticker`, `added_at`) is the closest pattern but has no quantity field and isn't valued. A new table is needed. | STRONG |
| 3. Input/UI surface gap | Ticker/company-name autocomplete already exists and is directly reusable (`#wl-add-form`, `/autocomplete/tickers`, `/autocomplete/companies`, `db/bigquery.py:1189-1217`, wired in `static/index.html:1149-1182`) — this part is *not* a gap, it's a reusable asset. Only the "quantity" input + a new add/remove flow (mirroring `POST/DELETE /watchlist/{ticker}`) is missing. | STRONG (gap is narrow: just add a quantity field + new endpoints, not a new UI paradigm) |
| 4. Pricing/valuation gap | `compute_treemap_positions()` (`src/portfolio_treemap.py:36-37`) **requires** a `value` field per position — raises `KeyError` without it; cannot degrade to share-count-only composition. No price/`kurs` field exists in any table (`companies` has `hop_url`/`isin` only, no price). No daily price-ingestion job is shipped — PUL-54 and PUL-61 (the two candidate price sources) are both still in the backlog. | STRONG — this is the load-bearing blocker |

## Narrowing Signals

- User confirmed (this session): no existing place in the app lets a user enter financial
  positions — rules out any "we already have the data, just expose it" framing.
- User confirmed the price source is intentionally deferred to PUL-61, not something to
  build inside this ticket — this resolves *where* the price will eventually come from, but
  does not resolve *what the treemap shows in the meantime* (PUL-61 isn't built yet either).
- Cross-system precedent (`context/archive/2026-06-23-companies-dictionary-table/plan.md:10`):
  the `companies` table was explicitly built as a "prerequisite for a follow-up daily
  company-stats ingestion job (separate ticket)" — i.e., the project has known since PUL-53
  that per-ticker pricing is a deliberately deferred, not-yet-built capability. This is
  consistent with — not a surprise relative to — the pricing gap found here.

## Cross-System Convention

The admin treemap (PUL-45/50) was sequenced as: data source (XTB upload, PUL-43) → storage
(`portfolio_snapshots`) → rendering (`compute_treemap_positions` + frontend). The non-admin
version is missing the equivalent of step 1 (data source) entirely — both the input
mechanism (this ticket) and the pricing mechanism (PUL-61) are unbuilt. The project's own
convention (companies-dictionary-table change) is to ship prerequisite/reference tables
ahead of the job that consumes them and explicitly note the dependency in the consuming
ticket — which is exactly what the user asked for re: annotating PUL-61.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: this is not an access-control change — it's a
> new feature with two real components: (a) a per-user positions ledger (ticker + quantity,
> reusing the watchlist's `client_id` pattern and the existing ticker/company autocomplete),
> and (b) a treemap rendering path that has no price to render with until PUL-61 ships.

The original framing ("revisit the 403 check") significantly undersold the scope. The user
already self-corrected mid-conversation toward the right shape (positions ledger + defer
pricing to PUL-61) before this investigation ran; the investigation's value is confirming
that shape against the actual code (autocomplete is reusable, watchlist pattern is reusable,
`compute_treemap_positions()` hard-requires a price) rather than discovering something the
user hadn't already suspected.

## Confidence

**HIGH** — every claim in the hypothesis table has direct file:line evidence; the pricing
gap is corroborated by the project's own prior-change history (companies-dictionary-table);
the user's own narrowing answers already point at the same reframe independently confirmed
here.

## What Changes for /10x-plan

The plan needs to explicitly decide what the non-admin treemap *shows* before PUL-61 ships
a price feed — options worth presenting to the user during planning (not decided here, per
Frame's no-solution-design rule):
- Ship the positions CRUD (add/remove ticker + quantity) now, but gate the treemap
  *visualization* behind "has price data" — show a simpler list/composition view (by share
  count, no $ values) until PUL-61 lands, then upgrade.
- Ship positions CRUD + treemap UI now with prices hardcoded to `null`/pending state per
  position, accepting a degraded treemap until PUL-61 back-fills prices.
- Sequence PUL-61 before this ticket's treemap-rendering phase (but the user has already
  asked to start PUL-64 now and annotate PUL-61 as a dependency, so full sequencing-first is
  likely not what they want — surface as a phasing question, don't assume).

Also confirmed reusable building blocks /10x-plan should lean on directly: the watchlist's
`client_id` mechanism (`static/index.html:693-699`, header `X-Client-Id`), the existing
autocomplete component (`#wl-add-form` pattern), and `db/bigquery.py`'s watchlist CRUD
functions as the template for a new `user_positions` table.

## References

- `db/bigquery.py:189-201` — `portfolio_snapshots` schema (admin-only, XTB-sourced)
- `db/bigquery.py:351-357` — watchlist schema (reusable per-user pattern)
- `db/bigquery.py:382-456` — watchlist CRUD functions (template for new positions table)
- `db/bigquery.py:465-472` — `companies` schema (`hop_url`, `isin`, no price field)
- `src/portfolio_treemap.py:4-73` — `compute_treemap_positions()`, requires `value` per position
- `static/index.html:1149-1182` — reusable ticker/company autocomplete pattern
- `static/index.html:693-699` — `client_id` localStorage mechanism
- `context/archive/2026-06-23-companies-dictionary-table/plan.md:10` — prior precedent for
  deferred pricing job
- Linear: PUL-64 (this change), PUL-61 (price source, to be annotated), PUL-54 (related,
  unresolved relationship to PUL-61)
