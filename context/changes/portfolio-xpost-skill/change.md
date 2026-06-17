---
change_id: portfolio-xpost-skill
title: Portfolio status X-post generator skill (broker_data → 2 X threads)
status: preparing
created: 2026-06-17
updated: 2026-06-17
archived_at: null
tracking:
  linear: PUL-39
  github: 53
---

## Notes

Claude Code skill that reads XTB screenshots from broker_data/ subfolders, extracts portfolio data via vision, generates 2 ready-to-publish X threads (main+IKZE / short+long wallets), asks for approval, then publishes via src/x_publisher.py and archives the screenshots. Extends publisher with media upload (tweepy v1.1 media_upload → media_ids).
