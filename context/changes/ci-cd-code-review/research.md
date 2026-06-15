---
date: 2026-06-15T09:40:17+0200
researcher: Radek
git_commit: 70256a34c6b6cbda9bca1b50570acbd3aa796b3b
branch: pul-33-ci-cd-code-review
repository: puls-gpw
topic: "CI/CD AI code-review pipeline — repo fit, CI/CD patterns, Gemini auth, review-criteria grounding"
tags: [research, codebase, ci-cd, github-actions, gemini, vertex-ai, code-review]
status: complete
last_updated: 2026-06-15
last_updated_by: Radek
---

# Research: CI/CD AI code-review pipeline (ci-cd-code-review)

**Date**: 2026-06-15T09:40:17+0200
**Researcher**: Radek
**Git Commit**: 70256a3
**Branch**: pul-33-ci-cd-code-review
**Repository**: puls-gpw

## Research Question

Ground the `ci-cd-code-review` change (`requirements.md`): where a standalone Node/TS review-agent package fits in this Python repo, how the existing CI/CD + secrets are wired, how the project actually authenticates to Gemini (so the agent can "reuse the key"), and which project conventions the review criteria must encode.

## Summary

The repo is a clean Python 3.13 / FastAPI / uv monolith with **zero existing Node infrastructure** — a Node/TS package drops in cleanly under a new `tools/` dir. CI today is a single workflow (`deploy.yml`) that runs **only on push to `master`** (no PR-triggered workflow exists yet — ours is the first), authenticating to GCP via the GitHub secret `puls_gpw_secret` (a service-account JSON).

**The single most important finding — it contradicts `requirements.md`:** the project does **not** use a Gemini API key. Production authenticates to Gemini via **Vertex AI + Application Default Credentials** (`genai.Client(vertexai=True, …)`), i.e. the Cloud Run runner service account. `GEMINI_API_KEY` exists in `.env.example` but is **legacy/unused** by the running code. So "reuse the existing Gemini key, no new vendor/secret" is not literally true. **Good news:** there is a clean honest path — the review agent can authenticate to **Vertex AI Gemini with the *existing* `puls_gpw_secret` SA** (the same secret `deploy.yml` already uses), via the Vercel AI SDK provider `@ai-sdk/google-vertex`. That keeps the "reuse an existing credential, same trust boundary" spirit intact, without minting a Gemini API key.

The 6 review criteria in `requirements.md` map cleanly onto concrete, file-grounded project rules (below); the sharpest project-specific check is the **BigQuery reserved-keyword + mocked-test blind spot**.

## Detailed Findings

### 1. Repo layout — where the Node/TS package goes

- Clean modular Python repo: `src/` (core), `db/` (BigQuery layer), `scripts/` (one-off utilities), `tests/`, `static/`, `data/`, `context/`. **No `tools/` or `packages/` dir yet**, no existing Node anywhere (only `.venv/.../playwright/.../package.json`, transitive). No `.nvmrc`, `tsconfig.json`, `package.json`, lockfile.
- **Recommendation:** put the agent at `tools/ai-code-reviewer/` (new top-level `tools/`). Keep it fully self-contained.
- **Packaging** (`pyproject.toml:1-33`): uv-managed, Python 3.13+, deps incl. `google-genai>=1.0`, `json5>=0.9`, `python-dotenv`; dev: `pytest>=8.0`, `respx`, `pytest-playwright`, `pip-audit`. **Do NOT add Node deps to pyproject**; **do NOT register `tools/` in `tach.toml`** (tach is Python-only). `tach.toml` modules: `main/post_main/api_main → src → db` (known smell: `db` imports `BigQueryError` from `src.exceptions`).
- **`.gitignore`**: ignores `dist/` (line 5, Python), `.venv`, `.env`/`.env.local`, `lessonMarkdawn/`, `oldProjectData/`, `.claude/`, `tach_module_graph.dot`. **Does NOT yet ignore `node_modules/`** → must add scoped Node ignores (`tools/**/node_modules/`, `tools/**/dist/`, `tools/**/*.tsbuildinfo`). Watch the existing top-level `dist/` rule vs Node `dist`.
- **`Dockerfile`**: Python-only (`python:3.13-slim` + uv, `CMD ["uv","run","python","main.py"]`). The agent is CI/dev-only — **no Dockerfile change**; don't co-deploy it into the app image.

### 2. Existing CI/CD + secrets (`.github/workflows/deploy.yml`)

