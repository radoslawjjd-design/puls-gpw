# X-post Generation + Email Delivery Implementation Plan

## Overview

Build the post-generation pipeline (S-04 + S-05 combined): query top-4 approved announcements
from a time window, generate a 6-tweet X thread via Gemini, validate with a rule-based
supervisor (max 3 attempts), save post_text to BQ, and email the ready-to-copy thread.

Three Cloud Scheduler triggers: 08:30, 13:00, 17:30 Europe/Warsaw. The 13:00 slot silently
no-ops when fewer than 2 approved announcements exist. Also fixes the Gemini trailing-comma
JSON bug as a prerequisite before adding new Gemini calls.

## Current State Analysis

- `db/bigquery.py` — BQ client, schema with `post_text / processed_at / supervisor_attempts`
  already defined (from F-02). `save_analysis_result` DML pattern established.
- `src/analyzer.py` — Gemini client singleton (`_get_client`), `_GEMINI_MODEL` constant;
  uses `json.loads(response.text)` which fails on Gemini trailing commas (~14% rate).
- `src/notifier.py` — `send_alert(exc)` via SMTP/STARTTLS; pattern to reuse for post email.
- `main.py` — scraper pipeline entrypoint; `load_dotenv()` + GCP client pattern established.
- `Dockerfile` — `CMD ["uv", "run", "python", "main.py"]`; post job uses Cloud Run command override.
- `.github/workflows/deploy.yml` — updates `puls-gpw` job; post job needs a second update step.
- `pyproject.toml` — no `json5` yet.

### Key Discoveries

- `lessons.md`: every `json.loads(response.text)` on a Gemini response must use `json5` — fix
  before adding new Gemini calls.
- BQ `ARRAY` parameters via `ArrayQueryParameter("ids", "STRING", list)` + `IN UNNEST(@ids)`
  allow single-query batch UPDATE for all N contributing rows.
- Cloud Run Job command override (`--command uv --args run,python,post_main.py`) avoids
  Dockerfile changes for the second job.
- `src/notifier.py` SMTP pattern (`SMTP_HOST/PORT/USER/PASSWORD/OWNER_EMAIL` env vars) is
  the only approved email channel.

## Desired End State

After `post_main.py` runs:
1. BQ: all N rows contributing to the post have `post_text`, `processed_at`, `supervisor_attempts` filled.
2. Owner receives email: subject `ESPI [Ranek|Południe|Wieczór] YYYY-MM-DD`, body with
   numbered tweets (1/6 … 6/6) ready to copy-paste into X.
3. Cloud Scheduler triggers the job at 08:30, 13:00, 17:30 Europe/Warsaw daily.

## What We're NOT Doing

- Automatic posting to X API (v2+ scope).
- HTML email template (plain text only for MVP).
- Per-announcement `analysis_type` field population (remains NULL; designed for future).
- Image/screenshot generation for tweets.
- Storing posts in a separate `generated_posts` BQ table.
- OCR, new scraper logic, or changes to the 15-min scraper job.

## Implementation Approach

Five sequential phases:

0. **Trailing comma fix** — json5 dependency + replace `json.loads` in `src/analyzer.py`; test coverage.
1. **BQ layer** — `fetch_top_n_for_window()` + `save_post_text()` with tests.
2. **Generator + Supervisor** — `src/post_generator.py` + `src/post_supervisor.py` with unit tests.
3. **Entrypoint + email** — `post_main.py` wiring everything; `send_post_email()` in notifier.
4. **Deployment** — CI/CD update + manual Cloud Run Job + Scheduler provisioning.

## Critical Implementation Details

**Window boundary logic**: all datetimes stored in BQ as UTC. Window bounds computed in
`Europe/Warsaw` then converted to UTC before querying. The `ranek` window spans midnight
(previous day 17:31 Warsaw → today 08:29 Warsaw), so `yesterday` must be computed correctly
when crossing DST boundaries — use `(now - timedelta(hours=15)).date()` never `today - 1`.

