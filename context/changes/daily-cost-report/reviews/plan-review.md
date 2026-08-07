<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Daily Cost Report

- **Plan**: `context/changes/daily-cost-report/plan.md`
- **Mode**: Deep
- **Date**: 2026-08-07
- **Verdict**: REVISE → **SOUND** after triage (all 6 findings fixed)
- **Findings**: 0 critical, 3 warnings, 3 observations

## Verdicts

| Dimension | Verdict | After fixes |
|-----------|---------|-------------|
| End-State Alignment | WARNING (F1, F3) | PASS |
| Lean Execution | PASS | PASS |
| Architectural Fitness | WARNING (F4, F5) | PASS |
| Blind Spots | WARNING (F6) | PASS |
| Plan Completeness | WARNING (F2) | PASS |

## Grounding

9/9 paths ✓, 6/6 symbols ✓, brief↔plan ✓, Progress↔Phase 37/37 bullets ✓

Verified clean and reported as non-findings:

- Adding a 5th `gcloud run jobs update` step and a new root-level `cost_report_main.py` leaves all
  four tests in `tests/test_deploy_workflow_filter.py` passing — none of them enumerates or counts
  deploy steps or job names, and `_MUST_DEPLOY` does not cover root-level files.
- No other test in `tests/` reads `.github/workflows/deploy.yml` or asserts on job names
  (grep for `puls-gpw-post`, `puls-gpw-company-stats`, `puls-gpw-etf-quotes`, `gcloud` → zero hits).
- Keeping `get_billing_rows` and `get_daily_gross` as two functions is justified, not redundant:
  the baseline window can start in the previous month while the month-to-date window cannot, so
  merging them would require a `min(month_start, D-7)` bound and introduce a trap rather than
  remove one.

## Findings

### F1 — Vertex per-model token split has no defined mapping, and the real SKU set defeats the obvious parse

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 2 — Report logic
- **Detail**: `CostReport.vertex_models` promises `input_tokens` and `output_tokens`, but the only
  mapping function defined was `map_sku_to_model(sku) -> str | None`, returning a model and no
  direction. Three traps the plan had not named: Flash GA has **two** output SKUs, one of which
  ends in `)` so `desc.endswith("Output")` drops it; and `"Flash GA / Lite input caching"` spans
  both models and cannot be attributed by matching a model name.
- **Fix A ⭐ Recommended**: One classifier returning `(model, direction)`.
  - Strength: Keeps the input/output split — the diagnostic PUL-69 actually used, where input and
    output rising together means more documents and input alone means a retry loop.
  - Tradeoff: Needs an explicit decision on the shared caching SKU.
  - Confidence: HIGH — all six SKU strings are recorded verbatim in the PUL-69 findings.
  - Blind spot: New SKUs ship with new model versions; unmatched SKUs need a defined fallback.
- **Fix B**: Report one token total per model, no direction.
  - Strength: Removes the whole class of mis-bucketing.
  - Tradeoff: A spike becomes uninterpretable — you see that it grew, not what shape it had.
  - Confidence: HIGH — strictly less logic.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A — `classify_sku(sku) -> tuple[str, str] | None`; the shared caching
  SKU gets its own row and unmatched SKUs fall into `"other"`, so per-model gross reconciles with
  the Vertex service line. Added a reconciliation success criterion and a table-driven test naming
  all six strings.

### F2 — Phase 3 needs `base_url`; no phase supplies it

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phases 3, 4, 5
- **Detail**: The renderer resolves the logo against `{base_url}`, but Phase 4's contract never
  mentioned it and Phase 5's env list omitted it. `src/notifier.py` has no internal default —
  `base_url` is a required positional on every existing renderer — and the only default in the repo
  is `_DEFAULT_BASE_URL` at `main.py:41`, private to an entry point that runs `load_dotenv()` and a
  large `db.bigquery` import at import time, so it cannot be imported. `APP_BASE_URL` also appears
  nowhere in `deploy.yml`; it is set out-of-band on the scraper job.
- **Fix**: Define `DEFAULT_BASE_URL` in `src/cost_report.py` (importable, unlike `main.py`'s), read
  `APP_BASE_URL` over it in `cost_report_main.py`, and add `APP_BASE_URL` to the create-time env in
  the Phase 5 runbook.
- **Decision**: FIXED

### F3 — The "baseline building" message was decided but never entered a phase contract

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: End-State Alignment
- **Location**: Phase 3 — Mail rendering
- **Detail**: The agreed behaviour below 4 baseline days was that the mail still goes out but says
  the baseline is building. Phase 2 carried `baseline_days`; Phase 3's contract never rendered it,
  so a suppressed flag would read identically to a calm day — the ambiguity the daily cadence
  exists to remove.
- **Fix**: Add the line to the Phase 3 contract plus a success criterion asserting it appears when
  `median_7d` is `None`.
- **Decision**: FIXED

### F4 — Import-time env read contradicts Phase 2's own framing

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 2
- **Detail**: Phase 2 opens "pure functions… no clock beyond an injected date", then placed
  `_ANOMALY_FACTOR = float(os.environ.get(...))` at module level. Verified: no test in the repo
  overrides an env-derived module constant and `importlib.reload` is used nowhere, so the env read
  itself would be untestable.
- **Fix**: Make the factor a parameter of `build_report`, with the env read at the call site.
- **Decision**: FIXED

### F5 — Passing `CostReport` into notifier breaks a real invariant

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 3
- **Detail**: `src/notifier.py` imports only stdlib today — zero `src.*`, zero `db.*` — and all six
  existing senders take primitives. A `CostReport` parameter would be the module's first
  project-domain import.
- **Fix**: Sender and renderer take primitives; the caller unpacks the dataclass.
- **Decision**: FIXED

### F6 — "Linting passes" is a local gate, not a CI gate

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phases 1, 4, 5 — success criteria
- **Detail**: Phase 4 claimed `uv run ruff check .` "proves the E402 entry is present". Verified:
  ruff runs in no workflow — `tests.yml` and `deploy.yml` both run only `uv run pytest` — so a
  missing entry would not block a merge.
- **Fix**: Keep the criteria, drop the "proves" claim.
- **Decision**: FIXED
