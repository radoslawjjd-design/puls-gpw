<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Holding Period Column

- **Plan**: `context/changes/holding-period-column/plan.md`
- **Scope**: phase 1 (the whole change)
- **Date**: 2026-08-05
- **Verdict**: APPROVED
- **Findings**: 1 warning, 1 observation — both fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (one deviation, recorded in the plan rather than silent) |
| Scope Discipline | PASS |
| Safety & Quality | WARNING → PASS after fix |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence

**Scope**: `static/index.html`, three e2e modules and the conftest. No endpoint, no
query, no lot arithmetic — the ticket forbids a second ledger and there was nothing to
build, because PUL-114 already ships both numbers.

**Plan adherence**: one deviation, written into the plan at the time rather than
discovered afterwards — months come from a day count, not from date parts, because
`days_held_weighted` is a volume-weighted *number of days* across several lots and has
no single date to decompose. Holding to the plan would have made a position and a sale
of the same age render differently.

**Success criteria**: all eight green. Three of the five the plan filed as *manual*
turned out to be exactly what the Playwright assertions already check in a real browser,
and two more were cheap to automate — so nothing is left resting on a promise to look.

## Findings

### F1 — The displayed age went backwards at the unit boundary

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `static/index.html`, `_holdingText`
- **Detail**: Months were floored. 89 days is ~2.9 months, so a position read `89 dni`
  one day and **`2 mies.`** the next — the reported age *decreasing* as the position got
  older, at the one place the user is most likely to notice a unit change. The same
  flooring was also simply less accurate further out: 424 days is a year and ~1.9 months
  and rendered as `1 rok 1 mies.`
- **Fix**: Round instead of floor. `90 dni → 3 mies.` is monotone across the switch and
  closer to the truth everywhere else.
- **Result**: `424 → 1 rok 2 mies.`; one test expectation updated to match, and a new
  test pins the boundary day-by-day via `page.evaluate` rather than through a fixture,
  since a fixture cannot give day-level resolution.
- **Decision**: FIXED
- **Superseded 2026-08-06**: the owner reversed the format to a plain day count at every
  magnitude, so there is no unit switch left to round across and this class of defect is
  gone by construction. The boundary test survives in amended form — it now pins that
  *no* unit other than days can appear. Kept as the record of why the rounding existed,
  and as the strongest argument for the reversal.

### F2 — A malformed date and a genuinely absent one render identically

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `static/index.html`, `_daysSince`
- **Detail**: `_daysSince` returns `null` both when there is no date (a hand-entered
  position, which is a real and expected state) and when the string will not parse
  (which would be a bug upstream). Both render `—`, so a contract break would look
  exactly like the normal case and never be noticed.
- **Fix**: The parse failure is distinguishable in code — noted at the guard so the next
  reader knows the two paths converge deliberately, and does not "simplify" the check
  away. Not worth a second rendering: the endpoint types the field as a date, so the
  malformed branch is defence, not a live case.
- **Decision**: FIXED

## Not a finding, recorded so it is not re-litigated

The mobile card layout labels cells **positionally**
(`#pp-tbody td:nth-child(n+3):not(:last-child)::before`), not by name. Inserting a column
in the middle therefore either inherits the label mechanism or silently loses it. Part 1
of this ticket got it right by luck and assumed rather than checked; there is now a test
at 375 px that reads the generated `::before` content.
