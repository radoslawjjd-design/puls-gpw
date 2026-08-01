# Cap the decompressed size of an uploaded broker .xlsx — Plan Brief

> Full plan: `context/changes/xlsx-import-size-cap/plan.md`
> Research: `context/changes/xlsx-import-size-cap/research.md`

## What & Why

The broker-import endpoints bound an upload at 5 MB of **compressed** bytes and nothing else.
`.xlsx` is a zip archive, so a conforming file can expand to a multiple of that in memory and
OOM a 512 MiB Cloud Run instance that serves up to 80 concurrent requests — taking down every
user on it, not just the uploader. This installs a ceiling on the size that actually matters.

## Starting Point

`read_sheets` calls `list(sheet.iter_rows(values_only=True))` for **every** worksheet
(`src/brokers/xlsx_reader.py:39,52`), while the XTB parser indexes exactly one. The 5 MB gate
(`src/api.py:386`) runs after `await file.read()` has already copied the whole body. Real
exports are 15–41 KB, 573 rows.

## Desired End State

An upload whose decompressed content exceeds the ceiling is rejected with 413 before any large
allocation, verified by measuring RSS against a purpose-built bomb. Honest imports — three
orders of magnitude below the ceiling — are untouched.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Where the guard lives | Two layers: zip-manifest sum before `load_workbook`, cell counter during iteration | openpyxl loads `sharedStrings.xml` eagerly inside `load_workbook` (`reader/excel.py:288-291`), so an in-iteration counter alone — the ticket's proposed fix — cannot see a string bomb | Research |
| Is the manifest trustworthy | Yes | `ZipExtFile.__init__` caps reads at `zinfo.file_size`; an under-reporting manifest truncates rather than expands | Research |
| Exception & status | New `BrokerFileTooLargeError` → 413 via a dedicated `except` | The AST test forbids `fastapi` in `src/brokers/`, and 413 matches the neighbouring size guard | Plan |
| Thresholds | 8 MiB uncompressed, 200 000 cells | ~200× the largest real export; a Python cell tuple far outweighs its XML bytes, and 512 MiB is shared across 80 concurrent requests | Plan |
| Sheet narrowing | In scope, via the existing `_REQUIRED_COLUMNS` | That dict already lists exactly the sheets that matter, so no signature change and no test churn | Plan review |
| Aggregate bound | Declare `--concurrency=8` on the Cloud Run service | Per-request ceilings cannot protect a 512 MiB instance serving Cloud Run's default of 80 | Plan review |
| Error message | Branch on 413 in the three frontend handlers | Today any failure reports a broker mismatch — actively misleading for a size rejection | Plan review |
| Existing 5 MB check | Move to `UploadFile.size` before `await file.read()` | Cheap; today it measures a cost already paid | Plan |
| Bomb measurement | Manual Verification step in Phase 1 | `lessons.md:227-230` — mocked tests do not prove behaviour at the boundary, and here the boundary is memory | Plan |

## Scope

**In scope:** manifest pre-check; bounded iteration; new exception + 413 mapping; sheet
narrowing; moving the existing size check earlier; an honest frontend error message; declaring
Cloud Run concurrency; the first-ever test of the 413 path.

**Out of scope:** how the body reaches the app (Starlette spooling, Cloud Run's undeclared
32 MiB cap); the deliberate double upload; GH #214 and #215; **pre-upload** size checks in the
browser; the accepted 404-vs-403 inconsistency; env-configurable ceilings.

## Architecture / Approach

```
upload → [api.py: file.size vs 5 MiB]  ← moved earlier
       → read_sheets(data)
         → [layer 1: sum(ZipFile.infolist().file_size) vs 8 MiB]   ← before load_workbook
         → load_workbook(read_only=True)                            ← shared strings load here
         → per sheet, only if wanted: reset_dimensions() + bounded loop
           → [layer 2: per-workbook 200k cell budget]
       → BrokerFileTooLargeError → except before BrokerImportError → 413
```

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Ceiling in the reader | Both guard layers + the exception; RSS measured | Opening a `ZipFile` first moves the non-zip failure earlier — the existing `BrokerParseError` test breaks unless `BadZipFile` is translated |
| 2. Sheet narrowing | Reader stops materialising unused worksheets | Signature change ripples to the caller and tests |
| 3. API layer | 413 mapping, earlier size check, honest error message, regression test | `except` ordering — the subclass placed after `BrokerImportError` silently yields 422 with tests still green |
| 4. Aggregate bound | `--concurrency=8` on the Cloud Run service | Changes production serving capacity; too low would throttle normal browsing |

**Prerequisites:** feature branch created before `/10x-implement` (project rule); Phase 1's RSS
baseline must be captured before any code changes.
**Estimated effort:** ~1 session across 4 phases, one commit each.

## Open Risks & Assumptions

- **Thresholds are reasoned, not measured.** The research phase's empirical run was interrupted;
  8 MiB / 200 000 cells derive from real-file scale (41 KB, 573 rows) and the 512 MiB budget.
  Phase 1's manual step is what converts this from assumption to fact — if the measurement
  contradicts it, the constants move before Phase 2.
- **The cell budget must be per workbook, not per sheet**, or a bomb split across twenty sheets
  slips through.
- **The guard remains partial by construction.** Starlette has already spooled the body before
  any application code runs; a fully pre-emptive limit needs middleware or a declared Cloud Run
  request cap, both out of scope.
- **Per-request and per-instance bounds are two halves of one guarantee.** Phases 1–3 cap a
  single request at ~50 MiB; Phase 4 caps how many share an instance. Ship both or the
  availability problem is only half solved.

## Success Criteria (Summary)

- An oversized upload is refused with 413 before a large allocation occurs — confirmed by RSS
  measurement, not only by test assertions.
- A real XTB export imports with identical positions, dividends and cash.
- The 5 MB path finally has a regression test under it.
