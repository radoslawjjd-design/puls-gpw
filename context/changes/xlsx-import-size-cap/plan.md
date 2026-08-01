# Cap the decompressed size of an uploaded broker .xlsx — Implementation Plan

## Overview

The broker-import endpoints accept an `.xlsx` upload bounded only by 5 MB of **compressed**
bytes, measured after the whole body is already resident. `.xlsx` is a zip archive, so a
conforming upload can expand to a multiple of that in memory and OOM a 512 MiB Cloud Run
instance serving up to 80 concurrent requests. This plan installs a ceiling on the
**decompressed** size, in the two places that can actually see it.

## Current State Analysis

`read_sheets` (`src/brokers/xlsx_reader.py:25-41`) opens the workbook with `read_only=True`
and then, per sheet, calls `list(sheet.iter_rows(values_only=True))` (`:52`). The only guard is
`_IMPORT_MAX_BYTES = 5 MiB` (`src/api.py:368`), checked at `src/api.py:386` — after
`await file.read()` at `src/api.py:1359` / `:1371` has copied the entire spooled upload into
one `bytes`.

Reading the installed **openpyxl 3.1.5** source settles what streams and what does not:

- `reset_dimensions()` is a two-attribute reset with no pre-scan
  (`.venv/Lib/site-packages/openpyxl/worksheet/_read_only.py:164-170`) — it does **not** break
  streaming, so the ticket's worry there is unfounded.
- `_cells_by_row` is a real generator reading the sheet XML incrementally
  (`worksheet/_read_only.py:60-100`). The `list(...)` at `xlsx_reader.py:52` is what discards
  that. After `reset_dimensions()` sets `max_row = None`, the generator's own `break` guard
  (`_read_only.py:85-86`) never fires, so it runs to EOF — lazily, but unbounded.
- **`load_workbook` eagerly materialises the whole shared-strings table.**
  `ExcelReader.read()` calls `read_strings()` unconditionally (`reader/excel.py:288-291`),
  which reads `sharedStrings.xml` into a Python list (`:139-147`) and hands it to every
  `ReadOnlyWorksheet` (`:228`). `read_only=True` gates the worksheet class, not the strings.

That last point is decisive: **a bomb whose payload sits in `sharedStrings.xml` is fully
resident before the first `iter_rows` call**, so an in-iteration counter alone — the fix the
ticket proposes — would close the row bomb and leave the string bomb wide open.

The zip manifest, however, is a trustworthy bound. `ZipExtFile.__init__` sets
`self._left = zinfo.file_size`, so reads are hard-capped by the declared size: a manifest that
under-reports yields truncated data and a CRC failure, not unbounded expansion. Summing
`file_size` over `ZipFile(...).infolist()` is therefore a sound upper bound on everything
openpyxl can extract.

Two further facts shape the solution:

- `tests/test_brokers_xtb.py:406-428` walks the AST of every `src/brokers/*.py` and fails on
  any import of `db`, `fastapi`, `google`, `starlette`. The reader cannot raise `HTTPException`.
- `src/api.py:399-401` catches the entire `BrokerImportError` hierarchy and maps it to 422,
  while the existing size guard answers 413 (`src/api.py:387`) — the codebase's only 413.

Scale for calibration: real XTB exports are 15–41 KB, 573 rows total across both sheets.

## Desired End State

An upload whose decompressed content exceeds the ceiling is rejected with **413** and a message
naming the limit, **before** any large allocation happens — verifiably, by measuring process
RSS against a purpose-built bomb. Honest imports (three orders of magnitude below the ceiling)
are untouched. The reader stops materialising worksheets no parser consumes, and the existing
5 MB gate stops measuring a cost already paid.

### Key Discoveries:

- Shared strings load eagerly at `load_workbook` time — `reader/excel.py:288-291`. Only a
  pre-open guard can bound them.
- `reset_dimensions()` is free and must be kept — `xlsx_reader.py:49-50`, locked by
  `tests/test_brokers_xlsx_reader.py:65-77`.
- The zip manifest is a sound upper bound — `zipfile.ZipExtFile.__init__` caps reads at
  `zinfo.file_size`.
- `sheets["Cash Operations"]` (`src/brokers/xtb.py:285`) is the only key the parser ever
  indexes, while `read_sheets` materialises all of them (`xlsx_reader.py:39`).
- `_IMPORT_MAX_BYTES` has **no test at all** — grep over `tests/` for `413|MAX_BYTES|przekracza`
  returns nothing.
