# requirements.md — ci-cd-code-review

> Brainstorm note seeding `/10x-research`. Not a spec — it captures intent and the
> subjective review criteria for this team/stack. The exact wording of criteria and
> thresholds will be refined in research/plan.

## Overall concept

- GHA workflow runs for every new pull request to `master` (plus manual `workflow_dispatch` for testing).
- A **composite action** wraps the review agent so the main workflow stays easy to reason about and reusable.
- Agent built with **Vercel AI SDK 6**, model = **Gemini via Vertex AI** (`@ai-sdk/google-vertex`). Auth reuses the **existing GCP service-account secret `puls_gpw_secret`** — the same credential `deploy.yml` already uses — NOT a Gemini API key (the project authenticates to Gemini through Vertex AI + ADC, `genai.Client(vertexai=True, …)`; `GEMINI_API_KEY` in `.env.example` is legacy/unused). No new vendor, no new secret. Model is swappable: Gemini Flash for routine diffs, Gemini Pro for harder ones.
- Standalone Node/TS package living in the repo (puls-gpw is Python; this is a deliberately independent package).

## Input parameters (what the agent sees)

- pull request title
- pull request description (?? cost tradeoff — include for now, drop if noisy/expensive)
- `git diff` against the base branch (requires `fetch-depth: 0`)
- lockfiles / generated artifacts stripped from the diff before the agent reads them (`uv.lock`, `tach_module_graph.dot`, etc.)

## Code Review Criteria

Each criterion is scored on a **1–10** scale (1 = worst, 10 = best). The agent emits a
structured result (`Output.object`): per-criterion scores + a **binding verdict (pass/fail)**
for the whole change + a short Markdown summary usable directly as a PR comment.

1. **Implementation correctness** — does the code do what it claims?
   - 1: logic is wrong or silently breaks existing behavior.
   - 10: correct on the happy path, edge cases, and error handling.
2. **Idiomaticity (Python 3.13 / FastAPI / uv)** — fits language and project conventions.
   - 1: fights the framework, ignores established project patterns.
   - 10: idiomatic, consistent with surrounding code and `CLAUDE.md`/`AGENTS.md` conventions.
3. **Complexity** — simplicity relative to the problem.
   - 1: over-engineered or needlessly convoluted for what it solves.
   - 10: the simplest solution that fully works; no premature abstraction.
4. **Test coverage vs risk** — pytest coverage proportional to the risk of changed paths.
   - 1: risky paths changed with no tests; or tests are mocked so heavily they prove nothing.
   - 10: meaningful tests cover the risk introduced (real round-trips where it matters).
5. **Security & secrets** — no leaks, validation at boundaries.
   - 1: secret committed, or unvalidated external input reaches a sink.
   - 10: secrets stay in env only (Gemini key, SMTP creds, BigQuery SA); inputs validated at system boundaries.
6. **Data & infra safety (BigQuery)** — project-specific failure classes.
   - 1: reserved-keyword columns unescaped, schema not ensured current, or a destructive infra action automated.
   - 10: BQ schema handled safely; no destructive infra (drop table / delete job / rotate secret) automated — those are human-only.

## Parked for later (out of MVP)

- **business alignment** — requires broader context than the diff alone.
- **architectural fit** — requires broader context than the diff alone.
- **plan-adherence review** — compare the PR against its `context/changes/<id>/plan.md`
  using the course skill `/10x-impl-review-ci`. Phase 2: run only when the PR explicitly
  references a plan; consider a separate (cheaper/pricier) model than the code review.

## Expected side-effects

- **PR comment** with the summary + per-criterion scores + verdict.
- **labels:** `ai-cr:failed` (red) OR `ai-cr:passed` (green).

## Expected behavior

- **on-demand re-run** when the label `ai-cr:review` is added.
- **hard merge gate:** the check fails (blocks merge) when verdict = fail, or when a score
  drops below an agreed threshold — turns the "opinion" into a mechanical gate.

## Cost / model controls

- cheap model (Gemini Flash) as default; escalate to Pro only where needed.
- hard step cap (`stepCountIs`) — Vercel AI SDK 6 has no named `maxCost`, so we bound the loop ourselves.
- **promptfoo** eval suite as a regression gate before any prompt change, and to compare models (Flash vs Pro) on the same fixtures.

## Data-risk caveat

The diff is our own repo code, sent read-only to Gemini via Vertex AI. The same GCP
service account (`puls_gpw_secret`) already backs ESPI/EBI analysis in production →
same trust boundary, no new data exposure. No production secrets or customer data flow
to the agent.
