---
change_id: x-publisher-core
title: X publisher core
status: archived
created: 2026-06-15
updated: 2026-06-15
archived_at: 2026-06-15T22:39:17Z
tracking:
  linear: PUL-26   # also delivers PUL-27 (wiring+deploy) — close BOTH at close-out
  github: 22       # also delivers #23 (PUL-27) — close BOTH at close-out
---

## Notes

**Tracking**: this change spans the pre-planned pair **PUL-26** (X Publisher core / tweepy client —
GitHub #22) + **PUL-27** (auto-publish wiring + deploy + `MIN_XPOST_SCORE=50` gate — GitHub #23).
Duplicate PUL-35 was canceled. Close-out must mark BOTH PUL-26 & PUL-27 Done and close GitHub #22 & #23.
Note: PUL-26 scoped a compliance guard (≤1 cashtag/≤2 hashtags/known-ticker) that this change
**deliberately defers** — only a non-empty/substance guard is built (see plan "What We're NOT Doing").
Secret loading diverges from PUL-27's single-JSON-blob spec: 4 separate Secret-Manager secrets → 4 env
vars (matches current puls-gpw convention, no SM API in code).

Prior art w `oldProjectData/` — działający publisher z poprzedniego projektu:
- `oldProjectData/agents/x_publisher.py` — tweepy + OAuth 1.0a, singleton, single/thread,
  compliance fail-fast (`agents.xpost_compliance`), partial-publish + Sentry alert.
- `oldProjectData/x_credentials.json` — 4 klucze: api_key/api_secret/access_token/access_secret
  (zdobyte przez X developer console; OAuth 1.0a user-token — tak postowałem ręcznie wcześniej).

Research ma ustalić: co przenosimy 1:1 vs adaptujemy do obecnego puls-gpw (tabela `x_posts`/PUL-29,
scheduler, czy `xpost_compliance`/`gpw_tickers` dziś istnieją), oraz X API access tier / limity
zapisu 2026 i OAuth 1.0a vs 2.0.

### HARD CONSTRAINT — nigdy nie publikuj pustego/„brak posta"

Jeśli na maila przychodzi PUSTY xpost (albo „brak posta"), taka treść **nie może** trafić
automatycznie na X. Plan MUSI dodać osobny **guard niepustości tuż przed `publish()`**
(belt-and-braces, niezależny od `post_supervisor`), nawet jeśli publish i tak siedzi wewnątrz
gałęzi `result.approved` w `post_main.py:125-129`. `X_AUTO_PUBLISH=on` nie zwalnia z tego guardu —
pusta/zdegenerowana treść = no-op publikacji + tylko email, jak dziś.