- Cloud Run: `--memory=512Mi --cpu=1 --max-instances=2`, no `--concurrency` → default 80
  (`.github/workflows/deploy.yml:81-97`).

## What We're NOT Doing

- Not changing how the body reaches the app. Starlette spools the file part with no cap
  (`starlette/formparsers.py:209`) and FastAPI parses the form before dependencies resolve, so
  a fully pre-emptive limit would mean custom middleware or a declared Cloud Run request cap —
  out of scope here.
- Not touching the double upload (preview then commit). That is a deliberate statelessness
  decision documented at `src/api.py:374-377`.
- Not addressing GH #214 (partial state on mid-sequence failure) or #215 (XTB cross-check
  test), the sibling findings from the same review.
- Not unifying the 404-vs-403 inconsistency (accepted finding F8) even though we touch these
  endpoints.
- Not making the ceilings environment-configurable. API-layer caps in this repo are hard-coded
  literals (`_IMPORT_MAX_BYTES`, `_WL_SENTIMENT_LIST_CAP`, `_CALENDAR_MAX_YEARS_BACK`).
- Not adding a **pre-upload** size check in the browser. Phase 3 corrects the message shown
  *after* the server rejects a file; it does not inspect the file before sending it.

## Implementation Approach

Two layers, because one is provably insufficient:

1. **Before `load_workbook`** — sum `file_size` over the zip manifest; reject above 8 MiB. This
   is the only thing standing between a `sharedStrings.xml` bomb and the heap.
2. **During iteration** — replace `list(...)` with a bounded loop carrying a per-workbook cell
   budget of 200 000; abort the moment it is exceeded.

Both raise a new `BrokerFileTooLargeError`, which keeps the reader free of `fastapi` (satisfying
the AST test) while a dedicated `except` clause in `src/api.py` maps it to 413 — matching the
status the neighbouring size guard already uses.

Thresholds are set tight deliberately: 8 MiB is ~200× the largest real export and 200 000 cells
is ~50× its cell count, while a Python cell tuple weighs far more than its XML bytes, so the
byte figure understates true memory. With 512 MiB shared across many concurrent requests,
headroom matters more than tolerance for hypothetical giant exports.

**Residual risk, stated plainly.** These ceilings bound a *single* request to roughly 50 MiB
(≈8 MiB of XML expanded into Python objects, plus the `bytes` copy and the tmpfs spool). They
say nothing about how many such requests share an instance. At Cloud Run's default of 80
concurrent requests per 512 MiB instance, ten simultaneous maximum-size uploads still exhaust
it. Phase 4 closes that by declaring `--concurrency` — the flag the service has simply never
passed. The per-request and per-instance bounds are two halves of one guarantee; neither alone
is sufficient. Note also that the arithmetic above is estimated, not measured — Phase 1's
manual step is what confirms it.

## Critical Implementation Details

**Ordering of the `except` clauses.** `src/api.py:399` currently catches `BrokerImportError`,
the base class. A new `except BrokerFileTooLargeError` must sit **before** it. Reversed, the
subclass is swallowed by the base handler and the endpoint silently answers 422 instead of 413
— with every test that only asserts "rejected" still green.

**The non-zip path must keep its current error.**
`tests/test_brokers_xlsx_reader.py:80-83` feeds `b"this is not a spreadsheet"` and expects
`BrokerParseError`. Opening a `ZipFile` first moves the failure earlier: `zipfile.BadZipFile`
now fires before `load_workbook` ever runs. The manifest pre-check has to translate that into
the same `BrokerParseError` (`"nie udalo sie odczytac pliku xlsx: ..."`) or the test breaks for
a reason unrelated to this change.

**The cell budget is per workbook, not per sheet.** A bomb split across twenty sheets each just
under a per-sheet cap would pass. The budget must be threaded through `_read_one` and
decremented across all sheets read.

## Phase 1: Ceiling in the reader

### Overview

Both guard layers plus the exception they raise. This is where the memory fix actually lands,
so this is also where it gets measured.

### Changes Required:

#### 1. New exception

**File**: `src/brokers/errors.py`

**Intent**: Give "the file is too large once decompressed" its own type, so the API layer can
answer 413 without the reader knowing HTTP exists.

**Contract**: `class BrokerFileTooLargeError(BrokerImportError)`, sibling of
`BrokerParseError`. Re-export it from `src/brokers/__init__.py` alongside the existing three
(`src/brokers/__init__.py:9`, `:32-39`).

#### 2. Manifest pre-check and bounded iteration

