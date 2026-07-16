#!/usr/bin/env node
import { buildReviewPrompt } from "./input.js";
import { runReview } from "./agent.js";

/**
 * CLI glue: read inputs from env, run the scorer, emit the `SecurityResult` as a
 * single JSON line to stdout for the composite action to capture.
 *
 * Exit codes: 0 on a produced verdict (pass OR fail — the merge gate decides
 * downstream), non-zero only on a technical/agent error so the workflow can
 * fail closed.
 */
async function main(): Promise<void> {
  const title = process.env.PR_TITLE ?? "";
  const body = process.env.PR_BODY ?? "";
  const diffPath = process.env.DIFF_PATH;

  if (diffPath === undefined || diffPath.trim().length === 0) {
    throw new Error("DIFF_PATH env var is required");
  }

  const prompt = buildReviewPrompt({ title, body, diffPath });
  const result = await runReview(prompt);

  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((err: unknown) => {
  const message = err instanceof Error ? (err.stack ?? err.message) : String(err);
  process.stderr.write(`AI security review failed technically: ${message}\n`);
  process.exit(1);
});
