---
date: 2026-08-01T00:00:00+02:00
researcher: Radek
git_commit: 65da33b1ef5049feedf9ca5d02b9bd0de290e8f4
branch: master
repository: puls-gpw
topic: "Cap the decompressed size of an uploaded broker .xlsx (PUL-105 / GH #213)"
tags: [research, codebase, brokers, xlsx, openpyxl, upload, availability]
status: complete
last_updated: 2026-08-01
last_updated_by: Radek
---

# Research: Cap the decompressed size of an uploaded broker .xlsx

**Date**: 2026-08-01
**Researcher**: Radek
**Git Commit**: `65da33b1ef5049feedf9ca5d02b9bd0de290e8f4`
**Branch**: `master`
**Repository**: puls-gpw

## Research Question

PUL-105 / GH #213: the only size guard on the broker-import upload is 5 MB on the
**compressed** body, while `read_sheets` materialises every worksheet in full. Where must a
ceiling actually live, what does it have to measure, and what constrains the fix?

Scope agreed with the user: **reader + upload path** (`src/brokers/xlsx_reader.py` and the
import endpoints in `src/api.py`), not a repo-wide upload audit.

## Summary

**The ticket's proposed fix is necessary but not sufficient.** It says: enforce a row/cell
ceiling *while iterating*, because `read_only=True` already gives a streaming iterator and the
`list(...)` is what discards that property. Direct reading of the installed openpyxl **3.1.5**
source confirms half of that and refutes the other half:

1. ✅ `reset_dimensions()` does **not** break streaming. It is a two-line attribute reset
   (`_max_row = _max_column = None`), no pre-scan — `worksheet/_read_only.py:164-170`.
2. ✅ `_cells_by_row` is a genuine generator that streams the sheet XML row by row
   (`worksheet/_read_only.py:60-100`), so replacing `list(...)` with a counting loop that
   aborts **does** bound per-sheet row memory.
3. ❌ **But `load_workbook()` eagerly materialises the entire shared-strings table before any
   worksheet handle exists.** `ExcelReader.read()` calls `self.read_strings()`
   unconditionally (`reader/excel.py:288-291`), which reads `sharedStrings.xml` into a Python
   list (`reader/excel.py:139-147`) and hands it to every `ReadOnlyWorksheet`
   (`reader/excel.py:228`). `read_only=True` does not change this.

**Consequence:** a bomb whose payload sits in `sharedStrings.xml` — the natural shape for an
xlsx bomb, since repeated text compresses superbly — is fully resident **before** the first
`iter_rows` call. A counter inside the iteration loop cannot see it, let alone stop it. A
purely in-iteration guard would close the row bomb and leave the string bomb wide open.

**Therefore the fix needs two layers:**

- **Layer 1 — before `load_workbook`:** sum `file_size` over `ZipFile(...).infolist()` and
  reject when the uncompressed total exceeds a ceiling. This is the *only* guard that can
  bound the shared-strings path.
- **Layer 2 — during iteration:** replace `list(sheet.iter_rows(values_only=True))` with a
  bounded loop, which also fixes a second waste the reader has today (below).

**Second finding, free win:** `read_sheets` materialises **every** worksheet
(`src/brokers/xlsx_reader.py:39`) while the XTB parser indexes exactly one —
`sheets["Cash Operations"]` (`src/brokers/xtb.py:285`). Every other sheet is fully read into a
list at `xlsx_reader.py:52` and then discarded by `_read_one`'s `return []` at `:56-57`. Real
exports carry several sheets. Reading only what the parser asks for cuts the attack surface
and the honest-path cost at once.

**Third finding, ordering:** the existing 5 MB check at `src/api.py:386` runs *after* the body
is already resident **three times over** — Starlette spools the file part to
`SpooledTemporaryFile` with no cap on file parts (`starlette/formparsers.py:209`,
`spool_max_size = 1 MiB` at `:126`; the `max_part_size` guard at `:160-167` applies only to
non-file fields), FastAPI parses the form at `fastapi/routing.py:406` **before**
`solve_dependencies` at `:451` (so auth runs after the upload is on disk), and
`await file.read()` (`src/api.py:1359`, `:1371`) then copies the whole spool into one `bytes`.
`len(data) > _IMPORT_MAX_BYTES` measures a cost already paid.

**Blast radius is real, not theoretical.** Cloud Run `puls-gpw-api` runs with `--memory=512Mi`,
`--cpu=1`, `--max-instances=2`, and **no `--concurrency` flag** → the platform default of 80
concurrent requests per instance (`.github/workflows/deploy.yml:81-97`). One OOM kill takes
out up to 80 users' requests and half the fleet.