**UNNEST batch UPDATE**: BigQuery's `ARRAY` parameter needs `ArrayQueryParameter("ids", "STRING", ids)`
and the query uses `WHERE announcement_id IN UNNEST(@ids)`. A plain `ScalarQueryParameter` list
won't work.

---

## Phase 0: Trailing Comma Fix (json5)

### Overview

Add `json5` to dependencies and replace both `json.loads(response.text)` calls in
`src/analyzer.py` with `json5.loads`. Add one regression test for trailing-comma input.

### Changes Required

#### 1. Add json5 dependency

**File**: `pyproject.toml`

**Intent**: Add `json5` to the `dependencies` list so it's available in the Docker image.

**Contract**: One new entry under `dependencies`: `"json5>=0.9"`.

#### 2. Fix analyzer JSON parsing

**File**: `src/analyzer.py`

**Intent**: Replace `json.loads` with `json5.loads` in `_call_analysis` (line 117) and
`_call_gate` (line 135). Import `json5` at module top.

**Contract**: `import json5` added; both occurrences of `json.loads(response.text)` become
`json5.loads(response.text)`. The stdlib `import json` stays for `json.dumps` on line 184.

#### 3. Regression test for trailing comma

**File**: `tests/test_analyzer.py`

**Intent**: Add one test where the mocked Gemini response has trailing commas and assert
`analyze_announcement` returns a valid result (not None structured_analysis).

**Contract**: New test `test_trailing_comma_json_handled()`. The mock returns
`resp.text = '{"company": "PKO",' + '...,}'` (trailing comma string); assert
`result.structured_analysis is not None`.

### Success Criteria

#### Automated Verification

- `uv run pytest --tb=short -q` — all tests pass, including new trailing comma test
- `uv run python -c "import json5; json5.loads('{\"a\": 1,}')"` — exits 0

#### Manual Verification

- Code review: confirm `json.loads` is gone from `_call_analysis` and `_call_gate`

---

## Phase 1: BQ Aggregation Layer

### Overview

Add two new functions to `db/bigquery.py`: `fetch_top_n_for_window()` reads top-N approved
announcements for a time window; `save_post_text()` batch-updates post fields on all contributing rows.

### Changes Required

#### 1. fetch_top_n_for_window

**File**: `db/bigquery.py`

**Intent**: Query BQ for announcements where `analysis_approved = TRUE` and `published_at`
is within `[window_start, window_end]`, ordered by `analysis_score DESC`, limit `n`.
Return a list of dicts with the fields needed by the post generator.

**Contract**:
```python
def fetch_top_n_for_window(
    window_start: datetime,
    window_end: datetime,
    n: int = 4,
) -> list[dict]:
```
Returns list of dicts with keys: `announcement_id`, `ticker`, `company`, `title`,
`structured_analysis`, `event_type`, `analysis_score`, `url`.
Empty list if none found. Raises `BigQueryError` on query failure.

Uses two `ScalarQueryParameter("TIMESTAMP")` for the range and one for `n`.
SELECT fields: all eight named above.

#### 2. save_post_text

**File**: `db/bigquery.py`

**Intent**: Batch-update `post_text`, `processed_at = CURRENT_TIMESTAMP()`, and
`supervisor_attempts` for all rows in `announcement_ids`. Used after supervisor approves.

**Contract**:
```python
def save_post_text(
    announcement_ids: list[str],
    post_text: str | None,
    supervisor_attempts: int,
) -> None:
```
Uses `ArrayQueryParameter("ids", "STRING", announcement_ids)` and
`WHERE announcement_id IN UNNEST(@ids)`. When `post_text is None`, the BQ UPDATE sets
`post_text = NULL` (records the failed attempt without overwriting a future successful run).
Raises `BigQueryError` on failure.

