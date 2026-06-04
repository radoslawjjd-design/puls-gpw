---
change_id: observability-baseline
title: F-03 Structured logging i email alert na błąd pipeline'u
status: implementing
created: 2026-06-04
updated: 2026-06-04
archived_at: null
tracking:
  linear: PUL-7
  github: 3
---

## Notes

Foundation — observability przed pierwszym produkcyjnym kodem pipeline'u. Prerequisit S-04 (email-orchestration). Bez tej warstwy pipeline może failować cicho w Cloud Run bez wiedzy właściciela.

Scope z roadmapy:
- structured logging (JSON) we wszystkich modułach pipeline'u
- email alert do właściciela przy każdym nieobsłużonym wyjątku lub błędzie etapu
- logi widoczne w Cloud Logging (GCP)

PRD refs: NFR (failure alerting — "cicha awaria jest niedopuszczalna"), FR-008 (po 3 failed próbach supervisora → alert zamiast posta).