**File**: `src/brokers/xlsx_reader.py`

**Intent**: Reject a workbook whose decompressed total exceeds the ceiling before openpyxl
opens it, and stop materialising sheets in full.

**Contract**: Two module-level constants beside `_MAX_HEADER_SCAN` (`:22`), each with a comment
stating why that number — `_MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024` and
`_MAX_CELLS = 200_000`. `read_sheets` sums `ZipFile(io.BytesIO(data)).infolist()` `file_size`
before `load_workbook` (`:32`) and raises `BrokerFileTooLargeError` above the first;
`zipfile.BadZipFile` must be re-raised as the existing `BrokerParseError` message. `_read_one`
keeps `reset_dimensions()` (`:49-50`) and replaces `list(sheet.iter_rows(values_only=True))`
(`:52`) with a loop that decrements a per-workbook cell budget and raises
`BrokerFileTooLargeError` on exhaustion.

Note `_find_header` and `_missing_columns` (`:72-91`) index `rows[:_MAX_HEADER_SCAN]`, so they
keep working against a list built by the bounded loop — no change needed there.

Message style follows the module: lowercase, no trailing period, offending value interpolated,
Polish without diacritics (`xlsx_reader.py:36`, `:59-61`) — the string is surfaced verbatim as
the HTTP `detail`.

#### 3. Tests

**File**: `tests/test_brokers_xlsx_reader.py`

**Intent**: Lock both layers.

**Contract**: Two tests in the file's existing style — full-sentence behavioural names, no
parametrisation, asserting on message content as at `:59-62`. One drives the manifest ceiling,
one the cell ceiling. `_workbook_bytes` (`:12-33`) gains whatever knob is needed to fabricate an
oversized workbook cheaply; the existing `broken_dimension` hook (`:30`) is the precedent.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_brokers_xlsx_reader.py`
- The pre-existing non-zip test still passes: `uv run pytest tests/test_brokers_xlsx_reader.py -k not_a_workbook`
- The layering test still passes: `uv run pytest tests/test_brokers_xtb.py -k imports_no_data_or_web_layer`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check src tests`

#### Manual Verification:

- **Before writing any code**: build a `sharedStrings.xml` bomb under 5 MB compressed in the
  scratchpad and record peak RSS parsing it with the current reader. This baseline is
  unrecoverable once the ceiling rejects the file, so it must be captured first
- After the change: confirm the same file is rejected and peak RSS stays flat
- Confirm a real XTB export still imports unchanged (thresholds are ~200× its size)

**Implementation Note**: After completing this phase and all automated verification passes,
pause here for manual confirmation before proceeding.

---

## Phase 2: Read only the sheets the parser consumes

### Overview

`read_sheets` materialises every worksheet; the XTB parser indexes exactly one. Narrowing it
removes honest-path waste and makes a bomb hidden in an unused sheet irrelevant.

### Changes Required:

#### 1. Narrow the reader

**File**: `src/brokers/xlsx_reader.py`

**Intent**: Stop reading worksheets no caller asks for.

**Contract**: The dict comprehension at `:39` skips any sheet whose title is not a key of
`_REQUIRED_COLUMNS` (`:15`). That dict is already the single source of truth for which sheets
matter and is referenced in exactly one other place (`:53`), so **no signature change, no
caller update, and none of the five existing `read_sheets(...)` call sites in
`tests/test_brokers_xlsx_reader.py` (`:39,50,60,75,82`) are affected.** A sheet that is skipped
is simply absent from the returned dict — the parser's own guard at `src/brokers/xtb.py:280-283`
already produces the right error for that case.

Note this deliberately couples "which sheets we read" to "which sheets we validate". If a
future broker needs a sheet with no required columns, that coupling is what has to be split —
not before.

#### 2. Neutrality test

**File**: `tests/test_brokers_xtb.py`

**Intent**: Prove narrowing changed nothing observable.

**Contract**: A test asserting the parser's output on `_golden_workbook()` (`:340-353`) is
unchanged when the workbook carries an extra unrelated sheet. Follow the section-comment style,
e.g. `# ── size ceiling (PUL-105) ──` (cf. `:454`).

### Success Criteria:

#### Automated Verification:

- All five pre-existing `read_sheets(...)` call sites still pass unmodified
- Broker tests pass: `uv run pytest tests/test_brokers_xtb.py tests/test_brokers_xlsx_reader.py`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check src tests`

#### Manual Verification:

- A real XTB export produces the same positions, dividends and cash balance as before

---

## Phase 3: API layer — status mapping and check ordering

### Overview

Map the new exception to 413, and stop measuring the upload after paying for it. Plus the first
test the existing 413 path has ever had.

### Changes Required:

#### 1. Status mapping

**File**: `src/api.py`

**Intent**: Answer 413 for a size breach rather than letting it collapse into the generic 422.

**Contract**: An `except BrokerFileTooLargeError` clause in `_resolve_import` **before** the
existing `except BrokerImportError` at `:399-401`, raising
`HTTPException(status_code=413, detail=str(exc))`. Ordering is load-bearing — see Critical
Implementation Details.

#### 2. Check the size before reading the body

**File**: `src/api.py`

**Intent**: Move the 5 MB gate ahead of the copy it is supposed to prevent.

**Contract**: Both endpoints (`:1359` preview, `:1371` commit) consult `UploadFile.size` — which
Starlette populates during spooling — against `_IMPORT_MAX_BYTES` before `await file.read()`,
raising the same 413 with the same Polish message as `:387`. Keep the existing `len(data)`
check in `_resolve_import` as the backstop, since `size` is not guaranteed non-`None`.

#### 3. Regression test for the pre-existing 413

**File**: `tests/test_api.py`

**Intent**: Put a net under the constant this phase moves.

**Contract**: One test in the `# ── Broker import (PUL-95)` section (`:2096`) posting an
oversized upload via `client.post(url, data=_import_form(), files=...)` and asserting 413. No
BigQuery patches are needed — the guard fires before the first BQ call at `src/api.py:390` —
but state that in the test's docstring, following the file's convention of naming the risk
(`:2153`).

#### 4. Stop telling the user the wrong thing

**File**: `static/index.html`

**Intent**: A rejected oversized file currently reports a broker mismatch. Say what actually
happened.

**Contract**: The three import error handlers — `:4242` (preview), `:4263` (commit) and `:4353`
(`_importIntoNewWallet`) — branch on `r.status === 413` and pass a size-specific Polish message
to `_ppImportError` (`:4157`) instead of the current catch-all
`'Nie udało się odczytać pliku. Sprawdź, czy to eksport z wybranego domu maklerskiego.'`. This
also fixes the message for the pre-existing 5 MB path, which has been misreporting since PUL-95.

### Success Criteria:

#### Automated Verification:

- API tests pass: `uv run pytest tests/test_api.py -k import`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check src tests`

#### Manual Verification:

- An oversized upload returns 413, not 422 — the `except` ordering trap, confirmed against the
  running app rather than only in tests
- The UI reports a size problem, not a broker mismatch, for an oversized file
- A normal import through the UI still previews and commits correctly

---

## Phase 4: Bound the aggregate, not just the request

### Overview

Phases 1–3 cap one request at roughly 50 MiB. They do not cap how many such requests share an
instance. This phase closes the half the ticket does not mention.

### Changes Required:

#### 1. Declare concurrency

**File**: `.github/workflows/deploy.yml`

**Intent**: The service runs at Cloud Run's default of 80 concurrent requests per instance
because no `--concurrency` flag was ever passed — a default, not a decision. At `--memory=512Mi`
that leaves the per-request ceiling from Phase 1 unable to protect the instance.

**Contract**: Add `--concurrency=8` to the `gcloud run deploy puls-gpw-api` step (`:81-97`),
alongside the existing `--cpu`/`--memory`/`--max-instances` flags, with a comment naming the
arithmetic: 8 × ~50 MiB worst case ≈ 400 MiB, inside 512 MiB with room for baseline. Combined
with `--max-instances=2` this gives 16 concurrent requests of total capacity — far above this
application's real traffic. Only the `api` service changes; the four `gcloud run jobs update`
steps (`:47-79`) are untouched.

### Success Criteria:

#### Automated Verification:

- Workflow file parses: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml'))"`

#### Manual Verification:

- After merge, `gcloud run services describe puls-gpw-api --project puls-gpw --region <region>`
  reports the new concurrency
- `/health` responds and normal browsing (announcements, portfolio) is not throttled

---

## Testing Strategy

### Unit Tests:

- Manifest ceiling: a workbook whose decompressed total exceeds `_MAX_UNCOMPRESSED_BYTES` raises
  `BrokerFileTooLargeError` naming the limit
- Cell ceiling: a workbook under the byte ceiling but over `_MAX_CELLS` raises the same
- Non-zip input still raises `BrokerParseError` with the existing message (regression on the
  reordering)
