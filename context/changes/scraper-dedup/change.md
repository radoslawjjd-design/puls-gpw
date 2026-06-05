---
id: scraper-dedup
title: "S-01: Scraper Bankier.pl + dedup check BigQuery"
status: impl_reviewed
created: 2026-06-05
updated: 2026-06-05

roadmap_id: S-01
tracking:
  linear: PUL-13
  github: 9
---

## Summary

Implementacja scrapera listingu Bankier.pl z oknem 15 min, batch dedup via BigQuery i insertem nowych ogłoszeń. Zastępuje stub w main.py.

## Prerequisites

- F-01 done (selektory HTML znane)
- F-02 done (BQ klient + insert_announcement gotowe)
- F-03 done (send_alert gotowe)
