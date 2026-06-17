# X-post Selection & Amounts Guard Implementation Plan

## Overview

Fix two coupled defects in the morning X-post pipeline (PUL-40 / GitHub #54), both
confirmed on the 2026-06-17 `ranek` window:

- **Defect A — company starvation.** `fetch_top_n_for_window` applies `LIMIT N` to
  raw announcements *before* ticker dedup, so N raw rows ≠ N distinct companies.
  Seven TOWERINVT (TOW) `wyniki_finansowe` rows tied at score 120 filled all four
  slots; ASB (`kontrakt_znaczacy`, also 120) was dropped.
- **Defect B — number-less results posts.** `wyniki_finansowe` / `wyniki_sprzedazowe`
  posts shipped without amounts because `key_numbers` was empty (NewConnect EBI cover
  notes; numbers sit in an unfetched attachment). The generator falls back to a
  number-less `summary_pl` sentence.

Both fixes converge on one new pure selection function and one publish-time belt.

## Current State Analysis

The selection → generation → publish path spans four sites:

- **`db/bigquery.py:318` `fetch_top_n_for_window`** — single SQL query:
  `WHERE analysis_approved = TRUE AND event_type != 'inne' AND published_at BETWEEN …
  AND analysis_score >= @min_score ORDER BY analysis_score DESC LIMIT @n`. No ticker
  dedup, **no secondary sort key** (so equal-score ties cut nondeterministically),
  and `published_at` is not in the `SELECT`. Returns dicts keyed
  `announcement_id, ticker, company, title, structured_analysis, event_type,
  analysis_score, url`.
- **`src/post_generator.py:322-332` `generate_post`** — independently dedups tickers
  (first-occurrence wins, relies on score-DESC input order) and parses
  `structured_analysis` with `json5` to read `key_numbers` / `summary_pl`. When
  `key_numbers` is empty the prompt (`post_generator.py:139`) deliberately emits a
  number-less context sentence from `summary_pl`.
- **`post_main.py:206-214`** — a third dedup, building `tickers` and `company_scores`
  in the same first-occurrence order; `expected_tweets = len(tickers) + 2` drives the
  supervisor's exact-count check.
- **`post_main.py:56` `is_publishable(tweets)` / `src/post_supervisor.py:32`
  `validate_post`** — neither receives `event_type`, so a "results tweet must carry a
  number" rule cannot live there without new plumbing.

`structured_analysis` is a **`STRING`** column (`db/bigquery.py:52`) holding JSON with
`key_numbers: list[str]` and `summary_pl: str` (`src/analyzer.py:42-43`). `key_numbers`
is legitimately empty for qualitative events (`kontrakt_znaczacy`, `dywidenda`,
`zmiana_zarzadu`/compliance — see `analyzer.py:110`), so any empty-`key_numbers` guard
must be **event-type-narrow**.

## Desired End State

`fetch_top_n_for_window` returns up to N **distinct companies**, each the
highest-priority surviving announcement for its ticker, with `wyniki_finansowe` /
`wyniki_sprzedazowe` rows that have empty `key_numbers` removed *before* the top-N cut
(so a dropped slot is backfilled by the next-best company). Selection is deterministic
across runs. As a final safety net, `post_main` never publishes a `wyniki_*` body tweet
that carries no digit.

Verify: a fixture with 7 same-ticker top-score rows + 1 distinct lower-score company
yields 2 companies (not 1); a fixture with a number-less `wyniki_finansowe` top row +
lower-score companies drops the results row and backfills; the publish belt blocks a
hand-crafted number-less results tweet.

### Key Discoveries:

- Three independent dedup sites (`bigquery.py`, `post_generator.py:329`,
  `post_main.py:206`) — the SQL fix makes the latter two redundant belts. Keep them;
  do not remove (defense-in-depth, and `generate_post` is called directly in tests).
- `published_at` exists on the table but is absent from the current `SELECT` — the
  deterministic tie-break (`score DESC, published_at DESC`) requires adding it to the
  `ORDER BY` (it need not be returned in the dict).
- `is_publishable` / `validate_post` are pure over `list[str]` — the belt's
  event-type context only exists in `post_main`, where both `announcements` (with
  `event_type`) and the generated tweets are in scope.
- `json5` is already a dependency and the established parser for `structured_analysis`
  (`.claude/rules/gemini-ai.md`); the selection helper reuses it.

## What We're NOT Doing

- **Not** fetching NewConnect periodic-report attachments / XBRL to recover
  `key_numbers` for `wyniki_finansowe` — that extraction work is the out-of-scope
  follow-up named in `change.md`.
- **Not** adding a minimum-company floor — a window may legitimately yield a
  single-company thread; the existing `if not tickers` no-post path (`post_main.py:217`)
  handles an emptied pool.
- **Not** removing the `generate_post` / `post_main` dedups — they stay as belts.
- **Not** moving the empty-`key_numbers` guard into BigQuery SQL (rejected: fragile
  strict-JSON parsing of the `STRING` column; harder to test).
- **Not** threading `event_type` into `validate_post` — the belt lives in `post_main`.

## Implementation Approach

Centralize all selection logic (dedup + event-narrow drop + top-N) in one **pure**
function operating on plain dicts, so it is unit-testable with zero BigQuery mocking.
`fetch_top_n_for_window` becomes: over-fetch all qualifying rows in the window (ordered
by the deterministic key, with a generous safety cap), then delegate to the helper.
The publish belt is a small, independent check in `post_main` keyed on `event_type`.

## Critical Implementation Details

- **Ordering contract.** The helper assumes its input is pre-sorted by selection
  priority (`analysis_score DESC, published_at DESC`) and performs *order-preserving*
  first-occurrence dedup. The SQL `ORDER BY` is therefore the source of the tie-break;
  the helper must not re-sort in a way that breaks this. Keep the two in sync.
- **Drop-before-limit ordering.** Within the helper the sequence is: dedup by ticker →
  drop empty-`key_numbers` `wyniki_*` → take first N. Dropping must happen *before* the
  N-cut so a removed results row frees its slot for the next company.

## Phase 1: Selection layer — dedup-before-limit + event-narrow drop (Defects A + B)

### Overview

Introduce a pure selection helper and rewrite `fetch_top_n_for_window` to over-fetch
and delegate to it. This is the real fix for both defects.

### Changes Required:

#### 1. Pure selection helper

**File**: `src/post_selection.py` (new module + function `select_top_companies`)

**Intent**: Given announcement dicts already ordered by selection priority and a target
`n`, return up to `n` dicts — one per distinct ticker (first occurrence wins), with
`wyniki_finansowe` / `wyniki_sprzedazowe` rows whose `key_numbers` is empty removed
*before* the top-N cut. Lives in its **own dependency-light module** (imports only
`json5` + `logging`) so the data-access layer can call it without inverting the
dependency direction or dragging the `google.genai` import chain
(`post_generator` → `gemini_client`) into `db/bigquery.py`. `post_generator` may import
the same helper if it wants the shared drop logic.

**Contract**: `select_top_companies(rows: list[dict], n: int) -> list[dict]`. Pure, no
I/O. Reads `ticker`, `event_type`, and `structured_analysis` (parsed via `json5`, same
tolerant handling as `generate_post`: a parse failure → treat `key_numbers` as empty).
The two number-dependent event types are a module-level constant
(`NUMBER_DEPENDENT_EVENT_TYPES = {"wyniki_finansowe", "wyniki_sprzedazowe"}`) exported
so the belt in Phase 2 can import the same set. Order-preserving; rows without a
`ticker` are skipped (mirrors `generate_post`).

#### 2. Over-fetch + delegate in the fetch query

**File**: `db/bigquery.py` (`fetch_top_n_for_window`, ~line 338)

**Intent**: Stop letting raw `LIMIT N` decide companies. Fetch all qualifying rows in
the window ordered by the deterministic priority key, then hand them to
`select_top_companies` (imported from `src/post_selection.py`) for dedup + drop +
top-N. Keep the function's public return contract (list of the same dicts, ≤ N,
score-DESC) unchanged. This is the first `src.*` import in `db/bigquery.py`; keep it
pointed at the leaf `post_selection` module only.