- **Trigger** (`deploy.yml:3-5`): `on: push: branches: [master]` only. **No `pull_request` workflow exists** — `ci-cd-code-review` introduces the first one.
- **Runner**: `ubuntu-latest`. **GCP auth** (`deploy.yml:20-23`): `google-github-actions/auth@v2` with `credentials_json: ${{ secrets.puls_gpw_secret }}` (a service-account JSON — the only repo secret referenced in CI).
- **Steps**: checkout → GCP auth → setup-gcloud → `astral-sh/setup-uv@v6` (Python 3.13) → `uv run playwright install chromium --with-deps` → **`uv run pytest --tb=short`** (line 35-36) → build/push Docker image (tag `github.sha`) to Artifact Registry → update Cloud Run Jobs `puls-gpw` (scraper) + `puls-gpw-post` → deploy Cloud Run Service `puls-gpw-api` (port 8080, SA `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com`, secrets `admin-api-key`/`user-api-key` from Secret Manager, env `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`).
- **Env constants**: `PROJECT_ID: puls-gpw`, `REGION: europe-central2`.
- **No** CODEOWNERS, PR template, ISSUE_TEMPLATE, dependabot, or other workflows.
- **Pattern to mirror for the review workflow**: `pull_request: branches: [master]` + `workflow_dispatch`, `ubuntu-latest`. If using Vertex Gemini, reuse `secrets.puls_gpw_secret` via `google-github-actions/auth@v2` (same as deploy). `fetch-depth: 0` on checkout (needed to compute the diff against base).

### 3. Gemini integration — the auth reality

- **SDK**: `google-genai` (`import google.genai as genai`), pinned `google-genai>=1.0` (`pyproject.toml:15`). Single shared thread-safe client singleton `get_client()` (`src/gemini_client.py:16-27`).
- **AUTH = Vertex AI + ADC** (`src/gemini_client.py:21-25`): `genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location=os.environ.get("GOOGLE_CLOUD_REGION","europe-central2"))`. **No API key read in code.** `GEMINI_API_KEY` (`.env.example:2`) is legacy/unused; the Vertex-mode switch was an explicit past review fix (`context/archive/2026-06-06-ai-analysis-supervisor/reviews/plan-review.md:46-54`).
- **Model**: env `GEMINI_MODEL`, default `gemini-2.5-flash-lite`, read once at import (`src/gemini_client.py:10`; "read once not per-call" was a past impl-review fix).
- **Call sites**: `analyzer.py:147-165` `_call_analysis()`, `analyzer.py:168-184` `_call_gate()`, `post_generator.py:243-264`. All pass `GenerateContentConfig(system_instruction=…, response_mime_type="application/json")`, then **`json5.loads(response.text)`** (trailing-comma tolerance, ~14% malformed rate), then Pydantic validation (`ConfigDict(extra="ignore")`).
- **Implication for the agent**: use Vercel AI SDK `@ai-sdk/google-vertex` (Vertex provider) authenticated with the existing `puls_gpw_secret` SA in GHA → reuses an existing credential, same trust boundary, no Gemini API key minted. Alternative (`@ai-sdk/google`, AI Studio) would require a NEW `GEMINI_API_KEY` secret. **Decision needed in /10x-plan.** Also: bake trailing-comma-tolerant JSON handling into the agent even though it uses structured output.

### 4. Review-relevant conventions → criteria

Maps to `requirements.md` criteria (2 idiomaticity, 4 test-vs-risk, 5 security, 6 BQ safety):

- **Secrets in env only**; **destructive infra (drop BQ table / delete Cloud Run job / rotate primary secret) is human-only, never automated** (`CLAUDE.md:10-11`, `AGENTS.md:10-11`, `infrastructure.md:69`).
- **`.claude/rules/gemini-ai.md`**: every `json.loads()` on a Gemini response MUST use a trailing-comma-tolerant parser (`json5.loads` preferred); flag stdlib `json.loads`; AI output Pydantic-validated before the supervisor.
- **`.claude/rules/db-bigquery.md`** (scoped `db/**/*.py`): `load_dotenv()` before any `db.*` import; new GCP clients need the `with_quota_project` guard (`hasattr`); destructive ops human-only.
- **`.claude/rules/tests.md`**: runner `uv run pytest`, deps via `uv add --dev` (never `pip`); risk-ordered priorities (dedup in `db/bigquery.py`; supervisor 3-retry gate in `src/post_supervisor.py`; Gemini JSON parsing); tests independent.
- **BigQuery reserved-keyword + mocked-test blind spot** (`context/foundation/lessons.md:211-236`): reserved-keyword columns (`window`, `range`, …) MUST be backticked in hand-built SQL; **mocked BQ tests don't verify SQL syntax** (PUL-29 `x_posts.window` bug passed all unit tests, surfaced only on real round-trip). The sharpest, most project-specific review check.
- Other AGENTS.md rules an automated reviewer can encode: uv-only, Python 3.13 + type annotations on public fns/Pydantic fields, Pydantic-before-supervisor, Conventional Commits, no HTTP routes in MVP, typed `PipelineStageError` on stage failures (never swallow), `tracking:` block in every `change.md`, run `tach check`.

