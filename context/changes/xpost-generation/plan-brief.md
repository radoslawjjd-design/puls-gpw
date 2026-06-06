# X-post Generation + Email Delivery — Plan Brief

> Full plan: `context/changes/xpost-generation/plan.md`
> X post strategy: `context/foundation/xpost-strategy.md`

## What & Why

Build the post-generation pipeline (S-04 + S-05 combined): generate a 6-tweet X thread
from top-4 approved announcements per time window, validate with a rule-based supervisor,
and email the ready-to-copy thread to the owner. This is the first fully automated
end-to-end run of the pipeline — from new ESPI on Bankier.pl to an email in the owner's inbox.

## Starting Point

BQ already stores `analysis_score`, `analysis_approved`, `structured_analysis` (per S-03).
Schema has `post_text / processed_at / supervisor_attempts` columns (added in F-02, still NULL).
Gemini client singleton and SMTP notifier exist and are reusable. `json.loads` on Gemini output
has a known ~14% trailing-comma failure rate (lessons.md) — fixed in Phase 0.

## Desired End State

Three times daily (08:30, 13:00, 17:30 Europe/Warsaw) a Cloud Run Job runs, picks the top-4
approved announcements for that window, generates a 6-tweet thread via Gemini, validates it
(max 3 attempts), saves it to BQ, and emails the owner a numbered ready-to-copy thread.
The 13:00 slot silently no-ops when fewer than 2 approved announcements exist.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Top-N companies per post | 4 | 6-tweet thread (hook+4+summary) fits X sweet spot (3–7 tweets = 3× impressions) | Plan |
| Posting schedule | 08:30 / 13:00 / 17:30 Warsaw | 2 guaranteed + 1 bonus; 3 full threads avoids "author diversity penalty" | Plan |
| 13:00 threshold | ≥2 approved, else no-op | Bonus window — don't email for sparse sessions | Plan |
| post_text storage | All N contributing BQ rows | Zero schema change; UNNEST batch UPDATE | Plan |
| S-04 + S-05 coupling | Same Cloud Run Job | Simpler for MVP; no inter-job coordination needed | Plan |
| Email format | Plain text, numbered tweets | Copy-paste to X without editing | Plan |
| Trailing comma | json5 in this change | Applies to all Gemini calls (lessons.md rule) | Plan |
| Cloud Run deployment | New job `puls-gpw-post` + CMD override | Isolates post pipeline from scraper | Plan |

## Scope

**In scope:**
- json5 fix for analyzer.py (Phase 0)
- `fetch_top_n_for_window()` + `save_post_text()` BQ functions
- `src/post_generator.py` — Gemini thread generation
- `src/post_supervisor.py` — deterministic rule validation (≤280 chars, $TICKER, #GPW, disclaimer)
- `post_main.py` — entrypoint with window detection, 3-attempt loop, email
- `src/notifier.py` — `send_post_email()` + `send_no_post_email()`
- CI/CD update + manual Cloud Run Job + 3 Cloud Scheduler triggers
- Unit tests for all new modules

**Out of scope:**
- Automatic X posting
- HTML email template
- `analysis_type` field population
- Image generation for tweets
- Separate `generated_posts` BQ table

## Architecture / Approach

```
Cloud Scheduler (08:30 / 13:00 / 17:30 Warsaw)
    → Cloud Run Job puls-gpw-post
        → post_main.py
            → fetch_top_n_for_window(BQ)  → list[dict] (top-4 approved)
            → loop max 3×:
                → generate_post(Gemini)    → GeneratedPost | None
                → validate_post(rules)     → ValidationResult
            → save_post_text(BQ)           → N rows updated
            → send_post_email(SMTP)        → owner inbox
```

Same Docker image as `puls-gpw` scraper; `puls-gpw-post` job overrides CMD to `post_main.py`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 0. Trailing comma fix | json5 + analyzer.py fix + regression test | Minimal — well-understood fix |
| 1. BQ layer | fetch_top_n_for_window + save_post_text | UNNEST array parameter syntax |
| 2. Generator + Supervisor | Gemini thread gen + rule validation | Gemini prompt quality → supervisor pass rate |
| 3. Entrypoint + email | post_main.py + send_post_email | Window boundary logic (DST, midnight crossing) |
| 4. Deployment | CI/CD + Cloud Run Job + Scheduler | Manual infra provisioning steps |

**Prerequisites:** S-03 done ✓ (analysis_score + analysis_approved in BQ), F-03 done ✓ (SMTP notifier)
**Estimated effort:** ~3-4 sessions across 5 phases

## Open Risks & Assumptions

- Gemini prompt quality for thread generation is unknown — may need iterative refinement after
  first real runs (supervisor pass rate target: ≥1/3 attempts succeed).
- Cloud Run Job `puls-gpw-post` must be manually created before CI/CD deploy step works;
  Phase 4 includes the `gcloud` command for the owner.
- `structured_analysis` JSON in BQ may have fields missing (trailing comma rows from S-03 era
  were skipped — only approved rows matter here, and approved rows went through full Gemini call).

## Success Criteria (Summary)

- Owner receives email with numbered tweets at 08:30 and 17:30 daily, ready to copy-paste into X
- BQ rows for contributing announcements have `post_text` NOT NULL after each run
- 13:00 slot emails only when there are ≥2 approved announcements, silent otherwise
