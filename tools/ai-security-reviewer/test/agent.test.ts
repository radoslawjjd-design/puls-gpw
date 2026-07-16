import { describe, it, expect, afterEach } from "vitest";
import { getModelId, DEFAULT_MODEL, STEP_CAP, parseReviewResult } from "../src/agent.js";

describe("agent wiring", () => {
  const original = process.env.GEMINI_MODEL;

  afterEach(() => {
    if (original === undefined) {
      delete process.env.GEMINI_MODEL;
    } else {
      process.env.GEMINI_MODEL = original;
    }
  });

  it("defaults to full Gemini Flash (not flash-lite)", () => {
    delete process.env.GEMINI_MODEL;
    expect(getModelId()).toBe("gemini-2.5-flash");
    expect(DEFAULT_MODEL).toBe("gemini-2.5-flash");
  });

  it("reads the model id from GEMINI_MODEL when set", () => {
    process.env.GEMINI_MODEL = "gemini-2.5-pro";
    expect(getModelId()).toBe("gemini-2.5-pro");
  });

  it("bounds the agent loop with a hard step cap", () => {
    expect(STEP_CAP).toBe(8);
  });
});

describe("parseReviewResult (malformed-JSON recovery path)", () => {
  const body = `{
    "scores": {
      "secretsLeakage": 8,
      "injectionRisk": 7,
      "inputValidation": 9,
      "dependencySafety": 6,
      "authPermissions": 10,
    },
    "verdict": "pass",
    "summary": "Trailing commas everywhere.",
  }`;

  it("recovers a SecurityResult from JSON with trailing commas (Gemini's ~14% failure mode)", () => {
    const result = parseReviewResult(body);
    expect(result.verdict).toBe("pass");
    expect(result.scores.authPermissions).toBe(10);
  });

  it("still rejects a recovered payload that violates the schema", () => {
    const outOfRange = body.replace('"secretsLeakage": 8', '"secretsLeakage": 99');
    expect(() => parseReviewResult(outOfRange)).toThrow();
  });
});
