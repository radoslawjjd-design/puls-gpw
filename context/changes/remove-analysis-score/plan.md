# Remove analysis_score from user-facing /announcements response Implementation Plan

## Overview

`GET /announcements` leaks the internal `analysis_score` field to non-admin
(user-role) API keys via the `AnnouncementUser` model and the
`list_announcements_user` BigQuery query. This plan removes it from both, and
adds regression tests that lock the user-facing response shape and confirm
the admin response stays unaffected (PUL-42 / GitHub #60).

## Current State Analysis

- `AnnouncementUser` (`src/api.py:89-96`) declares
  `analysis_score: float | None = None` alongside `company`, `ticker`,
  `event_type`, `structured_analysis`, `published_at`.
- `list_announcements_user` (`db/bigquery.py:621-672`) selects `analysis_score`
  in its `SELECT` clause (`:644-645`) and includes it in the returned dict
  (`:668`).
- `AnnouncementAdmin` (`src/api.py:67-86`) is a structurally separate model
  that also declares `analysis_score` (`:86`) — untouched by this change.
- `list_announcements_user` has exactly one caller: the `role != "admin"`
  branch of `GET /announcements` (`src/api.py:143-152`) — confirmed via
  project-wide grep, so removing the column has no other internal
  consumer to break.
- `AnnouncementUser.model_config = ConfigDict(extra="ignore")` — removing the
  field from the model is sufficient on its own to drop it from the response;
  the BQ-layer removal is a defense-in-depth cleanup (don't fetch/transmit
  data that's discarded downstream).

## Desired End State

`GET /announcements` with a user API key never includes `analysis_score` in
its response, regardless of what the BQ row contains. `GET /announcements`
with an admin API key is unaffected — `analysis_score` is still present and
correctly populated.

Verified by: `uv run pytest tests/test_api.py tests/test_bigquery.py -v` and
the full suite (`uv run pytest`).

### Key Discoveries:

- `src/api.py:89-96` and `db/bigquery.py:644-645,668` — the two leak points,
  already pinpointed and verified in `context/changes/remove-analysis-score/frame.md`.
- `tests/test_api.py:89-99` (`test_announcements_user_returns_subset_fields`)
  and `tests/test_api.py:48-63` (`test_announcements_admin_returns_list`)
  already mock the relevant data shape — extend rather than duplicate.
- `tests/test_bigquery.py:521-527` (`test_list_announcements_user_only_approved`)
  already exercises `list_announcements_user`'s query string and returned
  rows — extend the same way.

## What We're NOT Doing

- Not touching `AnnouncementAdmin` or `list_announcements_admin` — admin
  response must stay exactly as-is.
- Not restricting nested fields inside `structured_analysis` (e.g.
  `sentiment`) — explicitly out of scope per the ticket, to be decided
  separately once table/response alignment is confirmed project-wide.
- Not changing `static/index.html` — its role-gated rendering (`role ===
  'admin'` checks at lines 573, 635, 639, 647) already hides the score from
  users visually; removing the field from the API response also closes the
  `data-score` DOM-attribute leak (`:569`) as a side effect, with no
  frontend code change required.
- Not removing the `analysis_score` BQ table column/schema field
  (`db/bigquery.py:62`) — still written by `save_analysis_result` and read
  by the admin path; this change only touches the user-facing `SELECT`.

## Implementation Approach

Single atomic change across the two leak points plus tests, landed together:
remove the field from the Pydantic model (the authoritative boundary, since
`extra="ignore"` already makes this sufficient on its own), remove it from
the BQ query as defense-in-depth, and add an allowlist-style test on the
user response so that any *future* field added to `AnnouncementUser` without
deliberate intent fails the same test — not just this one field.

## Phase 1: Remove analysis_score from model + query, with regression tests

### Overview

Remove the field from both leak points and harden the test suite so a
future accidental field addition to the user-facing model is caught
automatically, not just this specific regression.

### Changes Required:

#### 1. Drop `analysis_score` from `AnnouncementUser`

**File**: `src/api.py`

**Intent**: Stop declaring `analysis_score` on the user-facing response
model — this is the authoritative fix; `extra="ignore"` means BQ can still
hand the model a dict containing the key and it will be dropped safely.

**Contract**: Delete line 95 (`analysis_score: float | None = None`) from
`AnnouncementUser`. The model's remaining fields (`company`, `ticker`,
`event_type`, `structured_analysis`, `published_at`) are unchanged.

#### 2. Drop `analysis_score` from `list_announcements_user`

**File**: `db/bigquery.py`

**Intent**: Stop selecting and returning a column that's discarded
downstream — defense-in-depth so the field never leaves BigQuery for a
user-role request in the first place.

**Contract**: Remove `analysis_score` from the `SELECT` clause (`:644-645`,
currently `company, ticker, event_type, structured_analysis, analysis_score,
published_at`) and remove the `"analysis_score": row.analysis_score,` entry
from the returned dict (`:668`). `list_announcements_admin` and its `SELECT`
(`:577` area) are untouched.

#### 3. Allowlist regression test on the user response

**File**: `tests/test_api.py`

**Intent**: Replace the narrow "absent fields" check with a full key-set
allowlist, so any field added to `AnnouncementUser` later without deliberate
intent breaks this test — not just a re-introduction of `analysis_score`
specifically.

**Contract**: Extend `test_announcements_user_returns_subset_fields`
(`:89-99`) with `assert set(data[0].keys()) == {"company", "ticker",
"event_type", "structured_analysis", "published_at"}`, replacing the current
narrower `"announcement_id" not in data[0]` / `"url" not in data[0]` /
`"ticker" in data[0]` assertions (the allowlist subsumes all three).

#### 4. Confirm admin response is unaffected

**File**: `tests/test_api.py`

**Intent**: Make the "admin unaffected" acceptance criterion an explicit,
checked fact rather than an assumption from the model being untouched.

**Contract**: Extend `test_announcements_admin_returns_list` (`:48-63`) with
`assert data[0]["analysis_score"] == 0.9`, matching the `analysis_score: 0.9`
already present in that test's `mock_rows`.

#### 5. BQ-layer regression test

**File**: `tests/test_bigquery.py`

**Intent**: Lock the `SELECT` clause and returned-dict shape of
`list_announcements_user` at the BQ layer too, independent of the API-layer
test.

**Contract**: Extend `test_list_announcements_user_only_approved`
(`:521-527`) with `assert "analysis_score" not in query_str` and
`assert "analysis_score" not in rows[0]`.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_api.py -v` passes
- `uv run pytest tests/test_bigquery.py -v` passes
- Full suite passes: `uv run pytest`

#### Manual Verification:

- `curl -H "X-API-Key: $USER_API_KEY" https://<host>/announcements` — response
  JSON contains no `analysis_score` key in any item
- `curl -H "X-API-Key: $ADMIN_API_KEY" https://<host>/announcements` —
  response JSON still contains `analysis_score` in each item
- Real-BigQuery round-trip (`scripts/test_bq.py` or an ad-hoc call to
  `list_announcements_user(...)` against a real client): the modified
  `SELECT` executes without a syntax error and returns rows without
  `analysis_score`. Per `context/foundation/lessons.md` ("BigQuery —
  kolumny o nazwach reserved keywords..."), mocked BQ tests never send SQL
  to a real parser — any edit to hand-built SQL in `db/bigquery.py` needs
  this manual round-trip before it's considered verified.

---

## Testing Strategy

### Unit Tests:

- `tests/test_bigquery.py::test_list_announcements_user_only_approved` —
  extended to assert `analysis_score` absent from both the query string and
  the returned dict shape.

### Integration Tests:

- `tests/test_api.py::test_announcements_user_returns_subset_fields` —
  extended to an exact key-set allowlist on the user response.
- `tests/test_api.py::test_announcements_admin_returns_list` — extended to
  assert `analysis_score` is present and correctly valued for admin.

### Manual Testing Steps:

1. Deployed/local app, `GET /announcements` with a user API key — inspect
   raw JSON, confirm no `analysis_score` key.
2. Same request with an admin API key — confirm `analysis_score` is present
   and numeric.

## Performance Considerations

None — removing one column from a `SELECT` and one field from a Pydantic
model has no measurable performance impact.

## Migration Notes

None — API response shape change only, no data migration; existing BQ rows
and the `analysis_score` column are untouched.

## References

- Frame brief: `context/changes/remove-analysis-score/frame.md`
- `src/api.py:67-155` (both models + `/announcements` endpoint)
- `db/bigquery.py:621-672` (`list_announcements_user`)
- `tests/test_api.py:48-99`, `tests/test_bigquery.py:521-527`
- Tracking: Linear PUL-42, GitHub #60

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Remove analysis_score from model + query, with regression tests

#### Automated

- [x] 1.1 `uv run pytest tests/test_api.py -v` passes
- [x] 1.2 `uv run pytest tests/test_bigquery.py -v` passes
- [x] 1.3 Full suite passes: `uv run pytest`

#### Manual

- [x] 1.4 `curl` with user API key — no `analysis_score` key in response
- [x] 1.5 `curl` with admin API key — `analysis_score` still present
- [x] 1.6 Real-BigQuery round-trip (`scripts/test_bq.py`) — modified `SELECT` executes cleanly, no `analysis_score` in returned rows
