import { ToolLoopAgent, Output, stepCountIs } from "ai";
import { createVertex } from "@ai-sdk/google-vertex";
import JSON5 from "json5";
import { ReviewResultSchema, type ReviewResult } from "./schema.js";
import { REVIEW_INSTRUCTIONS } from "./instructions.js";

/**
 * Default model: full Gemini Flash (not flash-lite — code review needs more
 * reasoning than the app's news classification). Swappable via GEMINI_MODEL.
 */
export const DEFAULT_MODEL = "gemini-2.5-flash";

/** Default Vertex region, mirroring `src/gemini_client.py`. */
export const DEFAULT_REGION = "europe-central2";

/**
 * Hard step cap — the lesson's recommended review-session bound and our primary
 * runaway-cost guard. The no-tool scorer finishes in a single step; this is a
 * safety net, not a working limit.
 */
export const STEP_CAP = 8;

/** Resolve the model id once from the environment. */
export function getModelId(): string {
  return process.env.GEMINI_MODEL ?? DEFAULT_MODEL;
}

/**
 * Construct the single-shot scorer: a Vertex-Gemini `ToolLoopAgent` with
 * structured output, no tools, and the step cap. ADC comes from the ambient
 * `GOOGLE_APPLICATION_CREDENTIALS` (set by `google-github-actions/auth` in CI,
 * or `gcloud auth application-default login` locally).
 */
export function createReviewAgent() {
  const vertex = createVertex({
    project: process.env.GOOGLE_CLOUD_PROJECT,
    location: process.env.GOOGLE_CLOUD_REGION ?? DEFAULT_REGION,
  });

  return new ToolLoopAgent({
    model: vertex(getModelId()),
    instructions: REVIEW_INSTRUCTIONS,
    output: Output.object({ schema: ReviewResultSchema }),
    stopWhen: stepCountIs(STEP_CAP),
  });
}

/**
 * Run the review against Vertex Gemini and return the validated result.
 *
 * Defensive json5 fallback: structured output normally parses for us, but if the
 * provider hands back text the SDK can't parse (Gemini's ~14% malformed-JSON
 * rate), retry with a trailing-comma-tolerant parser before failing.
 */
export async function runReview(prompt: string): Promise<ReviewResult> {
  const agent = createReviewAgent();
  const result = await agent.generate({ prompt });

  try {
    return result.output;
  } catch (err) {
    const text = result.text;
    if (typeof text !== "string" || text.trim().length === 0) {
      throw err;
    }
    return ReviewResultSchema.parse(JSON5.parse(text));
  }
}