### 5. Testing setup (for the test-vs-risk criterion)

- **pytest**, config `pyproject.toml:30-31` (`pythonpath=["."]`). **103 test functions** across `tests/test_{analyzer,api,bigquery,parser,post_generator,post_supervisor,scraper}.py`; **4 e2e** Playwright in `tests/e2e/test_pagination.py` (only `conftest.py`, spins uvicorn on ephemeral port, monkey-patches BQ).
- **`scripts/test_bq.py`**: real-BigQuery round-trip (manual, `uv run python scripts/test_bq.py`, needs ADC). Ensures schema + `ensure_schema_current()`, insert/dedup/save_analysis/save_x_post round-trips (asserts `x_row.window == "poludnie"` — the reserved-keyword path), `finally` cleanup. **Mandatory manual verification for any SQL-touching change** (sibling `scripts/test_alert.py` for logging/email).
- pytest gates deploy on **push to master** — not on PRs (yet).

## Code References

- `src/gemini_client.py:10,16-27` — Gemini singleton, Vertex+ADC auth, `GEMINI_MODEL` default.
- `src/analyzer.py:147-184` — `_call_analysis()` / `_call_gate()`, `json5.loads`, Pydantic.
- `.github/workflows/deploy.yml:3-5,20-23,35-36` — push-only trigger, `puls_gpw_secret` GCP auth, pytest gate.
- `.env.example:2,5,8` — `GEMINI_API_KEY` (legacy/unused), `GOOGLE_CLOUD_PROJECT`, `BIGQUERY_DATASET`.
- `.claude/rules/{gemini-ai,db-bigquery,tests}.md` — enforceable review checks.
- `context/foundation/lessons.md:211-236` — BQ reserved-keyword + mocked-test blind spot.
- `scripts/test_bq.py:39-41,106,128-150` — real-BQ round-trip + cleanup.
- `tach.toml`, `pyproject.toml:1-33`, `.gitignore`, `Dockerfile` — packaging boundaries.

## Architecture Insights

- The repo is greenfield for Node — a `tools/ai-code-reviewer/` package is fully isolated from the Python build/test/deploy paths (no pyproject, tach, or Dockerfile coupling).
- CI is "push-to-master deploys"; there is no PR gate at all today. Adopting PR-flow + this review workflow is the project's first `pull_request` automation.
- Gemini access is uniform and centralized (one singleton, one model constant, json5 everywhere) — easy to mirror conventions, but the auth model (Vertex+ADC, not API key) is the key constraint for the GHA agent.

## Historical Context (from prior changes)

- `context/foundation/infrastructure.md:64-83` — platform decision (Cloud Run + Secret Manager + Artifact Registry); human-only infra ops (line 69); risk register.
- `context/deployment/deploy-plan.md:46,83` — Secret Manager `gemini-api-key`→`GEMINI_API_KEY` (line 46, legacy); "Next steps #4: Wire GitHub Actions CI/CD" (line 83, now `deploy.yml`).
- `context/archive/2026-06-06-ai-analysis-supervisor/{plan.md:231,162; reviews/plan-review.md:46-54; reviews/impl-review.md:52-60}` — Gemini singleton design, `google-genai>=1.0`, Vertex+ADC correction, read-model-once fix.
- `context/archive/2026-06-08-xpost-generation/plan.md:214-239` — extraction of `get_client()`/`GEMINI_MODEL` into `src/gemini_client.py`.
- **No prior Gemini Flash-vs-Pro comparison** — the promptfoo comparison is net-new here.

## Open Questions (for /10x-plan)

1. **Gemini auth path (decision):** Vertex AI via existing `puls_gpw_secret` SA (`@ai-sdk/google-vertex`, no new secret) vs AI Studio API key (`@ai-sdk/google`, new `GEMINI_API_KEY` secret). Recommendation: Vertex + existing SA — honors "no new secret" and mirrors the app. → **`requirements.md` line "reuses existing Gemini API key" needs correcting to "reuses existing GCP SA via Vertex AI".**
2. **Composite action hosting:** start local (`.github/actions/ai-reviewer/`) per the lesson, or separate repo? Lesson recommends local first.
3. **Merge-gate severity:** which score/verdict blocks merge, and at what threshold?
4. **Model for the diff-review vs plan-review (phase 2):** Flash for code review, Pro (or reasoning model) for plan-adherence?
5. **promptfoo provider:** does the eval suite hit Vertex Gemini directly, or via OpenRouter (the lesson's pattern)? Vertex auth in promptfoo CI needs the SA too.

## Related Research

- `context/changes/ci-cd-code-review/requirements.md` — the seed spec (6 criteria, side-effects, behavior, cost controls).
- `context/foundation/lessons.md` — BQ + Gemini priors used throughout.
