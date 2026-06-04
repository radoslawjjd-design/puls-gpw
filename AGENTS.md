# Repository Guidelines

puls-gpw is a scheduled Python pipeline that fetches ESPI/EBI regulatory announcements from GPW and NewConnect, analyses them with a Gemini AI agent and a supervisor, and delivers X-style email summaries to the owner. Stack: Python 3.13 + FastAPI + Pydantic + uv, deployed as Google Cloud Run Jobs.

## Hard Rules

- Never write to `context/archive/` — that directory is immutable. Open new work with the appropriate workflow skill instead.
- Secrets (Gemini API key, SMTP credentials, BigQuery service account) live in environment variables only. Never commit them or paste them in code.
- Destructive infra actions (drop a BigQuery table, delete a Cloud Run job, rotate a primary secret) are human-only — never automate them.
- The supervisor gate is hard: an announcement is discarded and the owner alerted when all 3 retry attempts fail. Do not bypass or soften the gate.
- One announcement, one email — the duplicate check runs before any AI call. Never skip it.

## Project Structure

- `main.py` — pipeline entry point (invoked as a Cloud Run Job, not an HTTP server)
- `pyproject.toml` — dependencies and project metadata; see @pyproject.toml
- `context/foundation/` — PRD, tech-stack, and infrastructure decisions (read-only for agents)
- `.venv/` — managed by uv; never edit manually
- `.env.example` — required environment variables (copy to `.env` for local runs; never commit `.env`)

## Commands

- `uv run python main.py` — run the pipeline locally
- `uv add <package>` — add a runtime dependency
- `uv add --dev <package>` — add a dev-only dependency
- `uv run pip-audit` — scan installed packages for known CVEs
- `uv sync` — recreate `.venv` from `uv.lock` after a git pull

## Coding Conventions

- All package management via `uv`. Never invoke `pip` directly.
- Python 3.13; type annotations required on all public functions and Pydantic model fields.
- AI output schema validated via Pydantic models before the supervisor evaluates the result.
- The pipeline is a script, not a web service. FastAPI's web layer is reserved for future health-check endpoints; do not add HTTP routes in MVP.
- As `main.py` grows, extract pipeline stages into `src/`: `src/scraper.py` (fetch), `src/parser.py` (PDF/HTML), `src/agent.py` (Gemini), `src/supervisor.py` (gate), `src/notifier.py` (email). Keep `main.py` as the orchestration entry point only.
- Pipeline stage failures raise a typed exception (`PipelineStageError` or subclass), log to stderr, and trigger an alert email to the owner. Never swallow exceptions silently.

## Testing

No test framework is configured yet. Add `pytest` via `uv add --dev pytest` and place tests under `tests/` when the first testable unit lands. Run with `uv run pytest`. Test priority: duplicate-check logic first, supervisor retry gate second — these are the highest-risk paths in the pipeline.

## Issue Tracking

Every `context/changes/<change-id>/change.md` must contain a `tracking:` block:

```yaml
tracking:
  linear: PUL-X   # Linear issue ID
  github: N        # GitHub issue number (integer)
```

Use `null` only when no corresponding issue exists. When `/10x-implement` completes the epilogue commit, the agent automatically closes both issues and prints a confirmation block to the console — no manual action needed.

## Commit & Pull Request Guidelines

No commit history exists yet. Use Conventional Commits prefixes (`feat:`, `fix:`, `chore:`, `refactor:`) from the first commit. Non-conforming messages will be flagged in PR review.

## Architecture

See @context/foundation/prd.md for the full pipeline design: scheduler → scraper → PDF/HTML parser → Gemini analysis → supervisor (max 3 retries) → email delivery. See @context/foundation/tech-stack.md for stack decisions.
