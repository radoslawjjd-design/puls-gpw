<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Cap the decompressed size of a broker .xlsx

- **Plan**: `context/changes/xlsx-import-size-cap/plan.md`
- **Scope**: Phases 1-4 of 4
- **Date**: 2026-08-02
- **Verdict**: REJECTED → **APPROVED** after triage (9 of 10 findings fixed)
- **Findings**: 1 critical, 4 warnings, 5 observations

## Verdicts

| Dimension | At review | After fixes |
|-----------|-----------|-------------|
| Plan Adherence | PASS | PASS |
| Scope Discipline | PASS | PASS |
| Safety & Quality | **FAIL** | PASS |
| Architecture | PASS | PASS |
| Pattern Consistency | WARNING | PASS |
| Success Criteria | WARNING | PASS |

Method: two independent sub-agents (plan-drift; safety/quality/patterns), with the
critical finding reproduced independently by the reviewer before it was accepted.

## Findings

### F1 — Sparse row indices bypass the cell budget

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: `src/brokers/xlsx_reader.py:134-136`
- **Detail**: `budget.spend(len(row))` charged per cell. `reset_dimensions()` leaves
  `max_col = None`, so openpyxl's gap-filler rows are the **empty** list — and it emits
  one per missing row index between `<row r="…">` elements. `spend(0)` can never trip the
  budget while `rows.append(row)` grows regardless, and the row number is attacker-supplied
  and unbounded (`openpyxl/worksheet/_reader.py:287`).

  Reproduced independently: a **4 772-byte** upload declaring `<row r="20000000">` passed
  both ceilings, parsed "successfully", and peaked at **305.8 MB** — 60% of the instance.
  Not a regression (master's `list(iter_rows(...))` was equally exposed) but precisely the
  attack this change exists to stop, which made the plan's claim that the budget "bounds
  what iterating it produces" false.
- **Fix**: `budget.spend(max(len(row), 1))`, binding row count to the same budget, plus a
  regression test built from raw sheet XML (openpyxl's writer cannot emit a sparse `r=`).
  - Strength: one line, no cost on honest files (573 rows × 8 cols ≈ 4.6k of 200k).
  - Confidence: HIGH — reproduced before and after.
- **Decision**: **FIXED**. Re-verified on the same file: **305.8 MB → 1.7 MB**, refused.

### F2 — A corrupt .xlsx that was 422 became an unhandled 500

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: `src/brokers/xlsx_reader.py:105-111`
- **Detail**: `_reject_oversized` caught only `BadZipFile`, but `ZipFile.__init__` also raises
  `NotImplementedError` for `extract_version > 63` — one byte of the central directory. On
  master this reached `load_workbook` inside `except Exception` → `BrokerParseError` → 422.
  Moving the zip open ahead of `load_workbook` let it escape `read_sheets`; it is not a
  `BrokerImportError`, and `src/api.py` registers no exception handler → 500 with a traceback.
  This faithfully reproduced what the plan specified, so it is a plan defect, not drift.
- **Fix**: `except (zipfile.BadZipFile, NotImplementedError)`.
- **Decision**: **FIXED**, with a regression test. The crafted file was confirmed to raise
  `NotImplementedError` (not `BadZipFile`), so the test would have failed before the fix.

### F3 — Manifest entry count is unbounded

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: `src/brokers/xlsx_reader.py:107`
- **Detail**: The ceiling sums `file_size` but is blind to entry **count**. ~97k zero-length
  entries fit inside the 5 MB upload gate at ~486 bytes/entry ≈ 47 MiB of `ZipInfo` objects,
  while `sum(file_size) == 0` raises no objection.
- **Fix**: `_MAX_ZIP_ENTRIES = 256` checked before summing.
- **Decision**: **FIXED**, with a regression test.

### F4 — The 5 MB test pinned none of the new behaviour

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Success Criteria
- **Location**: `tests/test_api.py:2289`
- **Detail**: Asserting 413 + `list_user_portfolios` not called already held on master, since
  the pre-existing `len(data)` check precedes the first BigQuery call. Proven by stubbing
  `_reject_oversized_upload` to a no-op: the suite still passed.
- **Fix**: assert the distinguishing property — patch `_resolve_import` and assert it is never
  reached, since reaching it at all means the body was read.
- **Decision**: **FIXED**.

### F5 — Concurrency arithmetic left no headroom

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: `.github/workflows/deploy.yml:103`
- **Detail**: 8 × ~50 MiB ≈ 400 MiB of 512 MiB leaves ~112 MiB for interpreter + FastAPI +
  `google-cloud-bigquery`. Measured idle footprint is **83.6 MB** (17.9 bare interpreter,
  80.9 after importing `src.api`, 83.6 after `create_app()`), so 8 would have consumed the
  instance. Separately, `--concurrency` is service-wide: capacity fell from 160 to 16 to
  defend one upload path.
- **Fix**: `--concurrency=4` with `--max-instances=4`, holding total capacity at 16 while
  halving worst-case per-instance memory. `min-instances=0` keeps the cost unchanged at rest.
- **Decision**: **FIXED**.

### F6 — Row list copied a second time

- **Severity**: OBSERVATION · **Dimension**: Safety & Quality · `src/brokers/xlsx_reader.py:148`
- **Detail**: `rows[header_index + 1:]` held a second near-full copy alive, doubling peak at
  exactly the moment the budget had declared the file acceptable — the reason the F1 exploit
  measured 305.8 MB rather than ~153 MB.
- **Decision**: **FIXED** — `del rows[:header_index + 1]` after the header row is read.

### F7 — Cell-budget message omitted the offending value

- **Severity**: OBSERVATION · **Dimension**: Pattern Consistency · `src/brokers/xlsx_reader.py:92-94`
- **Detail**: Reported the limit but not how far over the file was; the sibling message two
  functions down interpolates both, which is this repo's convention.
- **Decision**: **FIXED**. Also gave the byte message one decimal place, so a file just past
  the ceiling no longer reads "zajmuje 8 MB, limit to 8 MB".

### F8 — Weak assertion on the byte ceiling

- **Severity**: OBSERVATION · **Dimension**: Success Criteria · `tests/test_brokers_xlsx_reader.py:146`
- **Detail**: `assert "8" in str(...)` also passes for "18 MB" or "limit to 80 MB".
- **Decision**: **FIXED** — asserts `"limit to 8 MB"`.

### F9 — Frontend message duplicated

- **Severity**: OBSERVATION · **Dimension**: Pattern Consistency · `static/index.html:4364`
- **Detail**: The third handler re-typed the wording instead of composing
  `_IMPORT_TOO_LARGE_MSG`; two copies drift.
- **Decision**: **FIXED** — composed from the shared constant.

### F10 — Plan success criterion 3.1 does not run the tests it guards

- **Severity**: OBSERVATION · **Dimension**: Success Criteria · `plan.md`, Phase 3
- **Detail**: `uv run pytest tests/test_api.py -k import` does not match the new tests, whose
  names contain no "import". The criterion would pass while the regressions it exists to catch
  went unrun. Flagged during implementation; the phase was verified against the whole file
  instead.
- **Decision**: **ACCEPTED** — recorded rather than edited, since rewriting a success criterion
  after the fact to match what was actually run is how criteria stop meaning anything. Worth
  carrying into the next plan as a rule: a criterion naming a `-k` filter must be run once
  against the tests it claims to cover.

## Verification after fixes

- `uv run pytest --ignore=tests/e2e` → **823 passed** (+3 regression tests)
- `uv run ruff check src tests` → clean
- F1 exploit re-run: `BrokerFileTooLargeError`, peak **1.7 MB** (was 305.8 MB)
- `tests/e2e` still uncollectable for reasons unrelated to this change — PUL-121 / GH #240

## Triage summary

```
Fixed:     F1, F2, F3, F4, F5, F6, F7, F8, F9   (9)
Accepted:  F10                                  (1)
```