#### 3. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Add tests for both new functions using the existing `_mock_bq_client` pattern.
`test_fetch_top_n_for_window_returns_rows` — mock returns 2 rows, assert list of dicts returned.
`test_save_post_text_calls_query` — mock BQ client, assert `query()` called with UNNEST pattern.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_bigquery.py --tb=short -q` — all tests pass
- `uv run pytest --tb=short -q` — full suite green

#### Manual Verification

- Spot-check: run `fetch_top_n_for_window` manually against production BQ and verify
  it returns rows ordered by `analysis_score DESC`

---

## Phase 2: Post Generator + Supervisor

### Overview

Two new modules: `src/post_generator.py` calls Gemini with a structured prompt and parses
the JSON thread; `src/post_supervisor.py` applies deterministic rules to validate each attempt.

### Changes Required

#### 0. src/gemini_client.py (refactor, prerequisite)

**Files**: `src/gemini_client.py` (new), `src/analyzer.py` (update import)

**Intent**: Move `_get_client()` singleton and `_GEMINI_MODEL` constant from `src/analyzer.py`
to a new `src/gemini_client.py` so post_generator.py can import from a stable public symbol
rather than a private symbol in a sibling module.

**Contract**: `src/gemini_client.py` exports `get_client() -> genai.Client` (no leading
underscore) and `GEMINI_MODEL: str`. `src/analyzer.py` imports both from `src.gemini_client`
and removes the local definitions. No behavior change.

#### 1. src/post_generator.py

**File**: `src/post_generator.py` (new)

**Intent**: Define a `GeneratedPost` dataclass (list of tweet strings) and a
`generate_post(announcements)` function that calls Gemini with the thread-format prompt
and returns the parsed post. Returns `None` on any failure.

**Contract**:
```python
@dataclass
class GeneratedPost:
    tweets: list[str]  # ordered; tweets[0]=hook, tweets[-1]=summary

def generate_post(announcements: list[dict]) -> GeneratedPost | None:
```

Uses `get_client()` from `src/gemini_client.py` (shared singleton). `GEMINI_MODEL` constant
from same module. `response_mime_type="application/json"`. `json5.loads`.
Expected Gemini output schema: `{"tweets": ["...", "...", ...]}`.

System prompt instructs: thread format (hook tweet + N per-company tweets + summary tweet),
`$TICKER` required per company tweet, `#GPW` + disclaimer (`Nie jest to rekomendacja inwestycyjna.`)
required in last tweet, each tweet ≤280 chars. Few-shot example from `xpost-strategy.md`.

User prompt: JSON serialization of announcements with `ticker`, `company`, `event_type`,
`key_numbers` (extracted from `structured_analysis`), `summary_pl`. Note: `structured_analysis`
arrives from BQ as a STRING — parse it with `json5.loads(row["structured_analysis"])` inside
`generate_post` before extracting `key_numbers` and `summary_pl` fields. Handle parse failure
gracefully (fallback to empty dict, log WARNING).

On failure (exception or JSON parse error): log WARNING, return `None`.

#### 2. src/post_supervisor.py

**File**: `src/post_supervisor.py` (new)

**Intent**: Define a rule-based `validate_post(post, tickers)` that returns `(approved, issues)`.
Purely deterministic — no Gemini call.

**Contract**:
```python
@dataclass
class ValidationResult:
    approved: bool
    issues: list[str]

def validate_post(post: GeneratedPost, tickers: list[str]) -> ValidationResult:
```

Checks (in order):
1. `len(post.tweets) >= 3` — minimum viable thread
2. Each tweet: `len(tweet) <= 280`
3. For each ticker in tickers: `f"${ticker}"` appears in `"\n".join(post.tweets[1:-1])`
4. `"#GPW"` in `post.tweets[-1]`
5. `"rekomendacj"` in `post.tweets[-1].lower()` — disclaimer presence
6. No tweet ends with `"..."` or `"…"` — truncation guard

`approved = True` iff all checks pass. `issues` lists failures.

#### 3. Tests

**Files**: `tests/test_post_generator.py` (new), `tests/test_post_supervisor.py` (new)

