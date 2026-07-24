<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Per-item "new" badge clearing (PUL-94) — Phase 1

- **Plan**: context/changes/announcement-seen-badges/plan.md
- **Scope**: Phase 1 of 2 (commit 3b97ebc)
- **Date**: 2026-07-24
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 2 observations — all 4 fixed post-review

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (7/7 planned changes MATCH, zero drift) |
| Scope Discipline | PASS (no EXTRA in diff) |
| Safety & Quality | WARNING (F1, F2 — fixed) |
| Architecture | PASS |
| Pattern Consistency | PASS (F3 observation — fixed) |
| Success Criteria | PASS (`uv run pytest` re-run: 707 passed; manual 1.3-1.5 human-confirmed) |

## Verified clean (no findings)

- XSS via `data-seen-key`: `esc()` escapes `& < > " '` — sufficient for the double-quoted attribute; the dataset value never round-trips into HTML (only `Set.add`/`Set.has`/`JSON.stringify`).
- Prototype pollution from `JSON.parse` of `faro_seen_items`: no vector (`Object.keys` of own data properties into a `Set`).
- Prune correctness: cutoff = min of non-null thresholds cannot prune an entry still able to badge in either view; 500-cap evicts oldest by `published_at`.
- Re-entrancy: `doLogout` synchronous; post-logout listeners inert via `role` guard; skeleton renders never arm `_viewRendered`.
- Cross-view key identity: both endpoints serialize through the same pydantic models → `ticker|published_at` matches across views.
- Multi-tab last-writer-wins and per-browser (not per-user) state: accepted classes, explicitly out of scope in the plan.

## Findings

### F1 — Unguarded `localStorage.setItem` on click/navigation/logout critical path

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:2187 (`_saveSeenItems`), :2163 (`_markViewSeen`)
- **Detail**: `QuotaExceededError` (full storage / legacy Safari private mode) would throw out of the row-click handler before `openModal` (popup never opens), out of `_navigateToView` first statement (navigation bricked), and mid-`doLogout` (half-logged-out UI). Read path was guarded; write path wasn't.
- **Fix**: try/catch around both `setItem` calls — storage is a best-effort cache; in-memory Set/threshold carry the session.
- **Decision**: FIXED

### F2 — Empty/filtered render arms `_viewRendered`; exit clears badges beyond what was seen

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: static/index.html:4463-4470
- **Detail**: Flag set before the `!data.length` early return and for filtered/paginated subsets — filter-to-zero → leave → threshold=now clears everything. Consistent with the plan's explicit whole-view-advance decision and NOT a regression (old code advanced on first render), but the guard's comment over-promised per-item precision.
- **Fix A ⭐ (applied)**: keep semantics, fix the comment to state the guard protects never-*displayed* views and that granularity is whole-view (empty/filtered renders count as display).
- **Fix B (rejected)**: stamp threshold with max(published_at of rendered rows) — reverses the plan decision.
- **Decision**: FIXED via Fix A

### F3 — Two independent view-key mappings could drift

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:4463 vs :2220
- **Detail**: `renderTable` defaulted any unknown containerId to 'announcements' — a future third consumer would silently arm the announcements flag.
- **Fix**: explicit `_CONTAINER_SEEN_KEYS` map (`table-body`/`my-wallet-table-body`); unknown containerIds get no threshold, no badge, no flag.
- **Decision**: FIXED

### F4 — Row with `published_at` but null ticker: badge without durable per-item clear

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: static/index.html:4498-4500
- **Detail**: `seenKey` required both fields, so a null-ticker row badged but its click-clear survived only until the next render.
- **Fix**: key requires only `published_at`; null ticker → empty prefix (`'|<iso>'`), still unique enough and durable.
- **Decision**: FIXED

## Post-fix verification

`uv run pytest` → 707 passed (full suite, incl. all e2e) after applying F1-F4.
