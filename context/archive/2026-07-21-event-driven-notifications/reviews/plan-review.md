<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Event-driven watchlist notifications (PUL-81 slice b-v2)

- **Plan**: context/changes/event-driven-notifications/plan.md
- **Mode**: Deep
- **Date**: 2026-07-22
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 0 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | PASS |

## Grounding
9/9 paths ✓. Symbols verified: `select_pending_notifications` has a single caller (`notification_main.py` + tests) — safe to remove in Phase 3; `send_announcement_digest_email(to_email, items, base_url)`; `test_main.py` `pipeline_mocks` fixture (`monkeypatch.setattr(main, …)`); the `puls-gpw` scraper job carries SMTP secrets (shared config, infra.md:23) so the hook can send. brief↔plan ✓. Progress↔Phase consistent (P1 1.1-1.3, P2 2.1-2.3, P3 3.1-3.5).

## Findings

### F1 — "Retried next pass" is false in the event-driven model — a send failure is permanent

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details + Phase 2 §2
- **Detail**: The plan carried slice b's "not recorded → retried next pass" idempotency wording, but this change removes the cron and the scraper skips already-processed announcements (`get_processed_ids_since`), so each announcement is hooked exactly once. A failed inline send is never retried → permanently missed. The wording claimed a retry that no longer exists.
- **Fix A ⭐ Recommended**: Accept + correct the wording, add a small in-run retry (2–3 attempts, short backoff) before giving up; a sustained outage miss is accepted (low-stakes).
- **Fix B**: Re-add a minimal cron safety-sweep (contradicts the "remove entirely" decision).
- **Decision**: FIXED (Fix A) — updated Critical Implementation Details (send-then-record bullet now says no next-pass, in-run retry, accepted outage miss) + Phase 2 §2 (per-recipient in-run retry).

### F2 — Inline SMTP × many sends could eat the scraper's 300s task-timeout

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2 §2 + Performance Considerations
- **Detail**: Each send is inline (SMTP timeout 10s); many approved announcements × watchers could accumulate toward the 300s task-timeout. Negligible at 2 subscribers; plan notes it.
- **Fix**: None now — the queue/BackgroundTasks move is the scale answer.
- **Decision**: SKIPPED — non-issue at current scale; noted in Performance.