**Contract**: SQL keeps the existing `WHERE` (approved, `event_type != 'inne'`, window,
`analysis_score >= @min_score`); change `ORDER BY analysis_score DESC` →
`ORDER BY analysis_score DESC, published_at DESC` and replace `LIMIT @n` with a generous
safety cap (e.g. `LIMIT 200`) so volume is bounded but the helper sees enough rows to
backfill. After building the row dicts, `return select_top_companies(rows, n)`.
`published_at` is referenced only in `ORDER BY`; it need not be added to `SELECT` or the
returned dict.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_post_selection.py tests/test_bigquery.py`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check .`

#### Manual Verification:

- Replaying the 2026-06-17 `ranek` data (7 TOW @120 + ASB @120) yields TOW **and** ASB,
  not 4× TOW.
- A window where the top row is a number-less `wyniki_finansowe` returns the next
  companies instead (slot backfilled), and the results row is absent.
- Real-BigQuery round-trip via `scripts/test_bq.py` confirms the rewritten query parses
  and returns distinct companies (mocked tests don't exercise the SQL parser —
  lessons.md "BigQuery — reserved keywords + mocked-test limits").

**Implementation Note**: After completing this phase and all automated verification
passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Publish-time belt — no number-less results tweet (Defect B defense-in-depth)

### Overview

A final safety net in `post_main`: even if the selection drop regresses or is bypassed,
a `wyniki_*` body tweet with no amount must not be published.

### Changes Required:

#### 1. Belt check before publish

