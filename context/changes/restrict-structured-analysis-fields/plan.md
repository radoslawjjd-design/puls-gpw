# Restrict sentiment inside structured_analysis from user-facing /announcements response Implementation Plan

## Overview

`GET /announcements` for a `user`-role API key returns `structured_analysis`
containing `sentiment`, an internal field the frontend already treats as
admin-only (`static/index.html:635-638`). This plan strips `sentiment` from
the parsed `structured_analysis` dict in the user-role branch of the endpoint,
mirroring the PUL-42 convention, and adds regression tests locking the
behavior for both roles (PUL-46 / GitHub #64).

## Current State Analysis

- `AnnouncementUser.structured_analysis` (`src/api.py:94`) is typed `dict |
  None`. `model_config = ConfigDict(extra="ignore")` (`:90`) only filters
  **top-level** model fields — it does not prune keys inside a nested
  `dict`-typed field. So removing a top-level field (PUL-42's pattern) isn't
  sufficient here; `sentiment` must be removed from the dict itself before
  the model is constructed.
- `_parse_structured_analysis` (`src/api.py:58-64`) parses the raw BQ string
  into a `dict | None` via `json5.loads`, returning `None` on parse failure.
  It is called identically in both the admin and user branches
  (`src/api.py:137`, `:148`).
- The user branch of `GET /announcements` (`src/api.py:141-151`) is the only
  place `AnnouncementUser` is constructed — the only place to apply the
  strip without touching the admin path.
- `_AnalysisResponse` (`src/analyzer.py:43-49`) is a closed schema: exactly
  `event_type`, `sentiment`, `key_numbers`, `summary_pl`, enforced by
  `extra="ignore"` at parse time in `_call_analysis` (`src/analyzer.py:170`).
  One writer (`db/bigquery.py:58` column, `save_analysis_result`). No other
  field will ever appear here — confirmed in `frame.md`.
- No internal pipeline code (`main.py`, `post_generator.py`,
  `post_selection.py`) reads `sentiment` via the HTTP API — they read
  BigQuery directly. Stripping it from the API response breaks nothing
  internal (confirmed in `frame.md`).
- `static/index.html:570` embeds the full `structured_analysis` blob
  (including `sentiment`) into a `data-structured-analysis` DOM attribute
  unconditionally. This is a second leak surface that closes automatically
  once the API stops sending `sentiment` to user-role responses — no
  frontend change needed (same shape as PUL-42's `data-score` side effect).

## Desired End State

`GET /announcements` with a user API key never includes `sentiment` inside
`structured_analysis`, regardless of what the BQ row contains. `GET
/announcements` with an admin API key is unaffected — `sentiment` is still
present inside `structured_analysis` when the underlying analysis produced
one.

Verified by: `uv run pytest tests/test_api.py -v` and the full suite (`uv run
pytest`).

### Key Discoveries:

- `src/api.py:89-96` (`AnnouncementUser`), `:141-151` (user branch) — the
  single point to change.
- `tests/test_api.py:67-76`
  (`test_announcements_user_parses_structured_analysis`) already mocks a
  `structured_analysis` JSON string for the user role — extend rather than
  duplicate.
- `tests/test_api.py:48-64` (`test_announcements_admin_returns_list`) already
  mocks `structured_analysis` for the admin role — add a `sentiment` key to
  its existing mock and assert it survives.

## What We're NOT Doing

- Not designing a general-purpose allowlist mechanism for
  `structured_analysis` — the schema is closed (4 fields, one writer,
  `extra="ignore"` enforced at parse time), confirmed in `frame.md`. Only
  `sentiment` is stripped.
- Not touching `AnnouncementAdmin`, `list_announcements_admin`, or the admin
  branch of `/announcements` — admin response must stay exactly as-is.
- Not changing the BQ query (`list_announcements_user`) or schema — the
  column is a single JSON string fetched once for both roles; there is no
  column to drop, only a key to drop after parsing.
- Not changing `static/index.html` — its existing `role === 'admin'` gate
  around `sentiment` rendering (`:635-638`) already hides it visually for
  users; stripping it from the API response also closes the
  `data-structured-analysis` DOM-attribute leak (`:570`) as a side effect,
  with no frontend code change required.

## Implementation Approach

Single inline change at the one construction point for `AnnouncementUser`,
plus tests extending the existing mocks rather than adding new fixtures.
Pop `sentiment` from the parsed dict (if present) immediately after
`_parse_structured_analysis(...)` returns, before passing the result into
`AnnouncementUser(...)`. No new function — a one-field `.pop()` doesn't
warrant an abstraction, consistent with the frame's explicit scoping.

## Phase 1: Strip sentiment from user-role structured_analysis, with regression tests

### Overview

Strip `sentiment` from the parsed `structured_analysis` dict in the
user-role branch only, and lock both the stripped (user) and unstripped
(admin) behavior with tests.

### Changes Required:

#### 1. Strip `sentiment` in the user branch of `/announcements`

**File**: `src/api.py`

**Intent**: Remove the internal `sentiment` field from `structured_analysis`
before it reaches a user-role response, while leaving the admin branch and
`_parse_structured_analysis` itself untouched.

**Contract**: In the user branch's list comprehension (`:146-151`), after
calling `_parse_structured_analysis(r.get("structured_analysis"))`, pop the
`"sentiment"` key from the resulting dict if it is not `None`, before passing
it into `AnnouncementUser(...)`. The admin branch (`:135-140`) and
`_parse_structured_analysis` (`:58-64`) are unchanged.

#### 2. Extend user-role test to assert `sentiment` is stripped

**File**: `tests/test_api.py`

**Intent**: Lock the new stripping behavior so a future regression
(e.g. someone reordering the dict construction) is caught.

**Contract**: Extend
`test_announcements_user_parses_structured_analysis` (`:67-76`) — change the
mock's `structured_analysis` JSON string to include `"sentiment":
"pozytywny"` alongside `"summary_pl": "test"`, and add `assert "sentiment"
not in data[0]["structured_analysis"]`.

#### 3. Extend admin-role test to assert `sentiment` survives

**File**: `tests/test_api.py`

**Intent**: Make the "admin unaffected" criterion an explicit, checked fact.

**Contract**: Extend `test_announcements_admin_returns_list` (`:48-64`) —
change the mock's `structured_analysis` JSON string to include `"sentiment":
"pozytywny"` alongside `"summary_pl": "test"`, and add `assert
data[0]["structured_analysis"]["sentiment"] == "pozytywny"`.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_api.py -v` passes
- Full suite passes: `uv run pytest`

#### Manual Verification:

- `curl -H "X-API-Key: $USER_API_KEY" https://<host>/announcements` —
  response JSON's `structured_analysis` objects contain no `sentiment` key
- `curl -H "X-API-Key: $ADMIN_API_KEY" https://<host>/announcements` —
  response JSON's `structured_analysis` objects still contain `sentiment`
  when the underlying analysis produced one

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that
the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- N/A — no BQ-layer query change, so no `test_bigquery.py` changes needed
  (unlike PUL-42, there is no column to drop from the `SELECT`).

### Integration Tests:

- `tests/test_api.py::test_announcements_user_parses_structured_analysis` —
  extended to assert `sentiment` is absent from the user-role response.
- `tests/test_api.py::test_announcements_admin_returns_list` — extended to
  assert `sentiment` is present and correctly valued for the admin-role
  response.

### Manual Testing Steps:

1. Deployed/local app, `GET /announcements` with a user API key — inspect
   raw JSON, confirm no `sentiment` key inside any item's
   `structured_analysis`.
2. Same request with an admin API key — confirm `sentiment` is present
   inside `structured_analysis` when the underlying analysis produced one.

## Performance Considerations

None — popping one key from an already-parsed dict has no measurable
performance impact.

## Migration Notes

None — API response shape change only, no data migration; existing BQ rows
and the `structured_analysis` column are untouched.

## References

- Frame brief: `context/changes/restrict-structured-analysis-fields/frame.md`
- `src/api.py:58-65,89-96,141-151` (parse helper, `AnnouncementUser`,
  `/announcements` endpoint)
- `src/analyzer.py:43-53,170,249` (closed `_AnalysisResponse` schema)
- `static/index.html:570,635-638` (existing admin-only render gate, DOM leak
  that closes as a side effect)
- `tests/test_api.py:48-64,67-76` (tests to extend)
- Prior convention: `context/archive/2026-06-19-remove-analysis-score/plan.md`
- Tracking: Linear PUL-46, GitHub #64

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Strip sentiment from user-role structured_analysis, with regression tests

#### Automated

- [x] 1.1 `uv run pytest tests/test_api.py -v` passes
- [x] 1.2 Full suite passes: `uv run pytest`

#### Manual

- [x] 1.3 `curl` with user API key — no `sentiment` key inside `structured_analysis`
- [x] 1.4 `curl` with admin API key — `sentiment` still present inside `structured_analysis`
