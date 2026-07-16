import { ToolLoopAgent, Output, stepCountIs, NoObjectGeneratedError } from "ai";
import { createVertex } from "@ai-sdk/google-vertex";
import JSON5 from "json5";
import { SecurityResultSchema, type SecurityResult } from "./schema.js";
import { SECURITY_REVIEW_INSTRUCTIONS } from "./instructions.js";

/**
 * Default model: full Gemini Flash (not flash-lite — security review needs more
 * reasoning than the app's news classification). Swappable via GEMINI_MODEL.
 */
export const DEFAULT_MODEL = "gemini-2.5-flash";

/** Default Vertex region, mirroring `src/gemini_client.py`. */
export const DEFAULT_REGION = "europe-central2";

/**
 * Hard step cap — safety net against runaway cost; the scorer finishes in a
 * single step under normal operation.
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
    instructions: SECURITY_REVIEW_INSTRUCTIONS,
    output: Output.object({ schema: SecurityResultSchema }),
    stopWhen: stepCountIs(STEP_CAP),
  });
}

/**
 * Parse a raw model text payload into a validated `SecurityResult` using a
 * trailing-comma-tolerant parser. Recovery path for Gemini's ~14% malformed-JSON
 * rate. Pure and network-free — unit-tested.
 */
export function parseReviewResult(text: string): SecurityResult {
  return SecurityResultSchema.parse(JSON5.parse(text));
}

/**
 * Run the security review against Vertex Gemini and return the validated result.
 *
 * Defensive json5 fallback: when the model returns text the SDK can't parse,
 * `generate()` throws `NoObjectGeneratedError` with the raw output on `.text`.
 * Recover with the trailing-comma-tolerant parser before failing closed.
 */
export async function runReview(prompt: string): Promise<SecurityResult> {
  const agent = createReviewAgent();

  try {
    const result = await agent.generate({ prompt });
    return result.output;
  } catch (err) {
    if (
      NoObjectGeneratedError.isInstance(err) &&
      typeof err.text === "string" &&
      err.text.trim().length > 0
    ) {
      return parseReviewResult(err.text);
    }
    throw err;
  }
}