**File**: `post_main.py` (in the approved branch, around the `_publish_to_x` call at
`post_main.py:237`)

**Intent**: For each announcement whose `event_type` is in the number-dependent set,
locate its body tweet by ticker and confirm the tweet contains at least one digit. If a
results tweet has no digit, treat the thread as not publishable (same outcome as the
existing `is_publishable` failure: persist `skipped`, still send the owner email) and
log a warning naming the ticker.

**Contract**: A helper (e.g. `_results_tweets_have_numbers(tweets, announcements)
-> bool`) importing `NUMBER_DEPENDENT_EVENT_TYPES` from `src/post_selection.py`. Body tweets
are `tweets[1:-1]`; match a tweet to its company via the parenthesised ticker
(`( $TICKER )` / `( TICKER )`) already used by `post_supervisor.validate_post:61`.
"Has a number" = `\d` present — intentionally coarse (a stray "Q1"/"2025" would pass);
this is acceptable because Phase 1's selection drop already guarantees non-empty
`key_numbers` (real figures) for any surviving `wyniki_*` row, so the belt is genuine
defense-in-depth, not the primary guarantee. Wire the result into the publish gate so a
failure routes to the skip path, not a hard pipeline error.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_post_main.py`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check .`

#### Manual Verification:

- A hand-crafted thread with a digit-less `wyniki_finansowe` body tweet is **not**
  published (status `skipped`), and the owner email still arrives.
- A normal thread with numbered results tweets publishes as before (no regression).

**Implementation Note**: After this phase and automated verification, pause for manual
confirmation.

---

## Testing Strategy

### Unit Tests:

- `select_top_companies` (new `tests/test_post_selection.py`): 7 same-ticker top rows + 1 distinct lower company → 2
  companies; number-less `wyniki_finansowe` top row → dropped + backfilled; number-less
  `kontrakt_znaczacy` / `dywidenda` → **kept** (qualitative events); `wyniki_sprzedazowe`
  treated same as `wyniki_finansowe`; no-ticker rows skipped; order preserved;
  unparseable `structured_analysis` → treated as empty `key_numbers`.
- `fetch_top_n_for_window` (`tests/test_bigquery.py`): a query-string assertion (matching
  the existing pattern at `tests/test_bigquery.py:125-139`) confirms the new
  `ORDER BY … , published_at DESC` and the safety cap; result delegates to the helper
  (assert ≤ N distinct tickers given a multi-row mock; existing `returns_rows` test still
  passes because the helper preserves mock order).
- Publish belt: results tweet without digit → blocked; with digit → allowed;
  qualitative-event thread (no `wyniki_*`) → unaffected.

### Manual Testing Steps:

1. Replay the 2026-06-17 `ranek` window data and confirm TOW + ASB both appear.
2. Confirm a number-less results announcement is dropped and the slot backfills.
3. Confirm the belt blocks a digit-less results tweet while the owner email still sends.

## Migration Notes

No schema or data migration. Pure query + selection logic change; the
`fetch_top_n_for_window` return contract is unchanged.

## References

- Change brief: `context/changes/xpost-selection-and-amounts-guard/change.md`
- Selection query: `db/bigquery.py:318`
- Callers of `fetch_top_n_for_window` (blast radius — exactly two): `post_main.py:200`
  and the dev script `_run_generate_post.py:162`. Return contract is unchanged, so both
  benefit automatically; no change needed in the dev script.
- Existing dedup (belt): `src/post_generator.py:322`
- Body-ticker match pattern: `src/post_supervisor.py:61`
- Publish gate: `post_main.py:98` (`_publish_to_x`), `post_main.py:56` (`is_publishable`)
- `key_numbers` semantics: `src/analyzer.py:71-127`
- Related: PUL-27 (analysis_score quality gate)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Selection layer — dedup-before-limit + event-narrow drop (Defects A + B)

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_post_selection.py tests/test_bigquery.py`
- [x] 1.2 Full suite passes: `uv run pytest`
- [x] 1.3 Linting passes: `uv run ruff check .`

#### Manual

- [x] 1.4 2026-06-17 `ranek` replay yields ASB (not 4× TOW); TOW correctly dropped as number-less `wyniki_finansowe`
- [x] 1.5 Number-less `wyniki_finansowe` top row dropped + slot backfilled
- [x] 1.6 Real-BQ round-trip confirms query parses + distinct companies

### Phase 2: Publish-time belt — no number-less results tweet (Defect B defense-in-depth)

#### Automated

- [ ] 2.1 Unit tests pass: `uv run pytest tests/test_post_main.py`
- [ ] 2.2 Full suite passes: `uv run pytest`
- [ ] 2.3 Linting passes: `uv run ruff check .`

#### Manual

- [ ] 2.4 Digit-less `wyniki_finansowe` tweet not published; owner email still sent
- [ ] 2.5 Normal numbered results thread publishes (no regression)
