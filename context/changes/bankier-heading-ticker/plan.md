# Bankier heading ticker — Implementation Plan

## Overview

One parser takes the wrong parenthesised group and a brand word becomes a
company's identity across three tables and one public tweet. Fix the extractor,
then close the two boundaries that let a bad value travel that far: the database
write and the cashtag that reaches X.

## Current State Analysis

`_extract_heading` (`src/company_profile.py:40-50`) does:

```python
m = re.search(r"\(([^)]+)\)", raw)   # first group wins
ticker = m.group(1).strip()
company = raw[: m.start()].strip() or None
```

The company name is correct in every observed case — it is the text before the
**first** paren. Only the ticker is wrong, and only when bankier emits more than
one group.

Nothing downstream caught it:

- `db/bigquery.py` `upsert_company` (`:1921`) and `insert_company_if_absent`
  (`:1962`) MERGE on whatever ticker they are handed, with no shape check.
- `src/post_generator.py:430` reads `enriched[0]["ticker"]` straight into the
  prompt as `cashtag_spolki`.
- Every deterministic repair in the generator is anchored on `_PAREN_TICKER_RE`
  (`src/post_generator.py:200`), which requires ALL CAPS so that `(2025)` is left
  alone. That constraint is right, and it is exactly why `Zabka` slipped past
  `_normalize_ticker_spacing`, `_enforce_body_cashtag` and
  `_enforce_body_ticker_ref` untouched.

### Key discoveries

- **A hyphen must be legal.** `CREOTECH-PDA` and `CRQUANTUM-PDA` are correctly
  extracted allotment-rights symbols. A validator of `^[A-Z0-9]{1,10}$` would
  reject them and turn an out-of-scope quirk into a new outage.
- **`db/bigquery.py` already imports from `src`** twice (`src.exceptions`,
  `src.post_selection`), which `tach.toml` records as a known violation of "db has
  no upward dependencies". Putting the shared predicate in a pure `src` module and
  importing it from `db` deepens that violation by one import. The alternative —
  validating only at the callers — gives up the property that makes the guard
  worth having, namely that no future write path can bypass it. Taking the import
  and extending the recorded note is the better trade.
- **`insert_company_if_absent` is the riskier of the two writes**: it is called
  with partial data on the announcement path, so it is the one that created the
  bare `Żabka` row.

## Desired End State

`fetch_company_profile` on the Żabka profile returns `ticker='ZAB'`. A heading
whose groups are all non-ticker-shaped yields `ticker=None` — a visible gap the
pipeline already handles — rather than a brand word. Neither company write accepts
a malformed ticker. A thread whose top company has a malformed ticker publishes
without a cashtag instead of with a wrong one.

## What We're NOT Doing

- **Executing the data repair.** Destructive production operations are human-only.
  The SQL is written to `data-repair.sql` for the owner.
- Deleting the published `$Zabka` tweet — the owner's decision, recorded as "leave".
- `CREOTECH-PDA` / `CRQUANTUM-PDA` — correctly parsed, separately wrong.
- Changing `_PAREN_TICKER_RE`. Its uppercase-only constraint is correct; the fix
  is to stop feeding it garbage, not to widen it.

---

## Phase 1: The extractor takes the ticker-shaped group

### Changes Required:

#### 1. Shared ticker-shape predicate

**File**: `src/tickers.py` (new)

**Intent**: One definition of "looks like a ticker", used by the parser, both
database writes, and the post generator, so the four cannot drift apart on what
they accept. Pure — no imports — so importing it from `db` costs the layering
nothing beyond the direction itself.

**Contract**: `is_valid_ticker(value: str | None) -> bool`, true for
`^[A-Z0-9][A-Z0-9-]{0,15}$`. The hyphen is deliberate: `CREOTECH-PDA` is a real
bankier symbol and rejecting it would break a working path.

> **Amended during implementation.** This originally read `{0,9}`, copied from the
> ticket. The hyphenated test case failed against it: `CREOTECH-PDA` is 12
> characters and `CRQUANTUM-PDA` is 13, so the ticket's own suggested pattern
> would have rejected both real symbols along with the junk. Length was never the
> discriminator — case and character set are, and `Żabka` / `przejęty` fail on
> lowercase at any bound. Widened to 16, which is headroom over the longest value
> in the table rather than a meaningful limit.

#### 2. Extractor

**File**: `src/company_profile.py`

**Intent**: Scan **all** parenthesised groups and take the last one that is
ticker-shaped; keep the company name as the text before the first paren, which is
already correct. When no group qualifies, return `ticker=None` — a missing ticker
is a gap the pipeline handles, a wrong one is silent corruption.

**Contract**: `_extract_heading` returns `(ticker | None, company | None)`; uses
`re.finditer` over `\(([^)]+)\)` and `is_valid_ticker` to choose.

