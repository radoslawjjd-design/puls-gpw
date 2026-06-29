# PUL-59 P&L Calendar — Plan Brief

> Full plan: `context/changes/pul-59-portfolio-calendar/plan.md`
> Research: `context/changes/pul-59-portfolio-calendar/research.md`

## What & Why

Add a monthly P&L calendar as a third tab in "Mój portfel" (alongside Tabela and Treemapa).
Each day cell is coloured green/red with the daily PLN change, or grey for weekends and
non-trading days (GPW holidays). The user navigates between months with prev/next buttons.

## Starting Point

`user_portfolio_positions` + `company_daily_stats` already exist and hold the data needed —
no new tables or schema changes required. The view-tab switching pattern (Tabela/Treemapa)
in index.html is a ready template for the third tab. No date-range query currently exists for
portfolio data — this plan adds one.

## Desired End State

Clicking "Kalendarz" in Mój portfel shows a full monthly grid. Each trading day shows PLN P&L
(e.g. "+320 PLN") coloured green or red; weekends and GPW holidays are grey. Prev/next
navigation works. Switching wallet tabs refreshes the calendar for the new portfolio.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Data source | user_portfolio_positions × company_daily_stats | User requested, portfolio_snapshots (XTB) not used | Research / User |
| P&L metric | portfolio_value(D) − portfolio_value(D−1) | Truest daily delta; kurs_zamkniecia already in BQ | Plan |
| Missing prices | Best-effort (partial sum) | User chose partial over strict all-or-nothing | User |
| Holiday detection | Absence from company_daily_stats | Weekday not in table = no session; matches GPW calendar naturally | Plan |
| First-day baseline | Lookback −35 days in BQ query | Covers longest holiday stretches (Christmas+NewYear) | Plan |
| Cell content | Colour + PLN amount | User chose; no % shown | User |
| Grid style | Full calendar Pn–Nd (5–6 rows) | Standard intuitive layout | User |
| Initial month | Current month | Simple, predictable UX | User |
| Compute fn location | New file src/portfolio_calendar.py | Clean separation; easy unit tests | User |

## Scope

**In scope:**
- New BQ function `get_portfolio_calendar_data()` with extended-range query + lookback
- Pure compute function `compute_calendar_pnl()` in `src/portfolio_calendar.py`
- FastAPI endpoint `GET /api/portfolio/calendar?year=&month=&portfolio_id=`
- Frontend: third tab button, calendar grid CSS, JS fetch + render + month nav
- Unit tests (compute fn + BQ), API tests, E2E tests

**Out of scope:**
- Position history tracking (current shares used for all past months)
- GPW holiday hardcoding (inferred from company_daily_stats)
- Click-to-drill-down on a day cell
- portfolio_snapshots (XTB/admin data)
- Percentage shown in cells (PLN only)

## Architecture / Approach

4-layer pipeline: BQ → compute → API → frontend.

BQ query crosses trading_days (DISTINCT snapshot_date from company_daily_stats) × user positions,
LEFT JOINs closing prices per day. Extended range (month − 35 days) provides the lookback baseline
for the first day. Python compute function handles all calendar logic (weekday math, delta, state
classification) — fully testable without BQ. API adds auth + param validation on top. Frontend
extends the existing view-tab pattern.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BQ function | `get_portfolio_calendar_data()` — daily portfolio values for extended range | CROSS JOIN correctness; BQ reserved keyword check |
| 2. Compute fn | `compute_calendar_pnl()` — full month grid with state/pnl_abs | Edge cases: lookback missing, empty portfolio, Feb |
| 3. API endpoint | `GET /api/portfolio/calendar` — auth, validation, JSON response | Portfolio ownership check (403 guard) |
| 4. Frontend | Kalendarz tab, CSS grid, JS nav, wallet integration | Mobile layout; synchronous nav-btn disable |

**Prerequisites:** None — user_portfolio_positions and company_daily_stats are populated.
**Estimated effort:** ~2-3 sessions across 4 phases (Phase 4 frontend is the largest).

## Open Risks & Assumptions

- Uses CURRENT positions to compute HISTORICAL P&L — if user sold/bought shares, past months may
  show incorrect totals. Documented limitation; acceptable for MVP.
- If scraper misses a trading day, that day appears as a non-session grey cell (same display as
  a real holiday). Acceptable since P&L can't be computed anyway.
- company_daily_stats coverage depth unknown at planning time — if data only goes back 3 months,
  older calendar months will be all-grey. Not a blocker.

## Success Criteria (Summary)

- All automated tests pass (unit, API, E2E)
- Calendar renders in browser with green/red/grey cells matching historical data
- Month navigation works without double-click race (synchronous button disable)