**Intent**: Unit tests mocking `src.post_generator._get_client` (same pattern as test_analyzer.py).
Supervisor tests are pure-logic (no mocks needed).

Key test cases for generator:
- Happy path: mock returns valid JSON with 6 tweets → `GeneratedPost` returned
- Gemini exception → `None` returned
- Trailing comma JSON → still parses (json5)
- Missing `tweets` key in response → `None` returned

Key test cases for supervisor:
- Valid 6-tweet post → `approved=True`
- Tweet over 280 chars → `approved=False`, issue listed
- Missing `$TICKER` → `approved=False`
- Missing `#GPW` in last tweet → `approved=False`
- Missing disclaimer → `approved=False`
- Truncated tweet ending `"..."` → `approved=False`

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_post_generator.py tests/test_post_supervisor.py --tb=short -q` — all pass
- `uv run pytest --tb=short -q` — full suite green

#### Manual Verification

- Run `generate_post` manually with 4 real announcements from BQ (fetched via Phase 1 function)
  and print the result; verify tweet structure matches xpost-strategy.md format

---

## Phase 3: Post Pipeline Entrypoint + Email

### Overview

`post_main.py` is the Cloud Run Job entrypoint. It determines the current window, fetches
top-4 from BQ, runs the generate → validate loop (max 3 attempts), saves to BQ, and sends
the email. `src/notifier.py` gains `send_post_email()`.

### Changes Required

#### 1. src/notifier.py — send_post_email

**File**: `src/notifier.py`

**Intent**: Add `send_post_email(window_name, date_str, tweets)` that emails the ready-to-copy
thread. Reuses the existing SMTP setup.

**Contract**:
```python
def send_post_email(window_name: str, date_str: str, tweets: list[str]) -> None:
```
Subject: `f"ESPI {window_name} {date_str}"`.
Body: tweets numbered `f"{i+1}/{len(tweets)}\n{tweet}\n"` separated by `"---\n"`.
Same env vars as `send_alert`. Does not catch exceptions — propagates to caller.

Add also:
```python
def send_no_post_email(window_name: str, date_str: str, reason: str) -> None:
```
Subject: `f"ESPI {window_name} {date_str} — brak posta"`.
Body: reason string (e.g. "Brak zatwierdzonych ogłoszeń w oknie.").

#### 2. post_main.py

**File**: `post_main.py` (new, project root)

**Intent**: Cloud Run Job entrypoint for the post-generation pipeline. Determines the active
time window, fetches top-4 approved announcements, runs generate→validate loop (max 3 attempts),
saves to BQ, sends email. Respects 13:00 no-op rule.

**Contract**: Entry structure mirrors `main.py` (`load_dotenv()` first, then imports,
`configure_logging()`, outer `try/except → send_alert`). Accepts optional `--window
{ranek,poludnie,wieczor}` CLI argument (default: auto-detect from current Warsaw time).

Window boundaries (Europe/Warsaw, converted to UTC for BQ):
- `ranek`: `(yesterday 17:31, today 08:29)`
- `poludnie`: `(today 08:30, today 12:59)`
- `wieczor`: `(today 13:00, today 17:29)`

Auto-detect logic: current Warsaw time determines the window (inclusive upper bounds):
- `<= 08:30` → `ranek`
- `08:31–13:00` → `poludnie`
- `13:01–17:30` → `wieczor`
- `> 17:30` → log WARNING "no active window", sys.exit(0) (no post, no email)

Flow:
```
announcements = fetch_top_n_for_window(start, end, n=4)

if len(announcements) < 2:
    if window != "poludnie":
        send_no_post_email(window_name, date_str, "Brak zatwierdzonych ogłoszeń.")
    return  # silent no-op for poludnie

for attempt in range(1, 4):
    post = generate_post(announcements)
    if post is None:
        continue
    result = validate_post(post, tickers)
    if result.approved:
        save_post_text(ann_ids, full_text, attempt)
        send_post_email(window_name, date_str, post.tweets)
        return

