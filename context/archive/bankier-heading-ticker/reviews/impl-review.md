<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Bankier heading ticker (PUL-102)

- **Plan**: `context/changes/bankier-heading-ticker/plan.md`
- **Scope**: Phases 1–3 of 4 (phase 4 is prepared, not executed, by design)
- **Date**: 2026-08-04
- **Verdict**: APPROVED (after F1 and F2 fixed in review)
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (after F1) |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | WARNING — deliberate, documented (F3) |
| Pattern Consistency | PASS |
| Success Criteria | PASS (automated); phase 4 pending the owner |

## Findings

### F1 — The ticket's own ticker pattern rejects two real symbols

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: `src/tickers.py`, `plan.md` Phase 1 contract
- **Detail**: The plan copied the ticket's suggested `^[A-Z0-9][A-Z0-9-]{0,9}$`.
  The hyphenated test case failed against it: `CREOTECH-PDA` is 12 characters and
  `CRQUANTUM-PDA` is 13, both correctly-parsed bankier symbols already in
  `companies`. Shipping the ticket's pattern would have made the new guard reject
  two working rows — turning an explicitly out-of-scope quirk into a fresh outage,
  which is a worse failure than the one being fixed.
- **Fix**: Widen to 16 and record why. Length was never the discriminator: `Żabka`
  and `przejęty` fail on lowercase at any bound, so the bound only has to clear
  the longest real value.
  - Strength: Grounded in the actual length distribution of the table (13 max),
    not a guess. Verified by exercising the predicate over every real value plus
    adversarial ones (`zab`, `ZAB!`, `ZAB `, `-ZAB`, `''`, `None`) — all correct.
  - Tradeoff: A longer bound admits more nonsense in principle; in practice case
    and charset do the work and 16 admits nothing lowercase.
  - Confidence: HIGH — checked against the live table.
  - Blind spot: None significant.
- **Decision**: FIXED — code widened, plan amended with the reason.

### F2 — The NULL-ticker rationale contradicted the pipeline's actual policy

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `db/bigquery.py` (`update_parsed_content` docstring)
- **Detail**: The guard's stated reason was "losing the filing would cost more
  than losing its ticker". That is not this pipeline's policy: `main.py` already
  skips an announcement outright when no ticker resolves — *"not a tradable
  company… we don't want these in the DB at all"* — and does not dedupe it, so a
  later run re-checks it. With the extractor now returning `None` instead of a
  brand word, that existing skip is what handles a broken heading, and it handles
  it better than a null-ticker row would.
  The guard's behaviour is still right as defence in depth for other callers; only
  its justification was wrong, which matters because the next reader would take it
  as the policy.
- **Fix**: State that it is defence in depth and name the primary control.
- **Decision**: FIXED.

### F3 — `db.bigquery` gains a third import from `src`

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architecture
- **Location**: `db/bigquery.py`, `tach.toml`
- **Detail**: `tach.toml` states "db has no upward dependencies" and records the
  existing violations. Importing `is_valid_ticker` from `src.tickers` deepens that
  by one. The alternative — validating only at the callers — abandons the property
  that makes the guard worth having: that no write path, including ones not yet
  written, can bypass it. Bankier changed its markup once and will again, and it
  is not the only upstream page.
- **Decision**: ACCEPTED — `src/tickers.py` is import-free, which is the cheapest
  possible shape for this violation, and `tach.toml`'s note now records the choice
  and the eventual fix (a shared leaf package).

## Success Criteria

| Check | Result |
|-------|--------|
| Phase 1 — extractor takes the ticker-shaped group | PASS |
| Phase 2 — both company writes refuse, announcement nulls | PASS |
| Phase 3 — no cashtag beats a wrong one; supervisor rejects | PASS |
| `uv run pytest --tb=short` | PASS — 1059 passed |
| `uv run ruff check` | PASS |
| Phase 4 — data repair | PENDING — `data-repair.sql`, human-only |

## Notes

- **Three layers, deliberately.** The parser fix alone would close today's hole
  and leave the shape of the failure intact. What made this bug expensive was that
  a wrong value travelled from one parser to three tables and a public tweet with
  nothing in between objecting. The database guard and the cashtag guard are what
  make the next markup change cheap.
- **The generator's ALL-CAPS regex was not the bug and was not widened.**
  `_PAREN_TICKER_RE` requires uppercase so that `(2025)` is left alone — a correct
  constraint that happens to make every deterministic repair blind to a ticker
  that is not one. The supervisor's new check uses a separate, case-blind pattern
  precisely because it has to *catch* `$Zabka`, which is the opposite job.
- **Data verified rather than assumed.** The ticket's figures had grown: 4
  announcements and 27 price rows under `Żabka`, not 1 and 24. The two orphaned
  price dates are real trading sessions — 732 other tickers have rows for
  2026-07-27 and 2026-07-28 — so the gap in `ZAB` is genuine and those bankier
  rows are the only prices held for those days. The repair re-keys them rather
  than deleting, and matches them dynamically so the SQL survives further drift.
- **MegaPixel's price is frozen** — 1.70 PLN, turnover 840.55, 11 transactions,
  identical every day since 2026-07-28. That, plus a heading reading `przejęty`,
  is why the owner chose delete over rename: an `MPS` row would be a live-looking
  identity for a delisted instrument that keeps accruing frozen rows.