## Detailed Findings

### The upload path, end to end

```
POST /api/portfolio/import/{preview|commit}     src/api.py:1346-1361 / :1363-1385
  → fastapi/routing.py:406   await request.form()      ← multipart spooled, file part UNBOUNDED
  → fastapi/routing.py:451+  solve_dependencies         ← auth (_get_user_id) runs HERE, after
  → src/api.py:1359 / :1371  await file.read()          ← whole body → one bytes object
  → src/api.py:371           _resolve_import(...)
  → src/api.py:386           len(data) > _IMPORT_MAX_BYTES → 413   ← the cost is already paid
  → src/api.py:398           get_parser(broker)(data)
  → src/brokers/xtb.py:279   read_sheets(data)
  → src/brokers/xlsx_reader.py:32  load_workbook(BytesIO(data), read_only=True, data_only=True)
                                   ↑ shared strings fully materialised HERE
  → src/brokers/xlsx_reader.py:39  {sheet.title: _read_one(sheet) for sheet in worksheets}
  → src/brokers/xlsx_reader.py:52  rows = list(sheet.iter_rows(values_only=True))
```

Exactly two upload surfaces exist in the whole repo, both in `src/api.py`
(`:1347` preview, `:1364` commit); no other endpoint reads a raw body. Three frontend callers:
`static/index.html:4238` (preview), `:4259` (commit), `:4349` (`_importIntoNewWallet`, which
skips preview entirely). No client-side size check — `static/index.html:4635`, `:4652` are
plain `<input type="file" accept=".xlsx">`.

The file is uploaded and parsed **twice** by design — `src/api.py:374-377` explains that a
preview token cached in one process would be unknown to the other Cloud Run instance. So any
per-request cost is paid twice per import.

### openpyxl 3.1.5 — what actually streams (site-packages, verified directly)

| Claim | Verdict | Evidence |
|---|---|---|
| `reset_dimensions()` forces a full pre-scan / breaks streaming | **False** | `worksheet/_read_only.py:164-170` — sets `_max_row = _max_column = None` and returns |
| `iter_rows` is lazy in read-only mode | **True** | `worksheet/_read_only.py:60-100` — `_cells_by_row` opens the sheet XML via `self._get_source()` (`:55-57`, `archive.open`) and `yield`s per row |
| `list(...)` at `xlsx_reader.py:52` is what destroys the streaming property | **True** | direct consequence of the above |
| Shared strings are lazy | **False — this is the critical one** | `reader/excel.py:288-291` calls `read_strings()` unconditionally inside `read()`; `:139-147` reads `sharedStrings.xml` into a list; `:228` passes it to every `ReadOnlyWorksheet`; `:167`/`:227` show `read_only` gates only the worksheet class, not the strings |

Note the interaction: after `reset_dimensions()`, `max_row` is `None`, so the `if max_row is not
None and idx > max_row: break` guard at `worksheet/_read_only.py:85-86` never fires — the
generator runs to EOF. Lazily, but without any bound. That is precisely the hole a counting
loop closes.

### Existing defensive-limit conventions (the fix must look like these)

House style: module-level private `_UPPER_SNAKE` constant declared next to its enforcement
point, with a comment stating *why that number*.

| Constant | Value | Where | On breach |
|---|---|---|---|
| `_IMPORT_MAX_BYTES` | 5 MiB | `src/api.py:368`, enforced `:386` | `HTTPException(413, "Plik przekracza 5 MB")` — the **only** 413 in the codebase |
| `_MAX_HEADER_SCAN` | 25 | `src/brokers/xlsx_reader.py:22`, used `:76`, `:86` | slice; header not found → `BrokerParseError` — **nearest in-module precedent** |
| `_MAX_CHARS` | 15000 | `src/parser.py:22`, early-exit `if len(all_text) >= _MAX_CHARS: break` at `:84`, `:152` | truncation — **the "abort while iterating" precedent F2 asks for** |
| `_WL_SENTIMENT_LIST_CAP` | 200 | `src/api.py:153`, enforced `:939` | silent truncation + `truncated: true` in the envelope |
| `_CALENDAR_MAX_YEARS_BACK` | 20 | `src/api.py:1264` | 422 with the bound **interpolated into the message** (`:1266-1267`) |
| `RateLimiter` sweep | 1000 | `src/auth.py:221`, `:234` | 429 — the repo's only other explicitly *availability*-motivated guard |

### The layering constraint (hard)