# All 3 attempts failed — record in BQ, then notify
save_post_text(ann_ids, post_text=None, supervisor_attempts=3)
logger.warning("post_main: all 3 supervisor attempts failed for window %s", window)
send_no_post_email(window_name, date_str, "Supervisor odrzucił wszystkie 3 próby.")
```

`full_text` passed to `save_post_text` is `"\n\n".join(post.tweets)`.

### Success Criteria

#### Automated Verification

- `uv run pytest --tb=short -q` — full suite green (no new tests required for post_main;
  all its dependencies are unit-tested in earlier phases)
- `uv run python post_main.py --help` — prints usage without error

#### Manual Verification

- Run `uv run python post_main.py --window ranek` (or `poludnie`/`wieczor`) locally with
  real env vars; verify: BQ rows updated (check `post_text` NOT NULL), email received with
  correct subject and numbered tweets
- Verify 13:00 no-op: query BQ manually to confirm no email sent when poludnie has < 2 results

---

## Phase 4: Deployment

### Overview

Update CI/CD to deploy the post job image. Manually provision the Cloud Run Job and three
Cloud Scheduler triggers (owner action — first-time infrastructure creation).

### Changes Required

#### 1. CI/CD: deploy post job

**File**: `.github/workflows/deploy.yml`

**Intent**: Add a second `gcloud run jobs update` step after the existing one to also update
`puls-gpw-post` with the new image on every master push.

**Contract**: New step after "Update Cloud Run Job":
```yaml
- name: Update Post Cloud Run Job
  run: |
    gcloud run jobs update puls-gpw-post \
      --image="${{ env.IMAGE }}:${{ github.sha }}" \
      --region=${{ env.REGION }} \
      --project=${{ env.PROJECT_ID }}
```
Note: this step will fail until the job is manually created (see Manual Verification).

#### 2. Manual: create Cloud Run Job (human-only, one-time)

**File**: N/A — gcloud command, not automated

**Intent**: Create the `puls-gpw-post` Cloud Run Job pointing to the same image as `puls-gpw`
but with a different CMD override.

**Contract** (command for owner to run):
```bash
gcloud run jobs create puls-gpw-post \
  --image="europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw:latest" \
  --command="uv" \
  --args="run,python,post_main.py" \
  --region=europe-central2 \
  --project=puls-gpw \
  --max-retries=1 \
  --task-timeout=300s \
  --set-secrets=GEMINI_API_KEY=...,SMTP_PASSWORD=...,...
```
(Full secret bindings to match the `puls-gpw` job configuration.)

#### 3. Manual: Cloud Scheduler triggers (human-only, one-time)

**File**: N/A — gcloud commands

**Intent**: Three Cloud Scheduler jobs that invoke `puls-gpw-post` at 08:30, 13:00, 17:30
Europe/Warsaw.

**Contract** (commands for owner to run — one per window):
```bash
# ranek — 08:30, covers previous day 17:31 → today 08:29
gcloud scheduler jobs create http puls-gpw-post-ranek \
  --schedule="30 8 * * 1-5" \
  --time-zone="Europe/Warsaw" \
  --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/puls-gpw/jobs/puls-gpw-post:run" \
  --message-body='{"overrides":{"containerOverrides":[{"args":["run","python","post_main.py","--window","ranek"]}]}}' \
  --oauth-service-account-email=<SA_EMAIL> \
  --project=puls-gpw

# poludnie — 13:00, covers 08:30 → 12:59
gcloud scheduler jobs create http puls-gpw-post-poludnie \
  --schedule="0 13 * * 1-5" \
  --time-zone="Europe/Warsaw" \
  --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/puls-gpw/jobs/puls-gpw-post:run" \
  --message-body='{"overrides":{"containerOverrides":[{"args":["run","python","post_main.py","--window","poludnie"]}]}}' \
  --oauth-service-account-email=<SA_EMAIL> \
  --project=puls-gpw

