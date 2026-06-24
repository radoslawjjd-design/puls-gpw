<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Switch Autocomplete + Watchlist Validation to Companies

- **Plan**: context/changes/switch-autocomplete-watchlist-to-companies/plan.md
- **Scope**: Phase 1-3 of 3 (full plan)
- **Date**: 2026-06-24
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence

- Two parallel sub-agent reviews (plan-drift detection + safety/quality/pattern compliance) read every changed/new file (`db/bigquery.py`, `src/company_profile.py`, `scripts/backfill_companies.py`, `tests/test_bigquery.py`, `tests/test_company_profile.py`) against the plan's "Changes Required" sections.
- All three functions (`list_tickers_missing_from_companies`, `profile_url_for_ticker`, `list_distinct_tickers`/`list_distinct_companies`) and the backfill script's flow match the plan's described contracts/SQL shapes verbatim.
- `git diff --name-only ce35c4b..HEAD` confirmed zero diff in `src/api.py`, `tests/test_api.py`, `tests/e2e/conftest.py` — matching the plan's "What We're NOT Doing" exactly.
- Full suite re-run live during review: 327 passed.
- All Phase 1-3 manual verification items were already executed live against production BigQuery/bankier.pl/the running API earlier in this session (real backfill of 265 tickers, coverage gap closed to 0, PKP spot-checked, autocomplete/watchlist endpoints exercised) — see plan.md Progress section for commit SHAs.

## Findings

### F1 — No inter-request delay against bankier.pl on the happy path

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/http_client.py:34-46, scripts/backfill_companies.py:46-71
- **Detail**: Verified by reading `src/http_client.py`: `get()` only calls `time.sleep(_REQUEST_DELAY)` when `attempt > 1` (between retries of the *same* URL). On the happy path — every call succeeds on the first try — there is no delay between successive distinct URLs. The plan's own "Performance Considerations" section assumes "on top of `src.http_client`'s existing 0.5s rate limit," but that limit doesn't apply across the per-ticker loop. `backfill_companies.py` made ~265 sequential live requests to bankier.pl in this session's real run with no enforced pacing beyond natural network latency. Pre-existing gap shared with `scripts/seed_companies.py` (already in production) — not a regression introduced by this change — and the plan explicitly scoped out "building retry/backoff beyond what `src.http_client.get()` already provides."
- **Fix A ⭐ Recommended**: Accept as-is — pre-existing, explicitly out of scope per the plan's "What We're NOT Doing" section.
  - Strength: The plan's own "What We're NOT Doing" section already rules this out; fixing it here would silently expand scope into a shared utility used by other scripts.
  - Tradeoff: The underlying politeness gap persists for any future one-off scraper script, not just this one.
  - Confidence: HIGH — verified directly against `http_client.py` source; this run completed without any 429/throttling response.
  - Blind spot: Haven't checked bankier.pl's actual rate-limit tolerance long-term; a much larger future backfill could behave differently.
- **Fix B**: Add an explicit `time.sleep(_REQUEST_DELAY)` after each ticker in `backfill_companies.py`'s loop, independent of retries.
  - Strength: Makes this one-off bulk scraper a better citizen toward a third-party site without touching the shared `http_client.py`.
  - Tradeoff: Adds ~265 × 0.5s ≈ 2 extra minutes to an already-run, already-idempotent script; cosmetic change to a script whose job is already done.
  - Confidence: MED — straightforward to add, but doesn't fix the underlying `http_client.py` gap for the next script that hits this pattern.
  - Blind spot: None significant.
- **Decision**: ACCEPTED (Fix A) — pre-existing, out of scope per plan, no throttling observed in the real run.
