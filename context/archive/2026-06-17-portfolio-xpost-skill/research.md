---
date: 2026-06-17T00:00:00+02:00
researcher: Claude
git_commit: 52c325d149e0c5164c2b640a7b969ef23a22e037
branch: radoslawjjd/pul-39-portfolio-status-xpost-generator-skill
repository: puls-gpw
topic: "Portfolio status X-post generator skill (PUL-39) — implementation conventions"
tags: [research, codebase, skills, x_publisher, bigquery, gemini, portfolio-xpost]
status: complete
last_updated: 2026-06-17
last_updated_by: Claude
---

# Research: Portfolio status X-post generator skill (PUL-39) — implementation conventions

**Date**: 2026-06-17
**Researcher**: Claude
**Git Commit**: 52c325d149e0c5164c2b640a7b969ef23a22e037
**Branch**: radoslawjjd/pul-39-portfolio-status-xpost-generator-skill
**Repository**: puls-gpw

## Research Question

Given the three open questions in PUL-39 already resolved by `/10x-frame` (standalone skill packaging, full media-upload in v1, new BigQuery table mirroring `x_posts`), what concrete codebase conventions should `/10x-plan` follow for: (1) the skill's structure/approval-gate, (2) the `x_publisher.py` media extension, (3) the new BigQuery table, and (4) the vision-extraction step?

## Summary

No prior art exists in this repo for vision-extraction, media upload, or a `broker_data/` directory — this skill is genuinely new ground on those three axes. However, every *adjacent* concern has a strong, consistent convention to mirror: skill structure (frontmatter + numbered Process steps + guardrails section, used by all ~26 skills), human-approval gates (`AskUserQuestion` with a "Recommended" option), BigQuery table additions (schema constant → `create_*_if_not_exists` → `ensure_schema_current` → thin binding → parameterized DML insert), and the Gemini client singleton (`src/gemini_client.py`, VertexAI-backed, easily extended for a vision call). The existing ESPI/EBI X-post pipeline (`post_selection.py` → `post_generator.py` → `post_supervisor.py` → `x_publisher.py`) is a separate, parallel pipeline — PUL-39 should not touch it, only reuse its conventions (cashtag/char-limit discipline, Gemini JSON-parsing-with-json5 workaround, BQ persistence pattern).

## Detailed Findings

### Skill orchestration conventions

- No existing skill uses vision/image-reading or touches X/Twitter — this will be the first end-to-end skill doing both.
- House style for `SKILL.md`: YAML frontmatter (`name`, `description`, optional `argument-hint`, `allowed-tools`) → top-level heading → "Initial Response" (what happens with no args) → numbered "Process" steps → a "Critical guardrails" / "What this skill does NOT do" section listing explicit non-goals.
- Human-approval-before-irreversible-action gate is universally done via `AskUserQuestion`, 2-3 options, one marked "(Recommended)", `multiSelect: false`. Reference: `.claude/skills/10x-implement/SKILL.md:84-96,154-165,221-233`, `.claude/skills/10x-archive/SKILL.md:125-134`.
- Recommended shape for the new skill: Vision Extraction step → Thread Generation step → Approval gate (`AskUserQuestion`: Publish (Recommended) / Edit & retry / Cancel) → conditional Publish step → conditional Archive step.

### `x_publisher.py` media upload extension

