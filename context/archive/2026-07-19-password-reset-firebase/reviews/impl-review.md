<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Password Reset via Firebase (PUL-85)

- **Plan**: context/changes/password-reset-firebase/plan.md
- **Scope**: Phases 1-3 of 3 (full plan)
- **Date**: 2026-07-19
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning (fixed during triage), 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (19× MATCH; phase-3 addendum honored; OOB helper removal deliberate) |
| Scope Discipline | PASS (3 post-plan additions — AI-sec hardening, get_user_by_email pre-check, logo badge — all documented + test-pinned) |
| Safety & Quality | PASS after F1 fix |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (519 unit / 96 e2e re-run at review time) |

## Review context

Reviewed at branch `pul-85-closeout` after squashes `0541db7` (#159) + `1494e99`
(#160), epilogue `0f413cf`. Prod-verified end-to-end by the user (branded mail,
reset page, new-password login, enum 204/204, Firebase console Faro+PL).

Key confirmations: anti-enumeration identical across status/body/headers on all
steady-state paths; `_ORIGIN_RE` rejects userinfo/path/query/quote tricks;
rate-limiter keyed on GFE-appended last XFF element; html.escape correct for
URLs in attributes; deploy.yml secret wiring syntax safe; all patterns
(endpoint, submit handler, e2e, conftest mocks) match established conventions.

## Findings

### F1 — Residual enumeration: synchronous post-existence-check work

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:466-486 (pre-fix)
- **Detail**: Two facets of one design decision (sync link-gen + SMTP after
  `get_user_by_email`): (a) timing — known accounts cost 2 Firebase calls + an
  SMTP session (~300ms-2s) vs 1 call for unknown, classifiable in 1-2 samples;
  (b) failure-mode — any post-check failure returned 503 for known vs 204 for
  unknown, so an SMTP outage turned the endpoint into a boolean account oracle
  (residual sibling of the #160 bug).
- **Fix**: Return 204 immediately after the existence check; move link
  generation + branded mail into FastAPI `BackgroundTasks`
  (`_send_reset_email_background`); background failure → log + `send_alert` to
  the owner (silent for the requester by design).
- **Decision**: FIXED — measured post-fix: unknown 2975ms vs known 2442ms
  (comparable; response no longer shaped by post-check work). Tests updated:
  link-gen/SMTP failures now assert silent 204 + `send_alert` called.
  519 unit / 96 e2e green.

### F2 — Origin holds by inherited guarantees, not an allowlist

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:373
- **Detail**: Origin is shape-validated, not allowlisted; safety currently
  rests on Cloud Run Host routing + Firebase authorized-domain validation.
  Belt-and-braces would pin the prod origin from an env var.
- **Decision**: SKIPPED (layered defenses sufficient; revisit with custom
  domain work on #20)

### F3 — Sync endpoint can hold a threadpool thread ~20-30s worst-case

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:451
- **Detail**: Same exposure profile as login (Firebase 10s timeout); the F1
  fix moved SMTP off the request path, shrinking the window further.
- **Decision**: SKIPPED (accepted, consistent with existing endpoints)

### F4 — Port regex admits 65536-99999

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: src/auth.py:370
- **Detail**: `(:\d{1,5})?` accepts out-of-range ports. Harmless nit — such an
  origin fails at Firebase validation anyway.
- **Decision**: SKIPPED
