<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: F-02 BigQuery schema + Python client

- **Plan**: context/changes/bigquery-schema/plan.md
- **Scope**: All phases (1–3 of 3)
- **Date**: 2026-06-04
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | WARNING |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — save_analysis silently drops BQ job errors

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:138
- **Detail**: `insert_announcement` checks `job.errors` after `.result()` and raises `RuntimeError`. `save_analysis` calls `.result()` then returns immediately — no error check. If the UPDATE job fails, or if `announcement_id` matches zero rows (stale ID, race), the call returns None silently and the analysis is never persisted.
- **Fix**: Add job.errors check and optional 0-row guard after `.result()`:
  ```python
  if job.errors:
      raise RuntimeError(f"save_analysis failed: {job.errors}")
  if job.num_dml_affected_rows == 0:
      raise RuntimeError(f"save_analysis: no row for {announcement_id!r}")
  ```
  - Strength: Mirrors the pattern already in insert_announcement (line 106).
  - Tradeoff: 2–3 lines added.
  - Confidence: HIGH
  - Blind spot: None significant.
- **Decision**: PENDING

### F2 — with_quota_project() breaks on non-supporting credential types

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality / Scope Discipline
- **Location**: db/bigquery.py:35
- **Detail**: `credentials.with_quota_project()` is not part of the base credentials interface. Raises `AttributeError` on Workload Identity / external-account credentials. Works today on Cloud Run SA, but will break if deployment migrates to Workload Identity.
- **Fix**: Guard with hasattr:
  ```python
  if hasattr(credentials, "with_quota_project"):
      credentials = credentials.with_quota_project(project)
  ```
  - Strength: Local ADC fix still works; Cloud Run with WI skips safely.
  - Tradeoff: On a new cred type, mismatch silently skipped (better than crash).
  - Confidence: HIGH
  - Blind spot: Untested on Workload Identity.
- **Decision**: PENDING

### F3 — Lazy _client singleton not thread-safe

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: db/bigquery.py:22–37
- **Detail**: No lock guards the None-check + init in `_get_client()`. Two concurrent FastAPI threads can both create a client. Not triggered today (sequential pipeline), but AGENTS.md says FastAPI health endpoints are planned.
- **Fix A ⭐ Recommended**: Add threading.Lock around init block.
  - Strength: Minimal change; standard Python singleton pattern.
  - Tradeoff: Negligible lock overhead (acquired once).
  - Confidence: HIGH
  - Blind spot: None significant.
- **Fix B**: Initialise eagerly at module import time.
  - Strength: Simpler; fails fast on bad credentials.
  - Tradeoff: BQ connection at import time; breaks scripts that import without needing BQ.
  - Confidence: MED
  - Blind spot: load_dotenv() ordering interaction not verified.
- **Decision**: PENDING

### F4 — Test cleanup not protected by try/finally

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: scripts/test_bq.py:40–88
- **Detail**: Steps 2–5 not wrapped in try/finally. Exception between insert and delete leaves test record in production table permanently.
- **Fix**: Wrap steps 2–5 in try/finally with DELETE in the finally block.
- **Decision**: PENDING

### F5 — Unplanned scope additions: quota project override + dotenv

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: db/bigquery.py:28–37, main.py:1–5
- **Detail**: Two benign additions not in the plan: `credentials.with_quota_project()` and `load_dotenv()`. Both operationally necessary; plan didn't anticipate ADC quota mismatch.
- **Fix**: No code change needed — accept as discovered-scope.
- **Decision**: PENDING

### F6 — Missing module docstring in db/bigquery.py

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:1
- **Detail**: scripts/research/*.py all have module docstrings. db/bigquery.py has none.
- **Fix**: Add a one-line module docstring at the top.
- **Decision**: PENDING
