# Portfolio status X-post generator skill Implementation Plan

## Overview

Build a new Claude Code skill, `.claude/skills/portfolio-xpost/SKILL.md`, that reads XTB broker screenshots from `broker_data/<wallet>/`, extracts portfolio data via Gemini vision, computes day-over-day deltas from a new BigQuery table, generates two ready-to-publish X threads (main+IKZE wallets / short+long wallets), gets human approval, publishes with attached screenshots via an extended `src/x_publisher.py`, and archives the processed screenshots.

## Current State Analysis

- `.claude/skills/*/SKILL.md` (26 skills) — consistent structure (frontmatter → Initial Response → numbered Process → guardrails), `AskUserQuestion`-based approval gates. No skill does vision or X publishing today (`context/changes/portfolio-xpost-skill/research.md`, "Skill orchestration conventions").
- `src/x_publisher.py:37-114` — `XPublisher` wraps a single `tweepy.Client` (OAuth 1.0a, API v2), text-only. `publish_thread(tweets: list[str]) -> list[str]` posts a reply-chain; raises `XPublisherError`/`XPublishPartialError` (`src/exceptions.py:32-52`). No `tweepy.API`/`OAuth1UserHandler`/`media_upload` precedent anywhere in the repo.
- `db/bigquery.py:68-186` — established 4-part table pattern (`_X_POSTS_SCHEMA` → `create_x_posts_table_if_not_exists()` → `ensure_schema_current()` → `ensure_x_posts_schema_current()`), plus parameterized-DML insert pattern in `save_x_post()` (`db/bigquery.py:551-616`).
- `src/gemini_client.py:16-27` — text-only `genai.Client(vertexai=True, ...)` singleton. No vision/multimodal call exists in the repo yet.
- `broker_data/` does not exist yet.
- The existing ESPI/EBI pipeline (`post_selection.py` → `post_generator.py` → `post_supervisor.py` → `x_publisher.py`) is a separate system; this plan reuses its conventions (char-limit/cashtag discipline patterns from `post_supervisor.py`) but does not modify it.

## Desired End State

Running `/portfolio-xpost` (or invoking the skill) with screenshots present in `broker_data/main/`, `broker_data/ikze/`, `broker_data/short/`, `broker_data/long/` produces two reviewed, approved, and published X threads with attached screenshots, persists a same-day snapshot row per wallet in `portfolio_snapshots`, and moves the processed screenshots to `broker_data/archive/<date>/`. Verification: query `portfolio_snapshots` for today's 4 rows, check the 2 threads are live on X with images attached, confirm `broker_data/` (minus `archive/`) is empty after a successful run.

### Key Discoveries:

- BigQuery table/insert pattern to mirror exactly: `db/bigquery.py:68-76` (schema), `:131-141` (create), `:144-176` (ensure_schema_current), `:179-186` (binding), `:551-616` (parameterized insert).
- `x_publisher.py` media upload has zero precedent — needs a second auth object (`tweepy.API` + `OAuth1UserHandler`) from the same four `X_*` env vars, additive to the existing `tweepy.Client`.
- No vision call exists — extend `src/gemini_client.py` with a multimodal helper rather than building a parallel client.

## What We're NOT Doing

- Not modifying the existing ESPI/EBI pipeline (`post_selection.py`, `post_generator.py`, `post_supervisor.py`, `post_main.py`) or its `x_posts` table.
- Not building an inline text editor for thread edits — edits go through a re-prompt/regenerate round (per user decision).
- Not auto-creating `broker_data/<wallet>/` subfolders or validating XTB screenshot format beyond "image file present" — the user is responsible for placing correct screenshots.
- Not implementing scheduled/automatic runs — this skill is user-invoked, like every other skill in this repo.
- Not deleting screenshots — they are archived, never removed from disk.

## Implementation Approach

Build bottom-up: persistence (BigQuery) first since both later phases depend on it, then the publisher's media capability (independently testable in isolation), then the skill orchestrator itself in two phases (extraction/generation, then approval/publish/archive) so the riskiest new code (vision extraction) is exercised before the also-new publish path is wired to it.

## Critical Implementation Details