`tests/test_brokers_xtb.py:406-428` (`test_parser_package_imports_no_data_or_web_layer`) walks
the AST of every `src/brokers/*.py` and **fails on any import of `db`, `fastapi`, `google`,
`starlette`**. So a ceiling enforced inside the reader cannot raise `HTTPException` — it must
raise a broker exception.

But `src/api.py:399-401` catches the whole `BrokerImportError` hierarchy
(`src/brokers/errors.py:8, 12, 16`) and collapses it to **422**, while the existing size guard
answers **413**. Two statuses for one class of failure is a pattern-consistency risk that
mirrors already-accepted finding F8 from the PUL-95 review. **This is a decision the plan must
make explicitly** — either a new sibling exception plus an `except` clause before `:399`, or
accept 422 for the reader-layer breach.

Message-format convention for broker errors: lowercase, no trailing period, always interpolate
the offending value; Polish without diacritics, because `str(exc)` is surfaced verbatim as the
HTTP `detail`. E.g. `"nie udalo sie odczytac pliku xlsx: {exc}"` (`xlsx_reader.py:36`),
`"arkusz '{title}' nie ma oczekiwanych kolumn: ..."` (`:59-61`).

**Caveat that lowers the stakes on message wording:** the frontend discards `detail` on both
import calls and renders a fixed Polish string for every non-OK status
(`static/index.html:4242`, `:4263`, `:4353-4357`). A 413 body is never shown to the user today.

### Test conventions and the current coverage hole

- Naming: full-sentence behavioural names, no `test_<fn>_<case>` shape —
  `test_all_rows_are_read_even_when_the_file_declares_a_1x1_dimension`
  (`tests/test_brokers_xlsx_reader.py:65`).
- Fixture builder is a keyword-only factory, not a pytest fixture:
  `_workbook_bytes(*, preamble_rows=4, header=None, data=None, broken_dimension=False) -> bytes`
  (`tests/test_brokers_xlsx_reader.py:12-33`). The `broken_dimension` knob monkeypatches
  `ws.calculate_dimension = lambda **kwargs: "A1:A1"` (`:30`) — the mechanism a size-cap test
  can reuse to fake a large sheet cheaply.
- **Zero parametrisation** in either broker test file. Error assertions check the message
  content, not just the type (`:59-62`).
- Endpoint tests: `tests/test_api.py:2096-2241`, posting via
  `client.post(url, data=_import_form(), files=_import_files())`, mocking at the **import site
  in `src.api`** (`patch("src.api.list_user_portfolios", ...)` etc., six stacked at
  `:2154-2161`) — never `db.bigquery` directly.
- **`_IMPORT_MAX_BYTES` / the 413 path has no test at all.** Grep over `tests/` for
  `413|MAX_BYTES|przekracza` returns nothing. There is no regression net under the constant
  being changed. No test anywhere asserts on memory or size.
- `test-plan.md:40` already carries the shape: `test_max_pages_safeguard | "Hard page cap
  prevents runaway fetching"`.

### Deployment reality

`.github/workflows/deploy.yml:81-97`: `--memory=512Mi`, `--cpu=1`, `--min-instances=0`,
`--max-instances=2`, `--timeout=60`, **no `--concurrency`** → default 80 per instance.
`context/foundation/infra.md:116` says `--memory=1Gi`, but that describes a Cloud Run **Job**,
not this service — treat `deploy.yml` as ground truth.

Realistic honest-path scale, from the PUL-95 change notes: real exports are **15–41 KB**, with
`Cash Operations` at 467 / 106 rows and `Closed Positions` at 72 / 26 rows — **573 rows total**
across both real files. Any ceiling in the tens of thousands of rows is three orders of
magnitude above real traffic.

## Code References

- `src/brokers/xlsx_reader.py:32` — `load_workbook(BytesIO(data), read_only=True, data_only=True)`; shared strings materialise here
- `src/brokers/xlsx_reader.py:39` — reads **every** worksheet; only one is ever consumed
- `src/brokers/xlsx_reader.py:49-50` — `reset_dimensions()`, load-bearing (XTB declares `A1:A1`), locked by `tests/test_brokers_xlsx_reader.py:65-77`
- `src/brokers/xlsx_reader.py:52` — `list(sheet.iter_rows(values_only=True))`, the materialisation
- `src/brokers/xtb.py:285` — `rows = sheets["Cash Operations"]`, the only key ever indexed
- `src/api.py:366-368` — `_IMPORT_MAX_BYTES` and the comment already conceding the gap
- `src/api.py:386-387` — the check, and the codebase's only 413
- `src/api.py:399-401` — whole `BrokerImportError` hierarchy → 422
- `src/api.py:1359`, `:1371` — `await file.read()`, before the check
- `tests/test_brokers_xtb.py:406-428` — the no-`fastapi`-in-`src/brokers` architectural test
- `.venv/Lib/site-packages/openpyxl/reader/excel.py:288-291` — eager `read_strings()`
- `.venv/Lib/site-packages/openpyxl/worksheet/_read_only.py:60-100` — the streaming generator
- `.venv/Lib/site-packages/openpyxl/worksheet/_read_only.py:164-170` — `reset_dimensions()`

