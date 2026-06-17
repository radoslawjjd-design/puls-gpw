# X-post Selection & Amounts Guard — Plan Brief

> Full plan: `context/changes/xpost-selection-and-amounts-guard/plan.md`

## What & Why

The morning X-post pipeline shipped a broken `ranek` thread on 2026-06-17: four slots
filled by one company (TOWERINVT, 7 tied rows), starving ASB; and `wyniki_finansowe`
posts went out with no amounts. Fix both selection defects so a thread shows up to N
**distinct** companies and never publishes a number-less financial/sales-results tweet.

## Starting Point

`fetch_top_n_for_window` (`db/bigquery.py:318`) does `ORDER BY analysis_score DESC
LIMIT N` over raw rows — no ticker dedup and no tie-break, so N raw rows ≠ N companies.
Ticker dedup happens later (and twice: `post_generator.py:329`, `post_main.py:206`).
`key_numbers` lives in the `structured_analysis` STRING column and is legitimately empty
for qualitative events (`kontrakt_znaczacy`, `dywidenda`), so an empty-numbers guard
must be event-type-narrow. The publish gate (`is_publishable`) sees only tweet strings,
no `event_type`.

## Desired End State

`fetch_top_n_for_window` returns up to N distinct companies, deterministically ordered,
with empty-`key_numbers` `wyniki_*` rows dropped *before* the top-N cut so freed slots
backfill from the next-best company. As a safety net, `post_main` blocks publishing any
`wyniki_*` body tweet that carries no digit.

## Key Decisions Made

| Decision                         | Choice                                            | Why (1 sentence)                                                            | Source |
| -------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------- | ------ |
| Where to apply Defect-B drop     | Python over-fetch + select                        | Backfills dropped slots, no fragile BQ JSON parsing, fully unit-testable.   | Plan   |
| Selection-logic home             | Pure helper in new `src/post_selection.py`        | Dependency-light (json5 only); avoids dragging genai into the db layer.      | Plan   |
| Tie-break ordering               | `score DESC, then published_at DESC`              | Deterministic and editorially sensible (freshest news wins ties).           | Plan   |
| Publish belt form                | Belt in `post_main` (ticker→event_type map)       | Real `event_type` context lives there; keeps `validate_post` pure.          | Plan   |
| Event-type scope                 | Both `wyniki_finansowe` + `wyniki_sprzedazowe`    | Sales results are as number-dependent as financial results — symmetric.     | Plan   |

## Scope

**In scope:**
- Pure `select_top_companies(rows, n)`: dedup → event-narrow drop → top-N.
- Rewrite `fetch_top_n_for_window` to over-fetch (deterministic order + safety cap) and delegate.
- Publish belt in `post_main`: no digit-less `wyniki_*` body tweet.

**Out of scope:**
- Fetching NewConnect attachments / XBRL to recover `key_numbers` (follow-up).
- Minimum-company floor (existing no-post path covers an emptied pool).
- Removing the existing `generate_post` / `post_main` dedups (kept as belts).
- Moving the numbers guard into BigQuery SQL.

## Architecture / Approach

One pure helper in a new dependency-light `src/post_selection.py` owns all selection
logic on plain dicts; `fetch_top_n_for_window` over-fetches the window (ordered
`score DESC, published_at DESC`, capped) and calls it. The publish belt is an independent `event_type`-keyed
check in `post_main`, mapping each results announcement to its body tweet by ticker.

## Phases at a Glance

| Phase                        | What it delivers                                              | Key risk                                          |
| ---------------------------- | ------------------------------------------------------------ | ------------------------------------------------- |
| 1. Selection layer (A + B)   | `select_top_companies` + rewritten `fetch_top_n_for_window`  | Helper/SQL ordering contract drifting out of sync |
| 2. Publish belt (B safety)   | `post_main` blocks digit-less `wyniki_*` body tweets         | Ticker→tweet match missing a body tweet           |

**Prerequisites:** none — all files and the `json5` dependency are in place.
**Estimated effort:** ~1 session across 2 phases.

## Open Risks & Assumptions

- The helper assumes SQL pre-sorts rows by selection priority; the two must stay in sync
  (called out in Critical Implementation Details).
- Over-fetch safety cap (~200) assumes per-window volumes stay small — true for current
  small-cap GPW/NewConnect flow.
- Belt matches tweets to companies by parenthesised ticker; a malformed tweet missing
  its ticker would be unmatched (acceptable — `validate_post` already requires it).

## Success Criteria (Summary)

- The 2026-06-17 `ranek` data yields TOW **and** ASB, not 4× TOW.
- A number-less `wyniki_finansowe` top row is dropped and its slot backfilled.
- A digit-less results tweet is never published; the owner email still sends.
