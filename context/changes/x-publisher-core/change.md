---
change_id: x-publisher-core
title: X publisher core
status: preparing
created: 2026-06-15
updated: 2026-06-15
archived_at: null
tracking:
  linear: null
  github: null
---

## Notes

Prior art w `oldProjectData/` — działający publisher z poprzedniego projektu:
- `oldProjectData/agents/x_publisher.py` — tweepy + OAuth 1.0a, singleton, single/thread,
  compliance fail-fast (`agents.xpost_compliance`), partial-publish + Sentry alert.
- `oldProjectData/x_credentials.json` — 4 klucze: api_key/api_secret/access_token/access_secret
  (zdobyte przez X developer console; OAuth 1.0a user-token — tak postowałem ręcznie wcześniej).

Research ma ustalić: co przenosimy 1:1 vs adaptujemy do obecnego puls-gpw (tabela `x_posts`/PUL-29,
scheduler, czy `xpost_compliance`/`gpw_tickers` dziś istnieją), oraz X API access tier / limity
zapisu 2026 i OAuth 1.0a vs 2.0.
