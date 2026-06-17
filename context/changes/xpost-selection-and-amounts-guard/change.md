---
change_id: xpost-selection-and-amounts-guard
title: Fix X-post company starvation and number-less financial-results posts
status: implementing
created: 2026-06-17
updated: 2026-06-17
archived_at: null
tracking:
  linear: PUL-40
  github: 54
---

## Notes

Bug fix for the morning X-post pipeline (PUL-40 / GitHub #54). Two defects confirmed on the 2026-06-17 `ranek` window:

- **Defect A — company starvation.** `fetch_top_n_for_window` (`db/bigquery.py`) applies `LIMIT N` over raw announcements *before* ticker dedup (dedup currently happens later, in `generate_post`). 7 TOWERINVT (TOW) `wyniki_finansowe` rows tied at score 120 filled all 4 slots; ASB (`kontrakt_znaczacy`, also 120) was dropped. Fix: dedup-before-limit via `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY analysis_score DESC) = 1` so N = N distinct companies. Keep the `generate_post` dedup as a belt.

- **Defect B — number-less results posts.** `wyniki_finansowe` posts shipped with no amounts because `key_numbers` was empty (NewConnect EBI cover notes, ~1 KB `parsed_content`, numbers in an unfetched attachment) and `post_generator` falls back to a number-less `summary_pl` sentence. Fix: **narrow** guard disqualifying only `wyniki_finansowe`/`wyniki_sprzedazowe` with empty `key_numbers` — `kontrakt_znaczacy`/`dywidenda` with empty `key_numbers` must stay (ASB-style qualitative events). Plus a belt in `post_supervisor`/`is_publishable`: no published `wyniki_finansowe` body tweet without ≥1 amount.

**Out of scope (follow-up):** fetching NewConnect periodic-report attachment / XBRL to recover `key_numbers` for `wyniki_finansowe` — larger extraction change, tracked separately.

Relates to PUL-27 (analysis_score quality gate).
