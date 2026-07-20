<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: E-mail Verification at Registration (PUL-86) — Full Plan

- **Plan**: `context/changes/email-verification-registration/plan.md`
- **Scope**: Full plan (4 phases; PR #162 squash-merged + epilogue ab15998)
- **Date**: 2026-07-20
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 4 observations (F1 fixed in triage)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Phase 1 was separately reviewed and APPROVED (`reviews/impl-review-phase-1.md`). This full
review covered phases 2-4 + cross-phase interactions with two sub-agents (drift; safety &
patterns) and a final full-suite run: **635/635 green**.

Drift agent: phases 2-4 all MATCH; one justified adaptation — the conftest `get_user` fake is
marker-based (`"unverified" not in uid`) instead of the plan's literal always-verified lambda,
forced by the session-scoped ExitStack (per-test mock flipping impossible) and still honoring
the explicit-SimpleNamespace contract from plan-review F3. Zero "What We're NOT Doing"
leakage; extras all supportive (`.auth-ok` styling, `"taken"` marker, backfill post-check).
Progress SHAs match actual commits; 4.5 is SHA-less **by design** (the epilogue contract
forbids writing its own SHA back — `/10x-archive` will surface it as a soft warning).

Safety agent: resend endpoint does identical request-path work for unknown / unverified /
already-verified (no enumeration or timing signal; unit tests pin byte-identical 204s); login
gate raises 403 before any BQ side effect with nothing logged; SPA surfaces use `textContent`
only (no XSS); mail template escaping equivalent to the reset sibling; login's extra Admin SDK
RPC fails closed (503). `TooManyAttemptsTryLaterError` handled consistently in both
background senders with no-alert tests.

## Findings

### F1 — conftest `get_user_by_email` was a bare MagicMock (truthy `email_verified`)

- **Severity**: ℹ️ OBSERVATION · **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: tests/e2e/conftest.py (PUL-85 patch)
- **Detail**: Truthy MagicMock attr meant every e2e resend exercised only the server's
  already-verified early return — the exact hazard plan-review F3 flagged for `get_user`.
- **Fix**: Explicit `SimpleNamespace` with the same `"unverified"` marker logic as
  `_fake_firebase_get_user`; the unverified-login e2e now exercises the background-send
  branch. Affected suites re-run green (19/19).
- **Decision**: FIXED

### F2 — resend limiter live in e2e (5/min/IP; suite currently makes 3 calls)

- **Severity**: ℹ️ OBSERVATION · **Impact**: 🏃 LOW
- **Location**: tests/e2e/conftest.py (only `_login_rate_limiter` is patched)
- **Detail**: Headroom of 2 clicks before future resend-clicking specs start hitting flaky
  429s. Note for future spec authors.
- **Decision**: ACCEPTED (note)

### F3 — backfill ops notes: auto-trust window, PII on stdout, double scan in `--apply`

- **Severity**: ℹ️ OBSERVATION · **Impact**: 🏃 LOW
- **Location**: scripts/backfill_email_verified.py
- **Detail**: All documented and accepted at current scale (4 accounts); dry-run prints the
  exact list to review before `--apply`. Prod run was a no-op (all accounts already
  verified — owner via the PUL-85 password-reset flow).
- **Decision**: ACCEPTED (documented)

### F4 — race: account deleted between register/resend and the background task

- **Severity**: ℹ️ OBSERVATION · **Impact**: 🏃 LOW
- **Location**: src/auth.py (`_send_verification_email_background`)
- **Detail**: Would raise from link-gen → one owner alert. Rare; noted so a stray alert
  isn't misread as SMTP breakage. Login `get_user` for a just-deleted user → 503
  (fail-closed), acceptable.
- **Decision**: ACCEPTED (note)

## Triage summary

- Fixed: F1 (conftest SimpleNamespace + marker logic; 19/19 affected e2e green)
- Accepted: F2, F3, F4 (ops/hardening notes)
- Final full suite: 635/635 green
