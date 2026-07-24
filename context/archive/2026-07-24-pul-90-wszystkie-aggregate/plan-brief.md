# "Wszystkie" Aggregate View in Mój portfel — Plan Brief

> Full plan: `context/changes/pul-90-wszystkie-aggregate/plan.md`
> Research: `context/changes/pul-90-wszystkie-aggregate/research.md`

## What & Why

Add a default, first-listed **"Wszystkie"** entry to the Mój portfel wallet selector that aggregates every portfolio the user owns. A user with several wallets (główny, IKZE, …) currently has no combined picture — value, P&L and daily change are per-wallet only. "Wszystkie" gives them one merged view.

## Starting Point

Mój portfel is always scoped to one wallet via `_activePortfolioId`. Positions/summary render per-wallet; the summary already computes value client-side from `shares × current_price`. The DB layer already returns all-user positions when `portfolio_id=None` (treemap uses it), but the `positions`/`calendar`/`history` endpoints require and validate a specific wallet id.

## Desired End State

Entering Mój portfel selects "Wszystkie" by default: Tabela shows one merged row per ticker across all wallets, Summary sums value/P&L/daily change, and Kalendarz shows a combined daily grid + combined value-history chart. The mode is read-only; clicking any wallet tab scopes back to that portfolio and restores editing. `?portfolio=all` round-trips.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Where to aggregate | Backend `portfolio_id=all` sentinel | 1 call, DB already all-capable, cache reuse | Research/Plan |
| Same ticker in ≥2 wallets | Merge into one row, server-side | Clean aggregate; keeps `PortfolioPositionOut` + table render untouched | Research/Plan |
| Kalendarz in all-mode | Aggregate grid **and** value chart | User wants combined daily behavior; folds companion chart into this change | Plan |
| Read-only scope | Hide Edytuj/Usuń + Dodaj pozycję (+ guards) | Positions belong to a wallet; keep CSV export & Dodaj portfel | Plan |
| "Wszystkie" visibility | Always first + default, even with 1 wallet | Matches ticket "default on entry"; consistent UX | Plan |

## Scope

**In scope:** `all` sentinel on positions/calendar/history; same-ticker merge (summed shares + weighted-avg buy price); "Wszystkie" tab first+default; read-only render; combined calendar grid + value chart; e2e coverage.

**Out of scope:** treemap changes (already user-wide); per-wallet source column in merged table; editing in all-mode; schema changes; new deps; purchase-date tranche accuracy.

## Architecture / Approach

One `_ALL_PORTFOLIOS = "all"` sentinel. The three per-wallet handlers branch on it: skip the ownership check, pass `None` to the DB layer. Positions merges same-ticker rows before the existing per-row P&L loop. Calendar/history DB functions gain an optional `portfolio_id` (conditional filter) — dropping the filter makes their daily `SUM(...)` the combined figure for free. Frontend adds the "Wszystkie" tab, defaults to it, routes the sentinel through existing fetches, and hides edit controls in that mode.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend | `all` sentinel on 3 endpoints + same-ticker merge | Merge math (weighted-avg) / not breaking per-wallet path |
| 2. Frontend | "Wszystkie" tab, default, read-only, aggregate calendar/chart | Default-selection + URL round-trip edge cases |
| 3. E2E + fixtures | 2nd portfolio + shared ticker, "Wszystkie" browser test | Fakes must answer the `all`/`None` call |

**Prerequisites:** none (branch `pul-90-wszystkie-aggregate` already open).
**Estimated effort:** ~1-2 sessions across 3 phases.

## Open Risks & Assumptions

- Same-ticker rows across wallets share identical market data (`current_price`/`daily_change_pct`/`price_history`) — merge carries the first non-null; assumed safe because the DB price scan is per-ticker.
- Scope bump: the value-history chart in Kalendarz (flagged companion in the ticket) is now included per the user's decision to make the calendar combined.

## Success Criteria (Summary)

- "Wszystkie" is first + default; merged table + summed summary across all wallets; no editing; wallet tabs scope back.
- Kalendarz in all-mode shows combined daily grid + combined value chart.
- No console errors, light+dark correct, no new deps; full test suite green.