## Architecture Insights

- **The guard is at the wrong layer measuring the wrong quantity after the cost is paid.**
  That is the generalisable shape of F2, and it recurs beyond xlsx: the same "validate after
  buffering" pattern is baked into how FastAPI orders form parsing versus dependencies.
- **`read_only=True` is a per-worksheet property, not a per-workbook one.** Anything the
  reader parses eagerly at open time (shared strings, styles, the manifest) is outside its
  protection. This is the single most important thing the plan must internalise.
- **Layering already forbids the obvious shortcut.** The architectural test means the reader
  cannot speak HTTP, so the status-code decision has to be made deliberately rather than
  falling out of the implementation.
- The parser consumes one sheet; the reader reads all of them. Narrowing that is a correctness-
  neutral, cost-reducing change that happens to shrink the attack surface.

## Historical Context (from prior changes)

- `context/archive/2026-07-29-xtb-portfolio-import/reviews/impl-review.md:55-69` — **F2
  verbatim**, severity WARNING, impact MEDIUM, decision DEFERRED. Its proposed fix is the
  in-iteration ceiling this research partially refutes.
- Same review: F1 (cash never falling to zero) was CRITICAL and fixed in-review; **F3 → GH
  #214** (partial state on mid-sequence failure) and **F4 → GH #215** (XTB cross-check test)
  are the still-open siblings. F8 (404 where twins return 403) was accepted with "unify when
  next working on these endpoints" — this change touches exactly those endpoints.
- `context/archive/2026-07-29-xtb-portfolio-import/plan.md:181` — `read_only=True` was chosen
  **explicitly because of the 512 MiB limit**; `:1024-1025` repeats it. The plan reasoned about
  memory correctly and the implementation then threw the streaming property away with
  `list(...)`. `plan.md:480-481` specified the guard as, in full, "reject above 5 MB".
- `plan.md:78-94` ("What We're NOT Doing") does **not** exclude size hardening — this is a gap,
  not a deliberate deferral.
- `context/foundation/lessons.md:227-230` (mocked-test limits) applies by analogy: a test that
  asserts the cap through a stubbed constant proves nothing about openpyxl's real memory
  behaviour. The automated test should assert the observable contract (exception type +
  message); actual memory behaviour belongs in a manual verification step.
- There is **no** existing lesson on input validation or resource exhaustion. This change is a
  good candidate to produce the first one via `/10x-lesson`.

## Related Research

- `context/archive/2026-07-29-xtb-portfolio-import/research.md` — the original broker-import
  exploration (PUL-95 / GH #186)

## Open Questions

1. **Unmeasured: the actual peak-memory numbers.** The empirical run (build bomb variants under
   5 MB, measure RSS/`tracemalloc` for `load_workbook` alone vs `+list(...)` vs bounded loop)
   was **not executed** — it was interrupted. The source reading above is strong enough to fix
   the *shape* of the solution, but the **threshold values** (uncompressed-bytes ceiling, cell
   ceiling) are currently reasoned from real-file scale (573 rows, 15–41 KB) rather than from
   measurement. Either accept a generous ceiling justified by that ratio, or run the
   measurement during `/10x-plan`.
2. **Status code:** new sibling exception → 413 from the API layer, or reuse `BrokerParseError`
   → 422? Precedent exists both ways. Needs an explicit plan decision.
3. **Does the scope include moving the `_IMPORT_MAX_BYTES` check earlier** (to `file.size` /
   `Content-Length`, before `await file.read()`)? `change.md` does not settle it. Note it can
   only ever be a partial mitigation: Starlette has already spooled the body by then, and
   Cloud Run's undeclared 32 MiB platform cap is the real outer bound.
4. **Is the zip manifest trustworthy?** The `file_size` field comes from the zip central
   directory and is therefore attacker-controlled. Un-verified whether openpyxl's
   `archive.open()` would then exceed a manifest that under-reports. Worth one check in
   planning — if spoofable, layer 1 needs to count decompressed bytes as it reads rather than
   trust the header.
5. **Should `read_sheets` stop reading sheets the parser never asks for?** Strictly a
   scope-widening but small and clearly beneficial; the plan should decide in or out rather
   than let it happen by accident.
