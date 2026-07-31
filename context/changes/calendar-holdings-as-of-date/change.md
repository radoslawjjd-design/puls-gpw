---
change_id: calendar-holdings-as-of-date
title: Compute calendar and value-history from holdings as of each day, not today's shares
status: implementing
created: 2026-07-30
updated: 2026-07-31
archived_at: null
tracking:
  linear: PUL-103
  github: 211
---

## Notes

PUL-103: kalendarz i wykres wartości liczą dzienne P&L z dzisiejszych stanów posiadania rzutowanych na każdy dzień (CROSS JOIN positions bez wymiaru czasu), przez co raportują wymyślone wartości dla dni sprzed pierwszej transakcji i błędnie ważą każdy dzień wewnątrz okresu. Naprawa: holdings-as-of-date rekonstruowane z user_broker_operations, granica na inception portfela, zastosowane do get_portfolio_calendar_data i get_portfolio_history. tracking: linear PUL-103, github 211

Blokuje PUL-104 (eksport CSV kalendarza) — GH #212.

### Znalezione przy Fazie 3 (nie w zakresie)

Wykres IKZE po zmianie zgłasza `excluded: ['CCC']`. CCC = **Modivo**, kupione 2025-08-11
(6 szt. po 173,65) i sprzedane 2025-09-23 (po 187,15) w obu portfelach IKZE. W
`company_daily_stats` **nie ma ani jednego wiersza dla CCC** — feed pokrywa 672 tickery
w tym oknie, ale nie ten. Skutek: ~1 040 zł wartości w tych sześciu tygodniach jest
niewycenione.

To **nie regresja** — przed tą zmianą ticker w ogóle nie trafiał do uniwersum wykresu,
więc brakowało go tak samo, tylko po cichu. Zmiana go **ujawnia** (`excluded` →
„CCC — brak notowań, pominięty w wycenie"), co jest dokładnie tym, do czego ta koperta
służy. Luka w źródle cen to osobny ticket.
