# Frame Brief: Portfolio-status xpost generator skill

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

PUL-39's description lists three explicit, unresolved design questions
under "Open questions (to resolve in /10x-shape or /10x-plan)":

1. Skill packaging: standalone `.claude/skills/portfolio-xpost/` orchestrator vs. a Python feature module.
2. Media attachments: confirm screenshots should attach to tweets (drives a publisher extension), or text-only.
3. Where "yesterday's data" persists between sessions (memory file vs BigQuery vs none).

## Initial Framing (preserved)

- **User's stated cause or approach**: the ticket itself already leans toward standalone skill packaging ("recommended") and toward building media support (the Notes/Gap-to-close section explicitly calls out extending `x_publisher.py` with media upload). Persistence was left fully open with no stated lean.
- **User's proposed direction**: resolve the three open questions before `/10x-plan`, per the agreed `/10x-frame` step in the PUL-39 skill sequence.
- **Pre-dispatch narrowing**: user decision captured directly via two AskUserQuestion rounds (see below) rather than investigation, since these are product/scope calls only the user can make — not bugs with discoverable root causes.

## Dimension Map

The three open questions map directly onto three independent scope dimensions — no fourth dimension found in the ticket or codebase:

1. **Skill packaging** — where the orchestrator code lives and how it's invoked.
2. **Media attachment scope** — whether v1 ships with screenshot upload or text-only.
3. **Cross-session persistence** — where "yesterday's balance/positions" data is read from to compute day deltas.

## Hypothesis Investigation

| Dimension | Evidence | Verdict |
| --- | --- | --- |
| Packaging: standalone skill dir | All 26 existing skills in this repo follow `.claude/skills/<name>/SKILL.md` (`Glob .claude/skills/*/SKILL.md` — 10x-new, 10x-plan, 10x-goal-implement, etc., zero exceptions). Ticket's own recommendation matches. | STRONG |
| Packaging: Python module in `src/` | No existing skill in this repo is implemented as a bare `src/` module instead of a `SKILL.md` orchestrator — would be the first exception to an unbroken convention. | NONE |
| Media: full upload in v1 | `src/x_publisher.py:42-47` builds a `tweepy.Client` (API v2) — text-only, no `media_ids` parameter on `create_tweet`. Media requires a second auth path (`tweepy.API` v1.1 + `OAuth1UserHandler`) for `media_upload()`. Real, scoped extension, not a one-liner. User confirmed: ship it now. | STRONG (resolved by user) |
| Persistence: BigQuery table | `db/bigquery.py` already has an established, repeated pattern for X-post-adjacent durable state: `_X_POSTS_SCHEMA`, `create_x_posts_table_if_not_exists()`, `ensure_x_posts_schema_current()` (PUL-29). No JSON/file-based state exists anywhere in `src/` for cross-session data. User confirmed: new table modeled on `x_posts`. | STRONG (resolved by user) |
| Persistence: local JSON file | No precedent in the codebase; project's `db/bigquery.py` is the sole durable-state mechanism today (Cloud Run jobs have no guaranteed persistent disk between runs). | WEAK |

## Narrowing Signals

- Grep across the whole repo for "legacy spec" / "legacy skill" / "broker_data" found **zero hits outside `change.md` itself** — the format/extraction rules referenced in the ticket as "legacy spec" exist only in the user's prior ad-hoc chat usage, not as a committed artifact. There is nothing to read for `/10x-research` beyond the ticket text; the ticket description is the source of truth.
- User resolved media scope → full upload now (not deferred).
- User resolved persistence → new BigQuery table mirroring `x_posts`, not a file.

## Cross-System Convention

This project has one consistent answer for "where does durable, cross-run state for the X-posting pipeline live?": BigQuery, with a table-per-concern pattern (`announcements`, `x_posts`) and an additive-migration helper (`ensure_schema_current`). A new `portfolio_snapshots`-style table follows that exact convention rather than introducing a second persistence mechanism.

For publishing, the convention is a thin transport layer (`src/x_publisher.py`) with zero business logic — the media extension should stay inside that module's existing shape, not leak upload logic into the skill or `post_main.py`.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: build the skill exactly as scoped in PUL-39, now with all three open questions closed — standalone skill packaging, full media-upload support in v1, and a new BigQuery table (mirroring `x_posts`) for cross-session day-delta data.

The initial framing held up on every axis — no reframe was needed, only resolution of explicitly-flagged open questions. The one new fact that changes `/10x-research`'s job: there is no "legacy spec" file to find. The ticket description is the only specification; ambiguities during research/planning should be resolved by asking the user, not by searching for a document that doesn't exist.

## Confidence

**HIGH** — packaging and persistence both have strong, unambiguous codebase-convention evidence; media scope and persistence choice were directly confirmed by the user. No further verification needed before `/10x-plan`.

## What Changes for /10x-plan

The plan should include four work areas, not three: (1) new BigQuery table + schema migration for portfolio snapshots, modeled on `_X_POSTS_SCHEMA`/`create_x_posts_table_if_not_exists`/`ensure_x_posts_schema_current`; (2) `x_publisher.py` media-upload extension via `tweepy.API`/`OAuth1UserHandler` (additive, alongside the existing v2 `Client`); (3) the `.claude/skills/portfolio-xpost/SKILL.md` orchestrator itself (extraction, verification, formatting, approval gate); (4) explicitly note in the plan that there is no legacy spec document — the plan's acceptance criteria come from the ticket text alone.

## References

- Source files: `src/x_publisher.py:37-114`, `db/bigquery.py:66-187`
- Ticket: PUL-39 description (`context/changes/portfolio-xpost-skill/change.md` links to it via `tracking.linear`)
- Related research: none — no `research.md` exists for this change; `/10x-research` should run fresh
