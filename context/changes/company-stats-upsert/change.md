---
change_id: company-stats-upsert
title: Replace DELETE+INSERT with MERGE upsert in company stats ingestion
status: implementing
created: 2026-06-27
updated: 2026-06-27
archived_at: null
tracking:
  linear: null
  github: null
---

## Notes

Aktualny kod w company_stats_main.py robi DELETE today's rows → batch INSERT every hour (9:01–17:01). Zamiana na BigQuery MERGE (UPSERT): jeśli ticker+snapshot_date istnieje → UPDATE, jeśli nie → INSERT. Eliminuje okno bez danych między DELETE a INSERT. Scheduler bez zmian (jeden job 1 9-17 * * 1-5).
