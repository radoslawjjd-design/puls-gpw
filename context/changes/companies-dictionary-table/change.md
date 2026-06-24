---
change_id: companies-dictionary-table
title: Companies dictionary table (ticker, name, hop URL)
status: implementing
created: 2026-06-23
updated: 2026-06-23
tracking:
  linear: PUL-53
  github: 84
---

## Notes

PUL-53: new BQ dimension table `companies` (ticker, name, hop_url) — prerequisite
for the daily company-stats ingestion job (separate ticket).

Framed via `/10x-frame` before planning — see `frame.md`. The ticket's two open
questions (seed source, write ownership) were investigated against the existing
codebase and narrowed/confirmed with the user; `/10x-plan` should start from the
reframed problem statement, not the original open questions verbatim.