- `reset_dimensions()` behaviour preserved — the existing 1×1-dimension test must stay green
- Narrowing is observationally neutral for the XTB parser

### Integration Tests:

- Oversized upload → 413 through `TestClient` (also the first coverage of the pre-existing
  `_IMPORT_MAX_BYTES` path)

### Manual Testing Steps:

1. In the scratchpad, build an `.xlsx` under 5 MB compressed whose payload is a large
   `sharedStrings.xml`; record uncompressed total and compression ratio.
2. Measure peak RSS parsing it with the pre-change reader, then with the post-change reader.
3. Import a real XTB export through the UI; confirm positions, dividends and cash are unchanged.
4. Upload an oversized file through the UI; confirm the response is 413.

## Performance Considerations

The manifest sum is O(number of zip entries) on the central directory — negligible against the
existing per-request cost. The bounded loop replaces `list(...)` with an explicit loop over the
same generator; for real files (573 rows) the difference is unmeasurable. Narrowing sheet
reading strictly reduces work. Note the file is uploaded and parsed **twice** per import
(preview then commit, `src/api.py:374-377`), so every saving here counts double.

## Migration Notes

No schema, no stored data. The change is deployable one commit per phase and revertable by
reverting those commits. Deploy is by merge to master (`.github/workflows/deploy.yml`); verify
via `/health`.

One intermediate state is worth naming so nobody reads it as a regression: **between Phase 1
and Phase 3 an oversized upload is rejected with 422, not 413.** `BrokerFileTooLargeError`
subclasses `BrokerImportError`, so until Phase 3 adds its own `except` clause it falls through
to the generic handler at `src/api.py:399-401`. The file is still refused and the message is
still correct — only the status differs.

Phase 4 changes production serving behaviour (`--concurrency`), so it takes effect on merge
like any other deploy. Reverting that commit restores Cloud Run's default.

## References

- Research: `context/changes/xlsx-import-size-cap/research.md`
- Origin (F2, DEFERRED): `context/archive/2026-07-29-xtb-portfolio-import/reviews/impl-review.md:55-69`
- Original stance on size limits: `context/archive/2026-07-29-xtb-portfolio-import/plan.md:480-481`, `:181`
- Nearest constant precedent: `src/brokers/xlsx_reader.py:22`
- Nearest abort-while-iterating precedent: `src/parser.py:84`, `:152`
- Layering constraint: `tests/test_brokers_xtb.py:406-428`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Ceiling in the reader

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_brokers_xlsx_reader.py` — 57cd653
- [x] 1.2 The pre-existing non-zip test still passes — 57cd653
- [x] 1.3 The layering test still passes — 57cd653
- [x] 1.4 Full suite passes: `uv run pytest` — 57cd653
- [x] 1.5 Linting passes: `uv run ruff check src tests` — 57cd653

#### Manual

- [x] 1.6 Baseline peak RSS recorded against a `sharedStrings.xml` bomb BEFORE writing code — 57cd653
- [x] 1.7 After the change the same file is rejected and peak RSS stays flat — 57cd653
- [x] 1.8 A real XTB export still imports unchanged — 57cd653

### Phase 2: Read only the sheets the parser consumes

#### Automated

- [x] 2.1 All five pre-existing `read_sheets(...)` call sites still pass unmodified — aa64fd2
- [x] 2.2 Broker tests pass — aa64fd2
- [x] 2.3 Full suite passes: `uv run pytest` — aa64fd2
- [x] 2.4 Linting passes: `uv run ruff check src tests` — aa64fd2

#### Manual

- [x] 2.5 A real XTB export produces the same positions, dividends and cash balance — aa64fd2

### Phase 3: API layer — status mapping and check ordering

#### Automated

- [x] 3.1 API tests pass: `uv run pytest tests/test_api.py -k import` — a1cdb76
- [x] 3.2 Full suite passes: `uv run pytest` — a1cdb76
- [x] 3.3 Linting passes: `uv run ruff check src tests` — a1cdb76

#### Manual

- [x] 3.4 An oversized upload returns 413, not 422, against the running app — a1cdb76
- [x] 3.5 The UI reports a size problem, not a broker mismatch, for an oversized file — a1cdb76
- [x] 3.6 A normal import through the UI still previews and commits correctly — a1cdb76

### Phase 4: Bound the aggregate, not just the request

#### Automated

- [x] 4.1 Workflow file parses

#### Manual

- [ ] 4.2 `gcloud run services describe puls-gpw-api` reports the new concurrency after merge
- [ ] 4.3 `/health` responds and normal browsing is not throttled