**Media upload fallback ordering**: per-tweet, attempt media upload *before* calling `create_tweet`. If the upload step itself raises, catch it, log a warning, and call `create_tweet` for that tweet **without** `media_ids` (text-only fallback) — do not let a media failure abort the tweet. If `create_tweet` itself then fails, the existing partial/full-failure semantics (`XPublishPartialError`/`XPublisherError`) apply unchanged, since that's a text-publish failure, not a media failure.

**Day-delta query ordering**: when computing day-over-day deltas, query `portfolio_snapshots` for the most recent row per wallet with `snapshot_date < today` (not `<=`) — re-running the skill twice in one day must not compare today's data against itself.

**Per-thread retry semantics**: the two threads (main+IKZE / short+long) are independent units of success — a failure in one must not block or corrupt the other, including on retry. Before halting because a wallet's `broker_data/<wallet>/` subfolder is empty, check `portfolio_snapshots` for a row with `snapshot_date = today` for that wallet: if one exists, treat the wallet as already-published-today and skip it rather than halting; only halt if the subfolder is empty AND no row exists for today. This lets a retry after a partial failure (e.g. thread A succeeded and was archived, thread B failed) process only the thread that still needs it, instead of incorrectly halting on thread A's now-empty, already-archived subfolders.

## Phase 1: BigQuery `portfolio_snapshots` table

### Overview

Add the new table following the exact `x_posts` pattern, plus insert/query functions needed for day-delta computation.

### Changes Required:

#### 1. Schema, table lifecycle, and migration

**File**: `db/bigquery.py`

**Intent**: Add a `portfolio_snapshots` table — one row per wallet per day — mirroring the `x_posts` table lifecycle exactly (schema constant, create-if-not-exists, additive schema migration, thin binding).

**Contract**:
- `_PORTFOLIO_SNAPSHOTS_TABLE_NAME = "portfolio_snapshots"`
- `_PORTFOLIO_SNAPSHOTS_SCHEMA`: `snapshot_id` (STRING, REQUIRED, uuid), `wallet` (STRING, REQUIRED — one of `main`/`ikze`/`short`/`long`), `snapshot_date` (DATE, REQUIRED), `total_value` (FLOAT, REQUIRED), `currency` (STRING, NULLABLE), `day_change_abs` (FLOAT, NULLABLE), `day_change_pct` (FLOAT, NULLABLE), `positions_json` (STRING, NULLABLE — JSON-encoded list of `{ticker, value, pct}`), `created_at` (TIMESTAMP, REQUIRED, server-side `CURRENT_TIMESTAMP()`).
- `create_portfolio_snapshots_table_if_not_exists()` — mirrors `create_x_posts_table_if_not_exists()` (`db/bigquery.py:131-141`).
- `ensure_portfolio_snapshots_schema_current()` — thin binding over `ensure_schema_current()`, mirrors `db/bigquery.py:179-186`.

#### 2. Insert and query functions

**File**: `db/bigquery.py`

**Intent**: Provide `save_portfolio_snapshot()` to insert one wallet/day row, and `get_latest_snapshot_before(wallet, before_date)` to fetch the most recent prior row for delta computation.

**Contract**:
- `save_portfolio_snapshot(wallet: str, snapshot_date: date, total_value: float, currency: str, day_change_abs: float | None, day_change_pct: float | None, positions_json: str | None) -> str` — parameterized INSERT DML following `save_x_post()`'s pattern (`db/bigquery.py:551-616`), generates `snapshot_id` (uuid), returns it. Raises `BigQueryError` on failure.
- `get_latest_snapshot_before(wallet: str, before_date: date) -> dict | None` — parameterized SELECT with `WHERE wallet = @wallet AND snapshot_date < @before_date ORDER BY snapshot_date DESC LIMIT 1`; returns `None` if no prior row exists (first-ever run for that wallet).

### Success Criteria:

#### Automated Verification:
- Unit tests pass: `pytest tests/test_bigquery.py -k portfolio_snapshots`
- Full test suite passes: `pytest`
- Linting passes: project lint command (see `AGENTS.md`)

