import { z } from "zod";

/** Per-criterion score: integer 1–10 (1 = worst, 10 = best). */
const scoreField = z.number().int().min(1).max(10);

/**
 * The structured result the agent must emit via `Output.object`.
 * The gate reads `verdict` and the minimum of all 5 `scores`;
 * criterion names appear as column headers in the PR comment.
 */
export const SecurityResultSchema = z.object({
  scores: z.object({
    /** 1. Hardcoded API keys, tokens, or service-account JSON in any form. */
    secretsLeakage: scoreField,
    /** 2. SQL/command/prompt/template injection via user-controlled input. */
    injectionRisk: scoreField,
    /** 3. Missing validation at FastAPI/system boundaries before reaching sinks. */
    inputValidation: scoreField,
    /** 4. Unpinned deps, packages added outside uv add, or known-CVE packages. */
    dependencySafety: scoreField,
    /** 5. Unprotected routes, permission regressions, privilege escalation. */
    authPermissions: scoreField,
  }),
  /** Binding verdict for the whole change. */
  verdict: z.enum(["pass", "fail"]),
  /** Markdown summary usable directly as a PR comment. */
  summary: z.string(),
});

export type SecurityResult = z.infer<typeof SecurityResultSchema>;
