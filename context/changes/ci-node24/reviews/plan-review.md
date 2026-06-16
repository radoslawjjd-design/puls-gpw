<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Bump GitHub Actions to Node 24-compatible versions

- **Plan**: `context/changes/ci-node24/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-16
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 1 critical, 1 warning, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | FAIL → PASS (F1 fixed) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | WARNING → PASS (F2 fixed) |

## Grounding

3/3 paths ✓, 8/8 line refs ✓, deploy trigger master-only ✓, brief↔plan ✓, Progress↔Phase ✓.
Verified safe upstream: `auth@v3` keeps `credentials_json` (using: node24); checkout@v6, setup-gcloud@v3, auth@v3 all publish moving major tags; setup-uv runtimes v6=node20, v7=node24, v8.2.0=node24.

## Findings

### F1 — setup-uv@v8 tag does not exist; the prescribed pin won't resolve

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 1, Contract — `astral-sh/setup-uv@v6 → @v8`
- **Detail**: setup-uv stopped publishing moving major/minor tags at v8. `@v8` does not resolve (only `@v8.2.0` exact, or `@v7`/`@v6`). The plan keeps deploy.yml tag-pinned and prescribed `@v8`, which GitHub cannot resolve — the "Set up uv" step would fail post-merge on the master-only deploy. The plan's automated criteria (YAML parse + old-string absence) both pass on the broken `@v8`, giving a false green.
- **Fix A ⭐ Recommended**: Pin setup-uv to exact `@v8.2.0` + add a "pin resolves" automated criterion.
  - Strength: Truly latest major (node24), honors the "latest majors" decision; closes the false-green gap.
  - Tradeoff: Exact-version pin deviates from the `@vN` convention for this one action; future patch bumps manual.
  - Confidence: HIGH — verified @v8.2.0 exists and is node24; @v8 does not resolve.
  - Blind spot: None significant.
- **Fix B**: Keep setup-uv at `@v7` (still a moving major tag, already node24).
  - Strength: Preserves `@vN` convention; auto-receives v7 patches.
  - Tradeoff: One major behind "latest"; contradicts the version decision.
  - Confidence: HIGH — verified @v7 resolves and is node24.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A — Phase 1 contract pins `@v8.2.0`; Migration Notes document the dropped-major-tag trap; new automated criterion 1.3 (every pin resolves via gh api) + Progress 1.3; manual items renumbered 1.4/1.5.

### F2 — Phase 1's per-phase manual-confirmation pause can't be satisfied in order

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1, Implementation Note
- **Detail**: Phase 1's note told the implementer to pause for human manual confirmation before Phase 2, but Phase 1's manual verification (post-merge Deploy run) can't occur until both phases are merged in one PR. Following it literally stalls the implementer.
- **Fix**: Reword Phase 1's note so the inter-phase gate is automated-only (1.1–1.3) and all manual verification happens together post-merge.
- **Decision**: FIXED — Phase 1 Implementation Note reworded.
