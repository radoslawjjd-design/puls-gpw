---
change_id: calendar-holdings-as-of-date
title: Compute calendar and value-history from holdings as of each day, not today's shares
status: preparing
created: 2026-07-30
updated: 2026-07-30
archived_at: null
tracking:
  linear: PUL-103
  github: 211
---

## Notes

PUL-103: kalendarz i wykres wartości liczą dzienne P&L z dzisiejszych stanów posiadania rzutowanych na każdy dzień (CROSS JOIN positions bez wymiaru czasu), przez co raportują wymyślone wartości dla dni sprzed pierwszej transakcji i błędnie ważą każdy dzień wewnątrz okresu. Naprawa: holdings-as-of-date rekonstruowane z user_broker_operations, granica na inception portfela, zastosowane do get_portfolio_calendar_data i get_portfolio_history. tracking: linear PUL-103, github 211

Blokuje PUL-104 (eksport CSV kalendarza) — GH #212.
