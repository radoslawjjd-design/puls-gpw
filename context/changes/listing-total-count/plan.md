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
- No pagination UI — the Designer adds "Strona 1 z N" after deploy.
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

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: The queries return their total

#### Automated

- [ ] 1.1 Every listing query carries `COUNT(*) OVER()`
- [ ] 1.2 The total is independent of page_size
- [ ] 1.3 A page past the end reports the true total, not 0
- [ ] 1.4 Full suite green, linting passes

### Phase 2: The endpoints send the header

#### Automated

- [ ] 2.1 All three endpoints send a correct `X-Total-Count`
- [ ] 2.2 `Access-Control-Expose-Headers` names it
- [ ] 2.3 Response bodies unchanged
- [ ] 2.4 Full suite green, linting passes

#### Manual

- [ ] 2.5 Header visible in the browser network panel
