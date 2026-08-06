# Stop rebuilding and redeploying on documentation-only commits

## Current state analysis

`.github/workflows/deploy.yml:3-5` triggers on every push to `master` with no path filter.
Every documentation-only commit therefore runs Playwright install, the full suite, a
`docker build`/`docker push`, four `gcloud run jobs update` calls and a `gcloud run deploy`
— producing a new revision of `puls-gpw-api` from an image that behaves identically to the
one already serving.

Verified while scoping this change:

- **`.dockerignore` already excludes `context/`, `tests/`, `.claude/` and `README.md`.**
  This is what makes skipping a deploy *provably* safe rather than probably safe: a commit
  confined to those paths cannot change the image, because those paths never enter the
  build context.
- **Markdown is not fully excluded.** `AGENTS.md` and `CLAUDE.md` reach the image through
  `COPY . .`. Ignoring `**.md` in the workflow without widening `.dockerignore` would skip
  deploys for commits that do change the image — harmlessly today, since nothing reads
  them, but the guarantee would be false rather than merely unexercised.
- **Nothing reads a markdown file at runtime.** Swept every tracked `.py` outside
  `context/`; all `.md` mentions are comments and docstrings. Only four `.md` files are
  tracked outside `context/`: `README.md`, `AGENTS.md`, `CLAUDE.md`, and one under
  `.claude/` (already excluded).
- **`tests.yml` triggers on `pull_request:` only** (`.github/workflows/tests.yml:17-19`), so
  a `paths-ignore` on deploy's *push* trigger cannot weaken PR checks. Same for
  `ai-code-review.yml` and `ai-security-review.yml`.

## Desired end state

A commit touching only `context/**` or markdown does not start the Deploy workflow; a
commit touching `src/`, `db/`, `static/`, `tests/` or a workflow file still does; PR checks
are untouched.

And the property the skip rests on is enforced rather than assumed: **every path pattern
the workflow ignores is a path `.dockerignore` keeps out of the build context.** A future
edit that pulls `context/` or a markdown file into the image would otherwise silently turn
the filter into a source of missed deploys.

## What we're NOT doing

- **Not switching to an allowlist (`paths:`).** A denylist fails safe: a path nobody
  thought about still triggers a deploy. An allowlist fails silent.
- **Not filtering `tests/**`.** It is in `.dockerignore`, so it would be sound — but the
  ticket's acceptance explicitly keeps it deploying, and deploy.yml's own test run is the
  post-merge gate on master. Soundness permits the skip; the acceptance criteria decline it.
- **Not touching `tests.yml`, `ai-code-review.yml`, or `ai-security-review.yml`.**
- **Not adding `concurrency` to deploy.yml.** Unrelated, and a real behaviour change.

## Phase 1: Guard the workflow↔dockerignore invariant

### Changes required

- `tests/test_deploy_workflow_filter.py` (new) — reads the two files as data and asserts:
  - the push trigger carries a `paths-ignore` covering `context/**`;
  - **every** `paths-ignore` pattern is excluded by `.dockerignore` (the soundness
    invariant — the reason this file exists);
  - the deployable trees (`src/`, `db/`, `static/`, `tests/`, `.github/workflows/`) are
    *not* matched by any ignore pattern;
  - `tests.yml` has no `paths`/`paths-ignore` on its `pull_request` trigger.
- `pyproject.toml` — add `pyyaml` to the dev dependency group. Regex-scraping YAML to
  assert a YAML fact is the kind of test that passes while the file means something else.
  Dev-only, so `uv sync --frozen --no-dev` keeps it out of the image.

Written first, against the unmodified `deploy.yml`, so it fails for the right reason.

### Success criteria

#### Automated verification:
- `uv run pytest tests/test_deploy_workflow_filter.py` fails before Phase 2, citing the
  missing `paths-ignore` — not an import error or a bad path.
- `uv run ruff check tests/test_deploy_workflow_filter.py`

## Phase 2: Apply the filter

### Changes required

- `.dockerignore` — replace the `README.md` line with `**/*.md`, so markdown as a class
  stays out of the build context and the invariant holds for the `**.md` pattern.
- `.github/workflows/deploy.yml` — add to the push trigger:
  ```yaml
  paths-ignore:
    - 'context/**'
    - '**.md'
  ```
  with a comment recording *why* the list is what it is, pointing at `.dockerignore` as the
  thing that makes it safe.

### Success criteria

#### Automated verification:
- `uv run pytest tests/test_deploy_workflow_filter.py` — now green.
- `uv run pytest` — full suite green.
- `uv run ruff check .`

#### Manual verification:
- Replay the last 30 commits on `master` against the filter: every commit that touched only
  `context/**` or markdown would have been skipped, and no commit touching `src/`, `db/`,
  `static/`, `tests/` or `.github/` would have been.
- `docker build` still succeeds with markdown excluded from the build context.

## Progress

### Phase 1: Guard the workflow↔dockerignore invariant
#### Automated
- [x] 1.1 Guard test fails against the unmodified workflow, citing the missing `paths-ignore`
- [x] 1.2 `uv run ruff check tests/test_deploy_workflow_filter.py`

### Phase 2: Apply the filter
#### Automated
- [ ] 2.1 `uv run pytest tests/test_deploy_workflow_filter.py` green
- [ ] 2.2 `uv run pytest` — full suite green
- [ ] 2.3 `uv run ruff check .`
#### Manual
- [ ] 2.4 Last 30 master commits replayed against the filter — skips and triggers both correct
- [ ] 2.5 `docker build` succeeds with markdown out of the build context
