---
change_id: xlsx-import-size-cap
title: Cap the decompressed size of an uploaded broker .xlsx while streaming
status: archived
created: 2026-08-01
updated: 2026-08-03
archived_at: 2026-08-03T18:47:23Z
tracking:
  linear: PUL-105
  github: 213
---

## Notes

PUL-105 / GitHub #213 — Broker import: cap the decompressed size of an uploaded `.xlsx`.

`src/brokers/xlsx_reader.py:39,52` — `read_sheets` materialises every sheet in full
(`list(sheet.iter_rows(values_only=True))`), and the only size guard, `_IMPORT_MAX_BYTES` = 5 MB
in `src/api.py:370`, measures the **compressed** upload and runs only after `await file.read()`
has already buffered the whole body. `.xlsx` is a zip archive, so a few MB of highly repetitive
cells expand to a multiple of that in memory and can take down the Cloud Run instance for every
user, not just the uploader. The endpoint requires authentication, so this is not an anonymous
vector — warning, not critical.

**Fix direction:** enforce a hard row/cell ceiling *while iterating*, aborting once exceeded,
instead of bounding the input size after the fact. `read_only=True` already gives a streaming
iterator; the `list(...)` call is what discards that property.

**Constraint:** `_read_one` calls `sheet.reset_dimensions()` deliberately — XTB declares a bogus
`A1:A1` dimension and without the reset the import silently returns one row. Any streaming
rewrite has to keep that.

Found in the PUL-95 implementation review (F2).