# wieczor — 17:30, covers 13:00 → 17:29
gcloud scheduler jobs create http puls-gpw-post-wieczor \
  --schedule="30 17 * * 1-5" \
  --time-zone="Europe/Warsaw" \
  --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/puls-gpw/jobs/puls-gpw-post:run" \
  --message-body='{"overrides":{"containerOverrides":[{"args":["run","python","post_main.py","--window","wieczor"]}]}}' \
  --oauth-service-account-email=<SA_EMAIL> \
  --project=puls-gpw
```

### Success Criteria

#### Automated Verification

- `git push` triggers GitHub Actions; both `gcloud run jobs update` steps succeed (after
  manual job creation)

#### Manual Verification

- Manually execute `puls-gpw-post` job in Cloud Run console: verify it completes, BQ updated,
  email received
- Activate each Cloud Scheduler trigger one-by-one; verify end-to-end for each window

---

## Testing Strategy

### Unit Tests

- `tests/test_analyzer.py` — trailing comma regression (Phase 0)
- `tests/test_bigquery.py` — `fetch_top_n_for_window`, `save_post_text` (Phase 1)
- `tests/test_post_generator.py` — happy path, failures, trailing comma, missing key (Phase 2)
- `tests/test_post_supervisor.py` — all 6 validation checks (Phase 2)

### Manual Testing Steps

1. Phase 0: inspect `src/analyzer.py` — confirm `json.loads` removed from `_call_analysis` and `_call_gate`
2. Phase 1: `fetch_top_n_for_window` against production BQ — verify ordering and fields
3. Phase 2: `generate_post` with real announcements — verify thread structure
4. Phase 3: end-to-end local run — BQ updated + email received
5. Phase 4: Cloud Run + Scheduler — each window triggered and validated

## References

- X post strategy: `context/foundation/xpost-strategy.md`
- BQ schema: `db/bigquery.py`
- Notifier pattern: `src/notifier.py`
- Gemini client singleton: `src/gemini_client.py:get_client` (extracted from analyzer.py in Phase 2)
- Lessons: `context/foundation/lessons.md`

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 0: Trailing Comma Fix

#### Automated

- [ ] 0.1 All tests pass including trailing comma regression test
- [ ] 0.2 json5 import smoke-test exits 0

#### Manual

- [ ] 0.3 Code review: json.loads gone from _call_analysis and _call_gate

### Phase 1: BQ Aggregation Layer

#### Automated

- [ ] 1.1 test_bigquery.py tests for fetch_top_n_for_window pass
- [ ] 1.2 test_bigquery.py tests for save_post_text pass
- [ ] 1.3 Full test suite green

#### Manual

- [ ] 1.4 fetch_top_n_for_window spot-checked against production BQ

### Phase 2: Post Generator + Supervisor

#### Automated

- [ ] 2.1 test_post_generator.py — happy path and failure cases pass
- [ ] 2.2 test_post_supervisor.py — all 6 validation checks pass
- [ ] 2.3 Full test suite green
- [ ] 2.5 src/gemini_client.py exists; analyzer.py imports from it; full suite green

#### Manual

- [ ] 2.4 generate_post run manually with real announcements — thread structure verified

### Phase 3: Post Pipeline Entrypoint + Email

#### Automated

- [ ] 3.1 Full test suite green
- [ ] 3.2 python post_main.py --help exits 0

#### Manual

- [ ] 3.3 End-to-end local run: BQ post_text updated, email received with numbered tweets
- [ ] 3.4 13:00 no-op verified when poludnie has < 2 results

### Phase 4: Deployment

#### Automated

- [ ] 4.1 GitHub Actions: both gcloud run jobs update steps succeed

#### Manual

- [ ] 4.2 puls-gpw-post Cloud Run Job manually created and executed successfully
- [ ] 4.3 All 3 Cloud Scheduler triggers activated and verified end-to-end