- Current state (`src/x_publisher.py:37-114`): `XPublisher` wraps a single `tweepy.Client` (API v2, OAuth 1.0a user-context), text-only. `publish_thread(list[str])` posts a reply-chain, returns tweet IDs, raises `XPublisherError` (nothing posted) or `XPublishPartialError(published_ids, cause)` (partial failure) — both defined in `src/exceptions.py:32-52`.
- `get_x_publisher()` (`src/x_publisher.py:90-114`) is a lazy, thread-safe singleton reading `X_API_KEY`/`X_API_SECRET`/`X_ACCESS_TOKEN`/`X_ACCESS_SECRET`, stripping BOM/whitespace via `_clean()` (Secret Manager injection artifact), fail-fast on missing vars.
- `.env.example` deliberately has no `X_*` entries — these are Secret-Manager-only in production; the new skill must not add them there either.
- No `tweepy.API`/`OAuth1UserHandler`/`media_upload` precedent anywhere in the repo — this is a genuinely new code path. Media upload needs a **second** auth object (`tweepy.API(OAuth1UserHandler(...))`) built from the *same* four env vars, since v1.1 media upload has no v2 equivalent in tweepy.
- No existing binary/image-loading convention either (Gemini calls in this repo are text-only today — see below). The skill will need to establish the image-read convention itself (plain `open(path, "rb").read()` is the simplest fit, no PIL/base64 precedent to match).
- Test convention: `tests/test_x_publisher.py` uses a `FakeClient` monkeypatched over `xp.tweepy.Client`, plus a `_reset_singleton` fixture and `_set_creds()` helper. A media test should add a `FakeAPI`/`FakeOAuth1UserHandler` analogue and assert `media_ids` is passed through to `create_tweet`.

### BigQuery table conventions

- Exact 4-part pattern to mirror for a new `portfolio_snapshots` table, all in `db/bigquery.py`:
  1. Schema constant — `_X_POSTS_SCHEMA` (`db/bigquery.py:68-76`), list of `bigquery.SchemaField`.
  2. `create_<table>_if_not_exists()` (`db/bigquery.py:131-141`) — `_table_ref()` + `client.get_table()` + create on `NotFound`.
  3. Generic `ensure_schema_current(table_name, schema)` (`db/bigquery.py:144-176`) — additive column migration, safe to call every startup.
  4. Thin per-table binding, e.g. `ensure_x_posts_schema_current()` (`db/bigquery.py:179-186`).
- Dataset/project resolution: `_DATASET = os.environ.get("BIGQUERY_DATASET", "espi_ebi")` (`db/bigquery.py:42`), client via `_get_client()` (`db/bigquery.py:82-102`) with the mandatory `with_quota_project` guard at line 94-95 (per `.claude/rules/db-bigquery.md`).
- Insert/query shape: parameterized DML via `bigquery.QueryJobConfig(query_parameters=[...])`, never raw string interpolation — see `save_x_post()` (`db/bigquery.py:551-616`) for the exact `ScalarQueryParameter`/`ArrayQueryParameter` pattern. No `load_table_from_dataframe` precedent for single-row writes.
- Test convention: `tests/test_bigquery.py:191-227` mocks `_get_client()`, asserts on `client.query.call_args` for both SQL string shape (backtick-quoting reserved words, per the `window`-column lesson) and parameter binding — mocked only, no real BQ round-trip in CI. `context/foundation/lessons.md` flags that hand-written SQL still needs a *manual* real-BigQuery round-trip before merging, since mocks don't catch SQL syntax errors.

### Existing X-post pipeline + vision/broker_data status

- `broker_data/` does not exist yet anywhere in the repo — confirmed absent at root and in all subdirectories.
- The existing ESPI/EBI pipeline is a complete, separate system and should NOT be modified by PUL-39:
  - Selection: `src/post_selection.py:48-88` (`select_top_companies()`)
  - Generation: `src/post_generator.py:310-424` (`generate_post()`), calls `genai.Client(vertexai=True, ...).models.generate_content(model=GEMINI_MODEL, ...)`, parses with `json5.loads()` to tolerate Gemini's trailing-comma bug
  - Validation: `src/post_supervisor.py:32-80` (`validate_post()`)
  - Publish: `src/x_publisher.py:50-81`
  - Persistence: `db/bigquery.py:131-186` (`x_posts` table) + email via `src/notifier.py:168`
- Gemini usage today is **text-only** — `src/gemini_client.py:16-27` exposes a simple `get_client()` singleton (`genai.Client(vertexai=True, project=..., location=...)`). No vision/image (`Part.from_file`, `Image`, `InlineData`) calls exist anywhere. The new skill's extraction step needs to add a vision-capable call (e.g. multimodal `generate_content` with an image part) — there's no existing wrapper to reuse beyond the client singleton itself.
- No prior design discussion of "portfolio", "broker_data", "XTB", or "screenshot" exists in `context/changes/**/` or `context/archive/**/` outside this change's own `change.md`/`frame.md` — confirms the frame's finding that the ticket text is the sole specification.