### Success Criteria:

#### Automated Verification:

- `Zabka Group SA (Żabka) (ZAB)` → `("ZAB", "Zabka Group SA")`
- `MegaPixel Studio SA (przejęty) (MPS)` → `("MPS", "MegaPixel Studio SA")`
- `Alior Bank SA (ALR)` → `("ALR", "Alior Bank SA")` — single-group control
- `Creotech Instruments SA (CREOTECH-PDA)` → hyphen survives
- A heading with no parens → `(None, None)`
- A heading whose only group is not ticker-shaped → `(None, name)`, not the word
- Full suite green, linting passes

---

## Phase 2: The database refuses a malformed ticker

### Changes Required:

#### 1. Company writes

**File**: `db/bigquery.py`

**Intent**: `upsert_company` and `insert_company_if_absent` reject a
non-ticker-shaped value with a WARNING and no write. The parser fix stops today's
source; this stops the next one, wherever it appears.

**Contract**: both functions return early after `logger.warning` when
`is_valid_ticker` is false. Return type stays `None`, so no caller changes.

#### 2. Announcement writes

**File**: `db/bigquery.py`

**Intent**: The same check on the announcement ticker write path, which is how
`Żabka` reached four announcement rows.

**Contract**: a malformed ticker is stored as `NULL` rather than rejecting the
whole announcement — losing the filing would be worse than losing its ticker, and
a null ticker is a state the schema and the UI already handle.

### Success Criteria:

#### Automated Verification:

- `upsert_company("Żabka", …)` performs no query and logs a warning
- `insert_company_if_absent("przejęty", …)` performs no query
- A valid ticker still writes, including a hyphenated one
- An announcement with a malformed ticker is stored with `ticker=None`
- Full suite green, linting passes

---

## Phase 3: No cashtag beats a wrong cashtag

### Changes Required:

#### 1. Post generator

**File**: `src/post_generator.py`

**Intent**: When the top company's ticker is not ticker-shaped, build the thread
without a cashtag rather than with a malformed one. Reach is worth less than not
publishing an error.

**Contract**: the `cashtag_spolki` prompt line is omitted when
`is_valid_ticker(enriched[0]["ticker"])` is false, and a warning names the ticker.

#### 2. Supervisor

**File**: `src/post_supervisor.py`

**Intent**: A malformed cashtag in generated text rejects the attempt, so the
existing retry budget gets a chance to produce a clean thread.

**Contract**: an added check in the existing validation sequence, reported through
whatever issue-string mechanism the supervisor already uses.

### Success Criteria:

#### Automated Verification:

- A malformed top ticker produces a prompt with no `cashtag_spolki` line
- A valid top ticker is unchanged
- The supervisor rejects a thread carrying a malformed cashtag
- Full suite green, linting passes

---

## Phase 4: Data repair — prepared, not executed

### Changes Required:

**File**: `context/changes/bankier-heading-ticker/data-repair.sql` (new)

**Intent**: The owner runs this. Ordered so no step depends on data a previous
step removed, with a verification query at the end.

**Contract**: statements for — copy `isin`/`hop_url` from `Żabka` onto `ZAB`
before deleting `Żabka`; re-key the two orphan price dates to `ZAB`; delete the
duplicate price rows; re-point the four `Żabka` announcements to `ZAB`; delete the
MegaPixel row and its prices per the owner's decision; then re-run the sweep.

### Success Criteria:

#### Manual Verification:

- The sweep returns only the two `-PDA` rows
- `ZAB` carries the isin and hop_url
- The next `company_stats_main` run prices `ZAB` from the official source and no
  `Żabka` row reappears

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: The extractor takes the ticker-shaped group

#### Automated

- [x] 1.1 Two-group and status-marker headings yield the real ticker
- [x] 1.2 Single-group and hyphenated controls unchanged
- [x] 1.3 No qualifying group yields None, never a brand word
- [x] 1.4 Full suite green, linting passes

### Phase 2: The database refuses a malformed ticker

#### Automated

- [x] 2.1 Both company writes reject a malformed ticker without querying
- [x] 2.2 A valid, including hyphenated, ticker still writes
- [x] 2.3 An announcement with a malformed ticker stores ticker=None
- [x] 2.4 Full suite green, linting passes

### Phase 3: No cashtag beats a wrong cashtag

#### Automated

- [x] 3.1 A malformed top ticker omits the cashtag line
- [x] 3.2 The supervisor rejects a malformed cashtag
- [x] 3.3 Full suite green, linting passes

### Phase 4: Data repair

#### Manual

- [x] 4.1 Ran 2026-08-04 with the owner's explicit authorisation, step by step
- [x] 4.2 Sweep returns only the two `-PDA` rows
- [ ] 4.3 Next stats run prices `ZAB` officially, no `Żabka` reappears
