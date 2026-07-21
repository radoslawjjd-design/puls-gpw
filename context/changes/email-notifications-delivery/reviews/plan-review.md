<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Email delivery for watchlist announcement notifications (PUL-81 slice b)

- **Plan**: context/changes/email-notifications-delivery/plan.md
- **Mode**: Deep
- **Date**: 2026-07-21
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | PASS |

## Grounding
8/8 paths ✓ (new files correctly absent). Symbols verified: `_html_escape` = `from html import escape as _html_escape` (src/notifier.py:9); `_send`/`send_verification_email`/`send_alert` + logo `{origin}/static/img/faro-mark.png`; deploy.yml env `PROJECT_ID/REGION/IMAGE` + per-job `jobs update … --args="run,--no-dev,python,X_main.py"` (5 jobs); infra.md "One-time provisioning runbook" + Cloud Scheduler table (`1,31 9-17` cron). brief↔plan consistent. Progress↔Phase consistent (P1 1.1-1.3, P2 2.1-2.3, P3 3.1-3.5).

## Findings

### F1 — New subscriber gets a 48h backlog blast on first pass

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 §2 — select_pending_notifications
- **Detail**: The sent-log anti-join is empty for a newly-enabled user, so their first pass would email every qualifying announcement of their watched tickers from the whole 48h candidate window at once — a backlog blast, not "new announcements since opt-in".
- **Fix**: Add `AND a.published_at >= COALESCE(ns.confirmed_at, ns.updated_at)` (confirmed_at is stamped on enable in slice a) so notifications are scoped to after opt-in.
- **Decision**: FIXED (Fix in plan) — added the since-opt-in predicate + a note to Phase 1 §2.

### F2 — Per-user vs fatal exception boundary contradictory for record_notification_sent

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 3 §1 — notification_main.py contract
- **Detail**: The contract said in-loop failures "continue" AND "BigQueryError from select/record re-raises (exit 1)" — conflicting for `record_notification_sent`, which runs inside the per-user loop. As written, one user's transient BQ write failure would abort all remaining recipients.
- **Fix**: Make the boundary explicit — only `select_pending_notifications` (+ table bootstrap) is fatal (exit 1); everything inside the per-user loop (send AND record, incl. BigQueryError) is per-user (log + had_failures + continue), auto-retried next pass.
- **Decision**: FIXED (Fix in plan) — rewrote the Phase 3 §1 exception-boundary wording.

### F3 — No cap on emails-per-pass (first-run / burst volume)

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 §1 — the send loop
- **Detail**: Digest bundling caps to one email per user per pass; F1's since-opt-in scope removes the backlog case. Only a scale concern against Gmail's ~500/day limit.
- **Fix**: None now — the ESP migration (out of scope) is the real fix.
- **Decision**: SKIPPED — non-issue at current scale.