#### Manual Verification:
- Manually run `create_portfolio_snapshots_table_if_not_exists()` + `ensure_portfolio_snapshots_schema_current()` against the real `puls-gpw` BigQuery dataset and confirm the table appears with the expected schema (per `context/foundation/lessons.md` PUL-29 lesson: mocks don't catch SQL syntax errors, a real round-trip is required before merge).
- Manually insert one row via `save_portfolio_snapshot()` and confirm `get_latest_snapshot_before()` retrieves it correctly for a `before_date` one day later.

---

## Phase 2: `x_publisher.py` media upload extension

### Overview

Add an additive media-upload publish path, independently testable before the skill wires into it.

### Changes Required:

#### 1. Media-capable publish method

**File**: `src/x_publisher.py`

**Intent**: Add `publish_thread_with_media()` alongside the existing `publish_thread()` (unchanged, still used by the ESPI/EBI pipeline) so threads can carry one image per tweet.

**Contract**:
- `XPublisher.__init__` additionally builds a `tweepy.API(tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret))` instance (v1.1, same four creds) for media upload — additive, the existing `tweepy.Client` is unchanged.
- New method `publish_thread_with_media(self, tweets: list[str], media_paths: list[str | None]) -> MediaPublishResult` where `MediaPublishResult` is a small dataclass with `tweet_ids: list[str]` and `media_attached: list[bool]` (parallel arrays, same length as `tweets`). `media_paths[i] is None` means no image for that tweet.
- Per tweet: if `media_paths[i]` is set, call `self._api_v1.media_upload(media_paths[i])` to get a `media_id`; on success pass `media_ids=[media_id]` to `create_tweet` and set `media_attached[i] = True`; on upload failure, log a warning, call `create_tweet` without `media_ids`, set `media_attached[i] = False` (per the agreed fallback-to-text-only behavior — see Critical Implementation Details).
- Reuses the existing reply-chain logic and `XPublisherError`/`XPublishPartialError` semantics for text-publish failures (these are unchanged from `publish_thread`).

#### 2. Tests

**File**: `tests/test_x_publisher.py`

**Intent**: Add a `FakeAPI`/`FakeOAuth1UserHandler` test double (parallel to the existing `FakeClient`) and verify: media upload success passes `media_ids` through; media upload failure falls back to text-only `create_tweet` and reports `media_attached[i] = False`; a `create_tweet` failure after a successful media upload still raises `XPublishPartialError`/`XPublisherError` per existing semantics.

**Addendum (manual-verification feedback, 2026-06-17)**: during Phase 4's live test of the corrected 2-tweet-per-thread format (see Phase 3 Addendum 2), the user pointed out that a wallet's *every* screenshot should attach to its tweet, not just the first one — `media_paths[i]` as a single optional path was too narrow for a multi-screenshot wallet (e.g. `main` had 3 screenshots). Corrected contract, superseding the single-path signature above:

- `publish_thread_with_media(self, tweets: list[str], media_paths: list[list[str]]) -> MediaPublishResult` — `media_paths[i]` is now the **list** of image paths for that tweet (empty list = no images), capped at 4 per tweet (X's limit; excess paths are dropped with a logged warning).
- Per tweet, each path in `media_paths[i]` is uploaded independently; an individual upload failure is logged and that image is skipped (not a full fallback) — the tweet still attaches whichever uploads succeeded. Only if *all* uploads for a tweet fail does it fall back to text-only, mirroring the original single-image fallback semantics. `media_attached[i]` is `True` iff at least one image attached.
- Caller mapping (used by the skill, see Phase 4): for a thread with N wallets and N tweets, `media_paths[i]` = all screenshots discovered for `wallets[i]` in Step 1.1 — tweet *i* and wallet *i* are paired positionally, independent of which wallets' data the tweet's text actually describes (text now spans all wallets per Phase 3 Addendum 2; media attachment stays per-wallet-per-tweet).
- New tests: `test_publish_thread_with_media_multiple_images_per_tweet`, `test_publish_thread_with_media_caps_at_four_images`, `test_publish_thread_with_media_partial_upload_failure_attaches_successful_ones` (in addition to the existing 3, updated for the list-based signature).

### Success Criteria:

#### Automated Verification:
- Unit tests pass: `pytest tests/test_x_publisher.py`
- Full test suite passes: `pytest`

#### Manual Verification:
- Manually confirm the v1.1 `tweepy.API` auth object actually authenticates against the real X API with the project's existing `X_*` credentials (e.g. a one-off `media_upload()` call against a real test image) before relying on it in Phase 4 — OAuth scope for media upload can differ from v2 tweet-posting scope and this is unverifiable from mocks alone.

---

## Phase 3: Skill — vision extraction + thread generation

### Overview

Build the first half of the skill orchestrator: reading screenshots, extracting portfolio data via Gemini vision (with uncertainty flagging), computing deltas, and generating the two thread drafts.

### Changes Required:

#### 1. Vision extraction helper

**File**: `src/gemini_client.py`

**Intent**: Add a multimodal extraction function that reads one or more screenshot images for a wallet and returns structured portfolio data (total value, currency, positions), flagging any field the model could not read with high confidence. Uses a dedicated, more capable model tier than the project's bulk-classification default, since misreading a financial figure is higher-stakes than a missed news classification.

**Contract**: `extract_portfolio_snapshot(image_paths: list[str]) -> PortfolioExtraction` where `PortfolioExtraction` carries the extracted fields plus an `uncertain_fields: list[str]` (empty when extraction was fully confident). Built on the existing `get_client()` singleton (`src/gemini_client.py:16-27`) but with its own model constant — `GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash")` (non-lite; `GEMINI_MODEL`/flash-lite stays untouched for the unrelated ESPI/EBI text pipeline, mirroring the tier-escalation precedent in `tools/ai-code-reviewer/src/agent.ts:8-11`) — using a multimodal `generate_content` call with image parts read from disk and `response_mime_type="application/json"` (same JSON-response convention as `post_generator.py:394-401`); parse with `json5.loads()` per the existing Gemini trailing-comma workaround.

#### 2. Skill orchestrator — Steps 1-2

**File**: `.claude/skills/portfolio-xpost/SKILL.md`

**Intent**: Define the skill's frontmatter and the first half of its Process: discover screenshots per wallet subfolder, run vision extraction per wallet, surface any `uncertain_fields` to the user via `AskUserQuestion` before continuing (per the agreed extraction-uncertainty handling), compute day-over-day deltas via `get_latest_snapshot_before()`, then generate the two thread drafts (main+IKZE / short+long) via a text Gemini call that also enforces the project's existing char-limit/cashtag-style discipline (mirroring `src/post_supervisor.py:32-80`'s validation rules for tweet length, applied to the new thread text).

**Contract**: Follows house `SKILL.md` structure (frontmatter → Initial Response → numbered Process steps → guardrails section), per `.claude/skills/10x-implement/SKILL.md` and `.claude/skills/10x-archive/SKILL.md` conventions identified in research. For each wallet subfolder (`broker_data/main/`, `ikze/`, `short/`, `long/`): if it's empty, check `get_latest_snapshot_before()`-adjacent lookup for a `portfolio_snapshots` row with `snapshot_date = today` for that wallet — if found, skip the wallet as already-published-today; if not found, halt with a clear error (genuinely missing data) rather than generating a partial thread. This makes retries after a partial publish failure (see Phase 4, Critical Implementation Details) resolvable without manual cleanup.

**Addendum (impl-review, 2026-06-17)**: thread drafting does *not* use a Gemini text call. Tweets are composed directly from a fixed template filled with Step 1's already-extracted/delta-computed values, then validated with the reused `post_supervisor.py` rules (length, no-truncation, `_ADVICE_RE`, disclaimer-present). This is intentional — it keeps an LLM from re-touching financial figures that are already extracted and validated, avoiding paraphrase/rounding risk on the exact numbers. The validation-discipline goal of the original contract is met; only the "via a text Gemini call" mechanism differs.

**Addendum 2 (manual-verification gap, 2026-06-17)**: the addendum above shipped a "1 self-contained tweet per wallet" template (a 2-tweet thread = main's tweet + IKZE's tweet, no shared framing). During Phase 4's manual end-to-end test the user rejected this format outright — it did not match the real legacy posting format the user had previously used and referenced when the ticket (PUL-39) was written. The Linear ticket's original description, not fully carried into `research.md`/`plan.md` during planning, specifies a combined-thread format; the user additionally supplied a real example thread (29.05.2026) as ground truth. Corrected contract, superseding the per-wallet-tweet template above:

- **Thread shape stays 2 tweets, but the split is by role, not by wallet**: tweet 1 is a *combined header* covering every wallet in the thread (`main`+`ikze` for Thread A, `short`+`long` for Thread B); tweet 2 is a *combined "Liderzy portfela" (leaders) tweet*, also covering every wallet in the thread. Neither tweet is wallet-exclusive.
- **New extraction fields** (implemented in `src/gemini_client.py`): `total_profit_abs` (wallet-level cumulative PLN profit) and per-position `profit_abs` (PLN profit for that position). `pct` is re-confirmed as the position's own cumulative % return (not a portfolio-allocation share — an earlier misreading during the manual test, corrected after the real example showed `pct` is exactly what the leaders tweet needs).
- **Hard exclusion**: the `Syn2bio`/`SYN2BIO` position is always dropped (zero purchase price makes its % return meaningless) — enforced both in the extraction prompt and defensively in code (`_EXCLUDED_TICKERS` filter in `extract_portfolio_snapshot()`).
- **Leaders selection rule** (user decision): top 3 positions by absolute PLN profit (`profit_abs`), filtered to `profit_abs > 500 zł` — fewer than 3 may qualify; ties broken by extraction order.
- **Cashtag handling** (user decision): the leaders tweet lists multiple tickers in one tweet, which conflicts with the project's standing "max 1 cashtag per tweet → 403" rule (`[[reference-cashtag-per-tweet]]`, `src/post_generator.py:190`). The user confirmed that rule still holds and that the real example's multi-`$cashtag` tweet was published via a different method outside this project. Resolution: the leaders tweet uses **plain tickers** (`XTB`, not `$XTB`) — no cashtag formatting at all in that tweet.
- **Polish number formatting**: comma decimal separator, space thousand separator (e.g. `54 442,99 zł`) — implemented as `format_pln()`.
- **🚀 emoji rule**: cumulative % return > 100% on a leader line, or day-over-day % change > 5% on the header's per-wallet delta line (only the cumulative threshold has real data to trigger on today, since day-over-day deltas require a prior snapshot).
- **Disclaimer/hashtags placement**: only the leaders (closing) tweet carries the disclaimer sentence and hashtags (`#GPW`, plus `#IKZE` when the IKZE wallet is in the thread) — the header tweet carries neither, matching the real example.
- New module `src/portfolio_thread_composer.py` (`WalletThreadData`, `format_pln()`, `compose_portfolio_thread() -> [header_tweet, leaders_tweet]`) implements all of the above as pure, unit-tested formatting/selection logic — kept out of any Gemini call for the same paraphrase/rounding-risk reason as Addendum 1. Covered by `tests/test_portfolio_thread_composer.py` (8 tests) and 2 new tests in `tests/test_gemini_client.py` for the new extraction fields and the Syn2bio exclusion.
- **Explicitly deferred, not implemented in this correction** (real gaps, not blocking the corrected format from shipping): the "Doładowanie" deposit-narrative line (detecting new buys by diffing today's positions against the prior snapshot), the `sum(positions) + free_cash == total_value` cross-check from the original ticket, and confirmed `short`/`long` wallet emoji/labels (no real example exists for that thread yet — current labels in `WALLET_LABELS` are best-effort guesses). Also unresolved: the ticket's archive path was `broker_data_archived/`, while this plan's Phase 4 contract (and the shipped code) use `broker_data/archive/<date>/<wallet>/` — not reconciled, flagging for visibility only since the implemented path is already in production use.

### Success Criteria:

#### Automated Verification:
- Unit tests pass: `pytest tests/test_gemini_client.py` (extraction parsing, uncertain-field flagging, mocked `genai.Client` per the agreed mock-only testing strategy; the whole file is portfolio-only, no `-k` filter needed)
- Full test suite passes: `pytest`

#### Manual Verification:
- Run the skill's extraction step against a real set of XTB screenshots placed in `broker_data/<wallet>/` and manually confirm the extracted balances/positions match what's visible in the screenshots.
- Manually verify the uncertainty flag triggers correctly on a deliberately blurry/cropped test screenshot.

---

## Phase 4: Skill — approval gate, publish, archive

### Overview

Complete the skill orchestrator: present the two drafts for approval, publish with attached screenshots, persist the snapshot rows, and archive the processed images.

### Changes Required:

#### 1. Skill orchestrator — Steps 3-5

**File**: `.claude/skills/portfolio-xpost/SKILL.md`

**Intent**: Add the approval gate, publish step, and archive step to complete the Process section.

**Contract**:
- Approval gate: `AskUserQuestion` with three options — "Zatwierdź" (proceed to publish), "Edytuj" (collect free-text refinement, re-run thread generation from Phase 3), "Anuluj" (stop, screenshots remain in `broker_data/`, nothing published or archived) — per the agreed approval-gate decision. The approval gate runs once per thread, not once for both — approving thread A and editing thread B (or vice versa) must be possible independently.
- The two threads are processed as fully independent units end to end (publish → persist → archive), per the Critical Implementation Details "Per-thread retry semantics" note: a failure publishing thread B must not affect thread A's already-completed persist/archive, and must leave thread B's wallets retryable on the next run.
- Publish step: call `get_x_publisher().publish_thread_with_media(tweets, media_paths)` once per thread (the relevant wallet screenshot(s) attach to the thread's first tweet); on `media_attached[i] = False` for any tweet, continue (already-agreed fallback) but record that status. If the thread's publish raises `XPublisherError`/`XPublishPartialError` (full or partial text-publish failure), skip that thread's persist/archive steps entirely — its wallet screenshots remain in `broker_data/<wallet>/` for the next run to pick up.
- Persistence step: for each wallet in a thread that published successfully, call `save_portfolio_snapshot()` with the extracted/delta-computed values; additionally record the media-attachment outcome (e.g. as part of `positions_json` metadata or a dedicated note — implementer's choice, must be queryable after the fact) so a degraded (text-only) publish is visible in BigQuery per the agreed "oznacz w logu/BQ" decision.
- Archive step: for each wallet in a thread that published successfully, move its screenshot file(s) from `broker_data/<wallet>/` to `broker_data/archive/<YYYY-MM-DD>/<wallet>/` (per the agreed archive decision). A thread that failed to publish keeps its wallets' screenshots in place, untouched, for retry — see Phase 3's per-wallet "already published today" check for how the retry run resolves which wallets still need processing.

**Addendum (manual-verification scope decision, 2026-06-17)**: 4.3 (happy path) was verified with a real live run — Thread A (main+ikze) drafted, approved, published (2 tweets, real X ids, images attached), persisted to `portfolio_snapshots`, screenshots archived to `broker_data/archive/2026-06-17/`. Thread B (short+long) was not run live — those folders have no screenshots yet — but the user accepted Thread A's round-trip as sufficient evidence since Thread B exercises identical code with different input data.

4.4/4.5/4.6 (the three failure-injection paths) were **not** verified via additional live X posts, by user decision — triggering them for real would mean posting extra real, public, irreversible tweets to a production account purely to observe a failure path. Verified instead via:
- **4.4 (Anuluj)**: logic review of `.claude/skills/portfolio-xpost/SKILL.md` Step 3.4 — explicit instruction to skip Step 4 and Step 5 entirely on Anuluj, leaving screenshots untouched and nothing published/persisted. No code path exists for this branch (it's orchestration prose followed by the invoking agent), so a live run would only re-confirm the same text.
- **4.5 (media-upload-failure fallback)**: covered by `tests/test_x_publisher.py::test_publish_thread_with_media_upload_failure_falls_back_to_text_only` and `test_publish_thread_with_media_partial_upload_failure_attaches_successful_ones` — both assert the exact fallback mechanics (failed upload skipped, tweet still posts, `media_attached` reflects degraded status).
- **4.6 (partial-failure retry)**: covered by `tests/test_x_publisher.py::test_partial_failure_carries_published_ids` (and the media variant `test_publish_thread_with_media_create_tweet_failure_after_media_upload_raises`) for the `XPublishPartialError`/`XPublisherError` raise semantics, combined with SKILL.md Step 4.3's documented "skip persist/archive, leave screenshots for retry" instruction and Step 1.2's "already published today" check that makes the retry resolve only the still-failing thread.

### Success Criteria:

#### Automated Verification:
- Full test suite passes: `pytest`
- Linting passes: project lint command

#### Manual Verification:
- End-to-end manual run: place real screenshots in all 4 `broker_data/<wallet>/` subfolders, invoke the skill, approve both drafts, confirm both threads are live on X with images attached, confirm 4 new rows in `portfolio_snapshots`, confirm screenshots moved to `broker_data/archive/<today>/`.
- Manually test the "Anuluj" path: confirm nothing is published, no BigQuery rows written, and screenshots remain in their original subfolders.
- Manually test the media-upload-failure fallback path (e.g. by temporarily pointing at an invalid image) and confirm the tweet still publishes text-only and the degraded status is visible in BigQuery.
- Manually test the partial-failure retry path: force thread B's publish to fail (e.g. temporarily break its credentials) after thread A succeeds; confirm thread A's wallets are archived/persisted, thread B's wallets are untouched, and re-running the skill processes only thread B (does not halt on thread A's now-empty subfolders).

---

## Testing Strategy

### Unit Tests:
- `portfolio_snapshots` schema/create/migration functions, mocked `_get_client()` (mirrors `tests/test_bigquery.py:191-227`).
- `save_portfolio_snapshot()` / `get_latest_snapshot_before()` — assert on `client.query.call_args` for correct SQL and parameter binding.
- `publish_thread_with_media()` — `FakeAPI`/`FakeClient` doubles for both the success path and the media-upload-failure fallback path.
- `extract_portfolio_snapshot()` — mocked `genai.Client`, fixed JSON responses covering both confident and uncertain-field cases.

### Integration Tests:
- None planned beyond the manual end-to-end runs in Phase 4 — this skill has no automated integration test harness (consistent with the rest of the project's skill-level workflows, which are manually invoked and manually verified).

### Manual Testing Steps:
1. Full happy-path run with real screenshots (Phase 4 manual verification).
2. "Anuluj" path — confirm no side effects.
3. Media-upload-failure fallback path — confirm degraded-but-successful publish.
4. Uncertain-extraction path — confirm the user is prompted before thread generation proceeds.

## Performance Considerations

None — this is a low-frequency, user-invoked skill (not a hot path); vision/LLM call latency is acceptable for an interactive workflow.

## Migration Notes

`portfolio_snapshots` is a brand-new table; no existing data to migrate. `ensure_portfolio_snapshots_schema_current()` is safe to call on every skill invocation (idempotent, additive-only), matching the existing `x_posts` convention.

## References

- Related research: `context/changes/portfolio-xpost-skill/research.md`
- Related frame: `context/changes/portfolio-xpost-skill/frame.md`
- BigQuery pattern: `db/bigquery.py:68-186,551-616`
- Publisher pattern: `src/x_publisher.py:37-114`
- Test doubles pattern: `tests/test_x_publisher.py:1-139`, `tests/test_bigquery.py:191-227`
- Gemini text-call pattern: `src/post_generator.py:310-424`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery portfolio_snapshots table

#### Automated
- [x] 1.1 Unit tests pass: `pytest tests/test_bigquery.py -k portfolio_snapshots` — cc801b1
- [x] 1.2 Full test suite passes: `pytest` — cc801b1
- [x] 1.3 Linting passes — cc801b1

#### Manual
- [x] 1.4 Real-BigQuery round-trip: table creation + schema verified — cc801b1
- [x] 1.5 Manual insert + get_latest_snapshot_before round-trip verified — cc801b1

### Phase 2: x_publisher.py media upload extension

#### Automated
- [x] 2.1 Unit tests pass: `pytest tests/test_x_publisher.py` — 2fe6468
- [x] 2.2 Full test suite passes: `pytest` — 2fe6468

#### Manual
- [x] 2.3 Real X API v1.1 media_upload auth verified with existing credentials — 2fe6468

### Phase 3: Skill — vision extraction + thread generation

#### Automated
- [x] 3.1 Unit tests pass: `pytest tests/test_gemini_client.py` — 8805e44
- [x] 3.2 Full test suite passes: `pytest` — 8805e44

#### Manual
- [x] 3.3 Real screenshot extraction manually verified against actual XTB screenshots — 8805e44
- [x] 3.4 Uncertainty flagging manually verified on a deliberately ambiguous screenshot — 8805e44

### Phase 4: Skill — approval gate, publish, archive

#### Automated
- [x] 4.1 Full test suite passes: `pytest` — ed5d74d
- [x] 4.2 Linting passes — ed5d74d

#### Manual
- [x] 4.3 End-to-end happy path: both threads published with images, 4 BQ rows written, screenshots archived — ed5d74d
- [x] 4.4 "Anuluj" path verified: no side effects — ed5d74d
- [x] 4.5 Media-upload-failure fallback path verified: degraded publish succeeds, status visible in BQ — ed5d74d
- [x] 4.6 Partial-failure retry path verified: thread A's success unaffected by thread B's failure, retry resolves only thread B — ed5d74d
