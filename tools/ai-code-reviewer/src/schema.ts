import { z } from "zod";

/**
 * Per-criterion score: integer 1–10 (1 = worst, 10 = best).
 * Criterion keys map 1:1 to requirements.md "Code Review Criteria" 1–6.
 */
const scoreField = z.number().int().min(1).max(10);

/**
 * The structured result the agent must emit via `Output.object`.
 * This is the contract the whole merge gate stands on: the gate reads
 * `verdict` and the minimum of `scores`, never the free-text `summary`.
 */
export const ReviewResultSchema = z.object({
  scores: z.object({
    /** 1. Implementation correctness — does the code do what it claims? */
    correctness: scoreField,
    /** 2. Idiomaticity (Python 3.13 / FastAPI / uv) — fits language + project conventions. */
    idiomaticity: scoreField,
    /** 3. Complexity — simplicity relative to the problem; no premature abstraction. */
    complexity: scoreField,
    /** 4. Test coverage vs risk — pytest coverage proportional to risk; mocks that prove nothing score low. */
    testCoverageVsRisk: scoreField,
    /** 5. Security & secrets — no leaks; validation at boundaries. */
    security: scoreField,
    /** 6. Data & infra safety (BigQuery) — reserved-keyword columns, schema currency, no automated destructive infra. */
    dataInfraSafety: scoreField,
  }),
  /** Binding verdict for the whole change. */
  verdict: z.enum(["pass", "fail"]),
  /** Markdown summary usable directly as a PR comment. */
  summary: z.string(),
});

export type ReviewResult = z.infer<typeof ReviewResultSchema>;
