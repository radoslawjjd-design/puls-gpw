# Frame Brief: Restrict sentiment inside structured_analysis from user-facing /announcements

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

`GET /announcements` for a non-admin (`user`) API key returns
`structured_analysis` containing `sentiment` (and, per the original ticket
wording, "possibly other internal fields"). PUL-42 explicitly deferred
restricting anything inside `structured_analysis`, leaving this open.

## Initial Framing (preserved)

- **User's stated cause or approach**: "Decide the user-facing allowlist for
  `structured_analysis` and enforce it" — framed as an open product decision
  still to be made, same leak location as PUL-42 (`AnnouncementUser` /
  `list_announcements_user`).
- **User's proposed direction**: Apply PUL-42's pattern (model field removal +
  defense-in-depth query trim) to whichever fields the allowlist excludes.
- **Pre-dispatch narrowing**: User confirmed (Q1) the concern is **only**
  `sentiment` — not a hypothetical broader set. User confirmed (Q2) this is
  **the same pattern as PUL-42** (mechanical alignment of API with an
  existing front-end decision), not a fresh product debate.

## Dimension Map

The observation could originate at any of these dimensions:

1. **Schema surface** — does `structured_analysis` actually contain fields
   beyond what's documented, making the allowlist genuinely open-ended?
2. **Product decision** — has anyone (frontend, docs, prior tickets) already
   decided which `structured_analysis` fields are user-visible vs admin-only,
   or is that decision still unmade?  ← initial framing assumed "unmade"
3. **Internal consumers** — does any in-process pipeline code (xpost
   generator, post selection) read `sentiment` from the API response in a way
   that filtering would break?
4. **Leak surface count** — is the JSON response body the only place
   `sentiment` leaks, or are there other surfaces (DOM attributes, cached
   autocomplete, etc.) that also need closing, mirroring the `data-score`
   side-channel found in PUL-42?

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| 1. Schema surface is open-ended (more fields could appear) | `src/analyzer.py:43-53` — `_AnalysisResponse` is a closed Pydantic model (`event_type`, `sentiment`, `key_numbers`, `summary_pl`) with `extra="ignore"` applied via `model_validate(...).model_dump()` before `json.dumps` (`src/analyzer.py:170,249`). `db/bigquery.py:58` — single `STRING` column, one writer (`save_analysis_result`). No other code path writes to this column. | NONE — schema is closed, exactly 4 fields, confirmed by grep across `src/` and `db/bigquery.py`. |
| 2. Product decision already made (frontend gate) | `static/index.html:635-638` — `sentiment` badge is rendered **only when `role === 'admin'`**, in the *same* conditional block that also gates `score` (`:639-640`) and `url` (`:641-642`) — the identical pattern PUL-42's plan cited as evidence for `analysis_score` already being admin-only by convention. The other 3 `structured_analysis` fields (`event_type`, `key_numbers`, `summary_pl`) are rendered unconditionally for both roles (`:630-634`, `:591`). | STRONG — decision already exists in the codebase, not hypothetical. |
| 3. Internal consumers depend on `sentiment` via the API | `grep -rn sentiment src/` → only hits in `analyzer.py` itself (definition/validation). `main.py`, `post_generator.py`, `post_selection.py` have zero references to `sentiment` or to fetching announcements through the HTTP API (no `USER_API_KEY`/`ADMIN_API_KEY` usage in `main.py`) — internal pipeline reads BigQuery directly, bypassing `/announcements` entirely. | NONE — no internal consumer would break. |
| 4. Other leak surfaces beyond the JSON body | `static/index.html:570` — `data-structured-analysis="${esc(JSON.stringify(row.structured_analysis))}"` embeds the **full** `structured_analysis` blob (including `sentiment`) into a DOM attribute unconditionally, regardless of role — same side-channel shape as the `data-score` leak PUL-42 closed as a side effect. | STRONG — a second leak surface exists, but it closes automatically once the API stops sending `sentiment` to user-role responses (same as PUL-42's `data-score` side effect — no separate frontend change needed). |

## Narrowing Signals

- User confirmed scope is `sentiment` only — hypothesis 1 (open-ended schema)
  is moot; no need to design a general-purpose allowlist mechanism, just fix
  the one field.
- User confirmed this is "the same pattern as PUL-42" — hypothesis 2 (product
  decision already made) is the operative one; no product discussion needed
  before planning.
- Frontend's existing `role === 'admin'` gate around `sentiment` is decisive:
  it's not a hint the field *should* be gated, it's proof the product
  decision already happened and the API just hasn't caught up.

## Cross-System Convention

PUL-42 established the convention for exactly this shape of problem
(internal/admin-only field visible in BQ row but reaching `AnnouncementUser`
unfiltered): drop the field from the Pydantic model (authoritative boundary,
`extra="ignore"` makes this sufficient alone), trim the BQ `SELECT` as
defense-in-depth, lock the user-response shape with an exact-key-set
allowlist test, and confirm the admin path is untouched. The leading
hypothesis here matches that convention exactly — the only structural
difference is that `sentiment` is nested inside the `structured_analysis`
dict rather than a top-level column, so the fix is a post-parse strip (after
`_parse_structured_analysis`) rather than a model-field deletion.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: strip `sentiment` from
> `structured_analysis` specifically in the `user`-role branch of
> `GET /announcements`, applying the exact PUL-42 pattern — not designing a
> general allowlist mechanism for an open-ended schema.

The initial framing ("decide the allowlist") implied an unresolved product
question and a schema that might need ongoing governance. Neither is true:
the schema is closed (4 fields, one writer, `extra="ignore"` enforced at
parse time) and the product decision is already encoded in
`static/index.html`'s role gate. What changes if this is planned correctly:
the plan scope shrinks to one field, one new post-parse step, and tests that
lock the exact key set of `AnnouncementUser.structured_analysis` for
user-role — no allowlist abstraction, no future-proofing for fields that
don't exist.

## Confidence

**HIGH** — strong evidence (closed schema confirmed by grep, frontend gate
confirmed by direct read at three line ranges) + matches the PUL-42 convention
exactly + user's own narrowing answers independently landed on the same
conclusion before evidence was presented to them.

## What Changes for /10x-plan

Plan should scope to: post-parse strip of `sentiment` from
`structured_analysis` in the `user`-role branch of `/announcements`
(`src/api.py`), no model/query change needed at the BQ layer (the column is
the full JSON string already, fetched once for both roles — there is no
column to drop, only a key to drop after parsing), plus an exact-key-set
regression test on the parsed `structured_analysis` dict for user-role,
mirroring `tests/test_api.py`'s existing allowlist test pattern from PUL-42.
No frontend change needed (DOM-attribute leak closes automatically, same as
PUL-42's `data-score` side effect).

## References

- Source files: `src/api.py:58-65,89-96,141-151`; `src/analyzer.py:43-53,170,249`;
  `db/bigquery.py:58,621-668`; `static/index.html:570,635-638`
- Related research: none (`research.md` not present for this change)
- Prior convention: `context/archive/2026-06-19-remove-analysis-score/plan.md`
- Investigation: direct reads (no sub-agent dispatch — small, familiar
  surface area, single-file/single-function scope)
