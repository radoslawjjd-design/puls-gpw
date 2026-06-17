# Portfolio status X-post generator skill — Plan Brief

> Full plan: `context/changes/portfolio-xpost-skill/plan.md`
> Frame brief: `context/changes/portfolio-xpost-skill/frame.md`
> Research: `context/changes/portfolio-xpost-skill/research.md`

## What & Why

Build a new Claude Code skill that turns XTB broker screenshots into two ready-to-publish X threads (main+IKZE wallets / short+long wallets) with attached images, after a human approval step. The initial framing held up — this is purely a build task; the only open questions were packaging, media scope, and persistence, all now resolved.

## Starting Point

The repo has 26 existing skills with a consistent structure and approval-gate pattern, a working text-only X publisher (`src/x_publisher.py`), a text-only Gemini client, and a proven BigQuery table convention (`x_posts`). Nothing in the repo today reads images, uploads media to X, or has a `broker_data/` folder — those three pieces are new.

## Desired End State

A user drops XTB screenshots into `broker_data/main/`, `ikze/`, `short/`, `long/`, runs the skill, reviews two generated thread drafts, approves them, and both are published live on X with screenshots attached. The day's portfolio data is recorded in BigQuery for the next run's delta calculation, and the screenshots move to a dated archive folder.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Skill packaging | Standalone `.claude/skills/portfolio-xpost/` | Matches the unbroken convention of all 26 existing skills. | Frame |
| Media attachment scope | Full media upload in v1 | User chose to ship it now rather than defer. | Frame |
| Cross-session persistence | New BigQuery table mirroring `x_posts` | Matches the project's only durable-state convention. | Frame |
| Uncertain extraction handling | Flag + show user before generating threads | Zero risk of publishing wrong financial numbers. | Plan |
| `portfolio_snapshots` schema shape | One row per wallet per day | Matches the flat, per-concern table convention used everywhere else. | Plan |
| Screenshot archive location | `broker_data/archive/<date>/` | Keeps history in one place, easy to find by date. | Plan |
| Media upload failure handling | Fallback to text-only, flag the degradation | Publishing shouldn't block on one failed image upload. | Plan |
| Vision/Gemini test strategy | Mock the Gemini client, no real API calls in CI | Matches the existing `FakeClient` test-double convention. | Plan |
| Approval gate granularity | Approve / Edit-via-reprompt / Cancel | Matches the existing `AskUserQuestion` pattern; no inline editor needed. | Plan |

## Scope

**In scope:**
- New `portfolio_snapshots` BigQuery table + insert/query functions
- `x_publisher.py` media-upload extension (`publish_thread_with_media`)
- New `.claude/skills/portfolio-xpost/SKILL.md` orchestrator (extraction → generation → approval → publish → archive)
- Vision extraction helper in `src/gemini_client.py`

**Out of scope:**
- Any change to the existing ESPI/EBI announcement pipeline or `x_posts` table
- Inline text editing of thread drafts (re-prompt/regenerate instead)
- Scheduled/automatic runs — this is user-invoked like every other skill
- Deleting screenshots — they're archived, never removed

## Architecture / Approach

Four phases, bottom-up: BigQuery persistence first (both later phases depend on it), then the publisher's media capability (independently testable), then the skill orchestrator split across two phases — extraction/generation first (the riskiest new code), then approval/publish/archive. This sequencing means the vision-extraction code is validated before it's wired to the also-new publish path.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BigQuery table | `portfolio_snapshots` table + insert/query functions | Hand-written SQL bugs only a real round-trip catches (PUL-29 lesson) |
| 2. Publisher media extension | `publish_thread_with_media()` on `XPublisher` | v1.1 OAuth scope for media may differ from v2 tweet-posting scope — needs a real-API check |
| 3. Vision extraction + generation | Skill reads screenshots, extracts data, drafts 2 threads | First vision call in this repo — no existing pattern to lean on |
| 4. Approval + publish + archive | Full end-to-end skill, screenshots archived | Coordinating partial failures (media fails but text succeeds) across BQ + X + filesystem |

**Prerequisites:** Real XTB screenshots for manual testing; confirmation that the existing `X_*` credentials have v1.1 media-upload scope (Phase 2 manual check).
**Estimated effort:** ~4 sessions, one per phase.

## Open Risks & Assumptions

- Assumes the existing `X_*` OAuth 1.0a credentials already have media-upload scope — unverified until Phase 2's manual check.
- Assumes XTB screenshots for a given wallet fit in 1+ images per subfolder, no multi-page stitching logic needed beyond passing all images in a subfolder to one vision call.
- No real BigQuery round-trip happens until Phase 1's manual verification step — until then the schema is only mock-tested.

## Success Criteria (Summary)

- Both threads (main+IKZE, short+long) publish live on X with images attached, after explicit user approval.
- `portfolio_snapshots` gets 4 new rows per successful run, enabling correct day-over-day deltas on the next run.
- Processed screenshots end up archived under `broker_data/archive/<date>/`, never lost or duplicated.
