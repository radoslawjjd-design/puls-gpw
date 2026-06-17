<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Portfolio status X-post generator skill (PUL-39)

- **Plan**: context/changes/portfolio-xpost-skill/plan.md
- **Mode**: Deep
- **Date**: 2026-06-17
- **Verdict**: REVISE → SOUND (after fixes)
- **Findings**: 1 critical, 1 warning, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING (tied to F1, resolved by fix) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL (resolved by fixes) |
| Plan Completeness | PASS |

## Grounding

7/7 paths ✓ (1 expected-new test file: tests/test_gemini_client.py), 9/9 symbols ✓, brief↔plan ✓

## Findings

### F1 — No partial-failure/retry story across the two independent threads

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Phase 3 ("halts if a required wallet subfolder is missing or empty") + Phase 4 (approval/publish/persist/archive)
- **Detail**: Phase 3's contract halts if any wallet subfolder is empty. Phase 4's archive step moves screenshots out per-thread on success. If thread A (main+IKZE) publishes successfully but thread B (short+long) fails, a retry finds thread A's wallets already-archived-empty and incorrectly halts the entire run — no way to recover without manual intervention (e.g. moving screenshots back out of archive/).
- **Fix A ⭐ Recommended**: Per-thread granularity + "already done today" check
  - Strength: Before halting on an empty subfolder, check `portfolio_snapshots` for a row with `snapshot_date=today` for that wallet — if found, treat as already-published-today and skip rather than halt. Process the two threads as independent units end to end. Reuses the BigQuery table this plan already builds; no new infrastructure needed.
  - Tradeoff: Halt condition becomes two-part (empty AND no row today) instead of one-part — slightly more logic to implement and test.
  - Confidence: HIGH — reuses existing infrastructure.
  - Blind spot: Doesn't cover the case where a screenshot is genuinely stale (wallet folder empty, no row today, but data was meant to be posted yesterday) — acceptable, out of scope.
- **Fix B**: All-or-nothing across the whole run
  - Strength: No per-wallet bookkeeping — simplest to implement, one invariant.
  - Tradeoff: A successfully-published thread's tweets stay live on X with zero record in BigQuery and its screenshots never archived unless rolled back manually — worse data-integrity gap, and rollback of an already-published X thread isn't possible anyway (X has no unpublish), which undercuts the simplicity argument.
  - Confidence: MEDIUM.
  - Blind spot: The "all or nothing" framing only ever applies to BQ/archive, never to the publish itself.
- **Decision**: FIXED (via Fix A) — plan.md updated: new "Per-thread retry semantics" note in Critical Implementation Details, Phase 3's contract now checks `portfolio_snapshots` before halting, Phase 4's contract makes the two threads explicitly independent (skip persist/archive on a thread's publish failure, leave its wallets retryable), and a new manual verification item (4.6) added to Phase 4 + Progress.

### F2 — Vision extraction model choice left unspecified, ignoring the codebase's own escalation precedent

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 3 — Vision extraction helper
- **Detail**: Phase 3's contract said the new `extract_portfolio_snapshot()` is "built on the existing get_client() singleton," which defaults `GEMINI_MODEL` to `gemini-2.5-flash-lite` (the cheap/bulk tier, also used for news classification). The codebase has an explicit precedent for escalating model tier on accuracy-critical tasks (`tools/ai-code-reviewer/src/agent.ts` uses `gemini-2.5-flash` non-lite "because code review needs more reasoning than the app's news classification"). Reading financial figures off a screenshot is at least as accuracy-critical, but the plan didn't address model tier at all.
- **Fix**: Add a dedicated `GEMINI_VISION_MODEL` env var (default `gemini-2.5-flash`, not -lite) for the extraction call, instead of silently reusing `GEMINI_MODEL`.
  - Strength: Matches the established "harder task → less-lite tier" pattern; keeps `GEMINI_MODEL` untouched for the unrelated ESPI/EBI text pipeline.
  - Tradeoff: One more env var to document and set; marginally higher per-call cost than flash-lite.
  - Confidence: MEDIUM — flash (non-lite) is the right next tier per precedent, but real accuracy on actual XTB screenshots is still unverified until Phase 3's manual verification step runs.
- **Decision**: FIXED — plan.md's Phase 3 vision-extraction contract now specifies `GEMINI_VISION_MODEL` defaulting to `gemini-2.5-flash`, with the precedent cited inline.
