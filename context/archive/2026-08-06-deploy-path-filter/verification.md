# Verification — deploy path filter (PUL-122)

Date: 2026-08-06. Branch: `feat/pul-122-deploy-path-filter`.

## Replay of real master history

A Deploy run is triggered per **push**, not per commit, and one first-parent step on
`master` is one push. Counting per commit inflates the win — several of the skipped-looking
commits arrive inside a merge that also carries code, and that push still deploys, which is
correct.

Last 20 pushes to `master`, evaluated against `paths-ignore: ['context/**', '**.md']`:

| | Count |
|---|---|
| Pushes examined | 20 |
| Would now be skipped | **4** |
| Would still deploy | 16 |

The four skipped:

- `7dd9815` — `chore: archive the 2026-08-04 batch` (15 files, all `context/`)
- `3e5b493` — `Close out and archive xlsx-import-size-cap (PUL-105) (#243)` (6 files) — the
  push named in the ticket
- `63b1204` — `chore(archive): close calendar-holdings-as-of-date (#227)` (7 files)
- `d17a439` — `docs(calendar-holdings-as-of-date): phase 5` (2 files)

No push touching `src/`, `db/`, `static/`, `tests/` or `.github/` was skipped, and no
skipped push touched anything outside `context/**`. Both acceptance directions hold on real
history rather than on constructed examples.

20% of production deploys over this window were rebuilding an image that could not have
differed.

## Build integrity without markdown

`docker build` could not be run here — the local Docker daemon is not running — so the step
that could actually have broken was checked directly instead.

The Dockerfile copies only `pyproject.toml` and `uv.lock` before `uv sync --frozen
--no-dev`, and `pyproject.toml` declares `readme = "README.md"`. If the sync resolved that
readme, excluding markdown would break the build. Reproduced the stage exactly — a scratch
directory holding those two files and nothing else:

```
uv sync --frozen --no-dev   → exit 0
```

The readme is not resolved. Beyond that step the change only removes files from `COPY . .`,
and nothing reads a markdown file at runtime (swept every tracked `.py` outside `context/`;
all hits are comments and docstrings).

The real `docker build` runs in CI when this branch reaches `master` — this change touches
`.github/`, `.dockerignore` and `tests/`, so it triggers a deploy itself.

## Guard test is discriminating, not vacuous

Restored `.dockerignore` to the old `README.md`-only rule and re-ran:

```
FAILED test_every_ignored_path_is_one_docker_never_sees
  → deploy.yml skips commits to '**.md', but .dockerignore lets
    ['AGENTS.md', 'CLAUDE.md', 'README.md'] into the build context
```

The test caught a real defect in its own first draft, too: `**/` matches *zero* or more
directories in both dialects, so `**/*.md` covers root-level `README.md`. The first
translator missed that and reported a sound `.dockerignore` as broken.
