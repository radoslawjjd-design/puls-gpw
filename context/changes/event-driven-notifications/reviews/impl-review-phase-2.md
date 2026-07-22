<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Event-driven watchlist notifications (PUL-81 slice b-v2)

- **Plan**: context/changes/event-driven-notifications/plan.md
- **Scope**: Phase 2 of 3
- **Date**: 2026-07-22
- **Verdict**: APPROVED
- **Findings**: 0 critical  0 warnings  3 observations
- **Commit reviewed**: 3329557

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS (1 observation) |
| Success Criteria | PASS |

## Success Criteria

- 2.1 `uv run pytest tests/test_main.py tests/test_notifier.py -q` → 9 passed (re-verified).
- 2.2 `uv run pytest --tb=short` → 682 passed at commit (no code changed since; only plan.md SHA write-back).

## Drift check

Every Phase 2 "Changes Required" contract item is present: imports of the sent-log
table creators + `select_recipients_for_announcement` + `record_notification_sent` +
`send_announcement_digest_email`; sent-log creators added to the startup block;
`base_url` read from `APP_BASE_URL` (default run.app URL); gated + doubly-isolated hook
after the `save_analysis_result` try-block; per-recipient in-run retry (3 attempts,
2.0s backoff) then `record_notification_sent`; run-level `notif_failures` → one
`send_alert` (never `sys.exit`); notifier link flipped to `?view=my-wallet` (hoisted
out of the item loop as it is now constant). BigQueryError containment (the
load-bearing isolation) is correct and test-proven.

## Findings

### F1 — Notification-failure alert carries a meaningless traceback

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: main.py (end-of-run alert) + src/notifier.py:397
- **Detail**: `send_alert()` embeds `traceback.format_exc()` and is designed for calls from inside an except handler. The new end-of-run call passes a freshly-constructed `RuntimeError` with no active exception, so `format_exc()` yields "NoneType: None" in the alert body. Functional but ugly.
- **Fix**: Accept as-is (count in subject/body already conveys the signal), or raise+catch to produce a real traceback.
- **Decision**: ACCEPTED (as-is; cosmetic, low value)

### F2 — Alert wording counts a record-only failure as a "missed send"

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (reliability nuance)
- **Location**: main.py `_notify_recipient` (record_notification_sent except → return 1)
- **Detail**: When the email sends OK but `record_notification_sent()` throws, the helper returns 1, so the alert reads "failed to send … permanently missed" even though the email went out and only the dedup record failed. Harmless (idempotent WHERE-NOT-EXISTS), but the wording can mislead during triage.
- **Fix**: Track record-failures separately, or soften wording to "failed to send or record".
- **Decision**: ACCEPTED (wording nuance; safe)

### F3 — Hook fires even after a best-effort save_analysis_result failure

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence (matches plan; noting a benign edge)
- **Location**: main.py (hook placed after the save_analysis_result try/except)
- **Detail**: If `save_analysis_result()` raises BigQueryError, its except logs and falls through; the hook still runs `select_recipients_for_announcement()`. The query filters `a.analysis_approved = TRUE`, and the row was never persisted as approved, so it returns `[]` — no email. Net effect: one wasted (isolated) query in a rare failure path. Matches the plan's contract (gate on result fields, hook after the save block), so not drift.
- **Fix**: None required. Could set a `save_ok` flag to skip the hook on save failure.
- **Decision**: ACCEPTED (plan-conformant; benign)
