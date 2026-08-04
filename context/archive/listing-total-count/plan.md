# X-Total-Count on the listing endpoints — Implementation Plan

## Overview

Three listing endpoints return a page of rows and no way to know how many there
are. Add the count as a response header, computed in the query that already runs.

## Current State Analysis

Four query functions back the three endpoints — `/announcements` branches on role:

| function | `db/bigquery.py` | endpoint |
| -- | -- | -- |
| `list_announcements_admin` | `:2380` area | `/announcements` (admin) |
| `list_announcements_user` | `:2431` | `/announcements` (user) |
| `list_announcements_for_watchlist` | `:2486` | `/announcements/my-wallet` |
| `list_x_posts_admin` | — | `/admin/x-posts` |

All four share the same shape: `_build_filter_clauses` → `WHERE` → `ORDER BY` →
`LIMIT @page_size OFFSET @offset`, returning `list[dict]`.

### Key discoveries

- **`COUNT(*) OVER()` is invisible on an empty page.** The window value travels on
  the rows, so a page past the end returns neither. The count would read 0 while
  the true total is whatever it is — wrong in the one place a paginated UI shows
  it. A fallback `COUNT(*)` query, run only when a page beyond the first comes
  back empty, keeps the normal path to one query.
- **The return type has to change**, and that is the whole cost of this ticket.
  A `NamedTuple` of `(rows, total)` unpacks like a plain tuple, so test mocks can
  return `([...], 7)` without importing anything.
- **No CORS middleware exists.** The expose header is inert same-origin. Sent
  anyway — see `change.md`.
- **The response body must not change.** FastAPI's injected `Response` sets a
  header without touching the returned list, which is what makes this the
  zero-regression change the ticket describes.

## Desired End State

Every one of the three endpoints answers with `X-Total-Count` equal to the number
of rows the filters match, independent of `page` and `page_size`, including on a
page past the end. Response bodies are byte-for-byte what they were.

## What We're NOT Doing

- No response-body change, no envelope, no `{items, total}` shape.
- ~~No pagination UI~~ — added in Phase 3 at the owner's request (2026-08-04),
  overriding the ticket's split. See that phase for what it changed.
- No count on non-paginated endpoints.

---

## Phase 1: The queries return their total

### Changes Required:

#### 1. Listing queries

**File**: `db/bigquery.py`

**Intent**: Each of the four gains `COUNT(*) OVER()` in its SELECT and returns the
rows together with the total. When a page beyond the first comes back empty, a
fallback `COUNT(*)` over the same filters supplies the total the window could not.

**Contract**: a shared `ListingPage(NamedTuple)` with `rows: list[dict]` and
`total: int`; all four functions return it. The window column is stripped from the
row dicts, which are built explicitly, so no caller sees an extra key.

### Success Criteria:

#### Automated Verification:

- Each query's SQL contains `COUNT(*) OVER()`
- The total comes back independent of `page_size`
- A page past the end still reports the true total, not 0
- A genuinely empty result set reports 0 without a second query on page 1
- Full suite green, linting passes

---

## Phase 2: The endpoints send the header

### Changes Required:

#### 1. Three endpoints

**File**: `src/api.py`

**Intent**: Set `X-Total-Count` from the total, and `Access-Control-Expose-Headers`
so it stays readable if the UI ever moves origin. `/announcements` sets it on both
role branches.

**Contract**: each handler takes an injected `response: Response` and sets both
headers before returning the unchanged list.

### Success Criteria:

#### Automated Verification:

- All three endpoints send `X-Total-Count` matching the filtered row count
- The header survives filters and paging — same value on page 1 and page 2
- `Access-Control-Expose-Headers` names it
- Response bodies unchanged
- Full suite green, linting passes

#### Manual Verification:

- The header is visible in the browser's network panel on all three endpoints

---

---

## Phase 3: The pager uses the total (added at the owner's request)

### Overview

The ticket assigned the frontend to the Designer. The owner asked for it in the
same pass, so it lands here.

### The bug this closes

All three pagers inferred the end of the list from `data.length < pageSize`.
That inference is wrong for exactly one input: a total that is a whole multiple
of the page size. The final page comes back FULL, the test never fires, "Next"
stays enabled, and the click lands on an empty table with nothing to say whether
the data or the app broke.

It was not hypothetical. Two E2E fixtures had been sized to *depend* on it —
`_FAKE_X_POSTS_ROWS` carried a comment explaining that 20 padding rows were
needed so "Next" would not be disabled, which is the bug being used as a
mechanism. Those tests were clicking through to an empty page 2 and asserting
only the URL.

### Changes Required:

#### 1. Shared pager helper

**File**: `static/index.html`

**Intent**: One `_applyPaging` for all three lists, replacing three copies of the
same wrong inference. Reads the header via `_totalCount`; a missing header falls
back to the old behaviour rather than rendering "Strona 3 z null" — no header
means a server we cannot ask, not a server that answered zero.

**Contract**: `_totalCount(response) -> number | null`;
`_applyPaging(labelId, prevId, nextId, page, pageSize, rowCount, total)`.
Pages = `max(1, ceil(total / pageSize))` — an empty list is still one page,
because "Strona 1 z 0" reads as a broken pager.

#### 2. The three fetches

**File**: `static/index.html`

**Intent**: `fetchAnnouncements`, `fetchXPosts` and `fetchMyWalletAnnouncements`
read the header and delegate to the helper.

#### 3. Fixtures that encoded the bug

**File**: `tests/e2e/conftest.py`

**Intent**: The admin fake returned every row for every page, so a total was
meaningless; it now really slices. The corpus goes to 40 (page 1 still holds the
20 `test_refresh` counts, and a second page exists). The x-posts padding goes
from 20 to 21 so page 2 holds an actual row instead of nothing.

#### 4. Assertions that pinned the whole label

**File**: `tests/e2e/*.py`

**Intent**: Roughly thirty assertions read `to_have_text("Strona 2")`. They are
about routing, refresh and login landing on the right page — not about how many
pages exist. Relaxed to a prefix match so each stays about its own subject;
`test_pagination.py` owns the arithmetic.

### Success Criteria:

#### Automated Verification:

- The label reads "Strona N z M" on all three lists
- A full final page disables "Next" — verified by deliberate break
- Changing the page size recomputes the page count
- Full suite green, linting passes

#### Manual Verification:

- The three pagers read correctly in the browser and stop at the real end

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: The queries return their total

#### Automated

- [x] 1.1 Every listing query carries `COUNT(*) OVER()`
- [x] 1.2 The total is independent of page_size
- [x] 1.3 A page past the end reports the true total, not 0
- [x] 1.4 Full suite green, linting passes

### Phase 2: The endpoints send the header

#### Automated

- [x] 2.1 All three endpoints send a correct `X-Total-Count`
- [x] 2.2 `Access-Control-Expose-Headers` names it
- [x] 2.3 Response bodies unchanged
- [x] 2.4 Full suite green, linting passes

#### Manual

- [ ] 2.5 Header visible in the browser network panel

### Phase 3: The pager uses the total

#### Automated

- [x] 3.1 Label reads "Strona N z M" on all three lists
- [x] 3.2 A full final page disables Next — confirmed by deliberate break
- [x] 3.3 Page size change recomputes the page count
- [x] 3.4 Full suite green (1073), linting passes

#### Manual

- [ ] 3.5 The three pagers read correctly in the browser and stop at the real end