## Code References

- `src/x_publisher.py:37-114` — XPublisher class, publish_thread, get_x_publisher singleton
- `src/exceptions.py:32-52` — XPublisherError / XPublishPartialError
- `db/bigquery.py:42` — `_DATASET` env resolution
- `db/bigquery.py:68-76` — `_X_POSTS_SCHEMA` template
- `db/bigquery.py:82-102` — `_get_client()` + `with_quota_project` guard
- `db/bigquery.py:131-141` — `create_x_posts_table_if_not_exists()` template
- `db/bigquery.py:144-176` — `ensure_schema_current()` generic helper
- `db/bigquery.py:179-186` — `ensure_x_posts_schema_current()` thin binding
- `db/bigquery.py:551-616` — `save_x_post()` parameterized DML insert pattern
- `src/gemini_client.py:16-27` — Gemini VertexAI client singleton (text-only today)
- `src/post_generator.py:310-424,392-401` — existing LLM thread-generation call pattern
- `src/post_selection.py:48-88` — existing announcement selection logic
- `src/post_supervisor.py:32-80` — existing validation/supervisor logic
- `tests/test_x_publisher.py:1-139` — FakeClient mocking pattern for publisher tests
- `tests/test_bigquery.py:191-227` — table-creation and insert test pattern
- `.claude/skills/10x-implement/SKILL.md:84-96,154-165,221-233` — AskUserQuestion approval-gate pattern
- `.claude/skills/10x-archive/SKILL.md:125-134,222-230` — approval gate + guardrails section pattern

## Architecture Insights

- This project has exactly one durable-state mechanism (BigQuery, table-per-concern) and one external-publish transport (`x_publisher.py`, thin/no business logic) — both conventions are strong and should be extended, not replaced, for PUL-39.
- The existing X-post pipeline keeps LLM generation, validation, and publishing as separate modules (`post_generator.py` / `post_supervisor.py` / `x_publisher.py`) rather than one monolith — PUL-39's skill should follow the same separation-of-concerns internally even though it's a single skill file, e.g. by keeping vision-extraction, thread-formatting, and the publish call as clearly delimited steps.
- Media upload is the one place where PUL-39 cannot mirror an existing pattern — it's new tweepy API surface (v1.1 `OAuth1UserHandler`/`media_upload`) layered next to the existing v2 `Client`. Plan should treat this as its own implementation phase with its own test double (`FakeAPI`), separate from the BigQuery and skill-orchestration phases.

## Historical Context (from prior changes)

- `context/changes/portfolio-xpost-skill/change.md` — original ticket (PUL-39/GH#53), status `preparing`.
- `context/changes/portfolio-xpost-skill/frame.md` — resolved the three open design questions (packaging, media scope, persistence) before this research; no reframe occurred.
- PUL-29 lesson (`context/foundation/lessons.md`) — BigQuery reserved-keyword (`window` column) and mocked-tests-don't-catch-SQL-errors lesson, directly applicable to the new `portfolio_snapshots` table's schema/insert code.

## Related Research

- None pre-existing for this change; this is the first `research.md` for `portfolio-xpost-skill`.

## Open Questions

- Exact vision model/call shape for screenshot extraction (e.g. `gemini-2.5-flash` vision vs a dedicated multimodal model, prompt structure for extracting balances/positions) — left for `/10x-plan` to decide, no existing convention to mirror.
- Exact `portfolio_snapshots` schema fields (which balance/position fields, per-wallet breakdown for main/IKZE/short/long) — depends on what the vision extraction actually returns; to be finalized in `/10x-plan`.
- Where archived screenshots should move to (a new `broker_data/archive/` subfolder vs. moving out of `broker_data/` entirely) — not specified in the ticket; `/10x-plan` should pick a convention and state it explicitly since there's no precedent.
