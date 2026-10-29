---
change_id: gcp-account-migration-kacha
title: Migrate puls-gpw to a new GCP project owned by the target Google account
status: cutover-complete-with-operational-holds
created: 2026-10-29
updated: 2026-10-29
archived_at: null
tracking:
  linear: null
  github: null
---

## Notes

User request: migrate the current puls-gpw application, infrastructure and data to
a new GCP project administered by the requested Google account, following the earlier
project-to-project migration. Source project: puls-gpw (139573454149), source
region: europe-central2. Account identities remain in the ignored local scope file.

The target account is authenticated in gcloud without changing the active source
account. Destination project `puls-gpw-kp-20261029` is created and linked to its
billing account; source inventory and the initial BigQuery preview copy are complete.

Source services and data must remain available until destination validation and
controlled cutover. Do not delete source resources, rotate primary secrets, or
run duplicate notification/publication pipelines. Preserve Firebase UIDs and
announcement/notification/post deduplication records. Secrets stay out of tracked
files and diagnostic output. Existing Bankier Cloudflare blocking is a separate
known failure; migration did not remove it. Scraping stays paused pending a fix.

Implementation merged in https://github.com/radoslawjjd-design/puls-gpw/pull/263.
Final data synchronization: 15 tables / 2,077,537 rows, exact row-multiset
comparison, and 7 Firebase users with identical UIDs and password hashes.
See runbook.md for the serving URL, operational holds and repeat-migration steps.
