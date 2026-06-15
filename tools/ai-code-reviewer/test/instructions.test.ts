import { describe, it, expect } from "vitest";
import { REVIEW_INSTRUCTIONS } from "../src/instructions.js";

describe("REVIEW_INSTRUCTIONS", () => {
  const text = REVIEW_INSTRUCTIONS.toLowerCase();

  it("encodes the BigQuery reserved-keyword backtick check", () => {
    expect(text).toContain("reserved-keyword");
    expect(text).toContain("backtick");
    expect(text).toContain("`window`");
    expect(text).toContain("`range`");
  });

  it("encodes the mocked-BQ-test blind spot", () => {
    expect(text).toContain("mocked");
    expect(text).toContain("do not verify sql syntax");
  });

  it("encodes the json5-tolerant Gemini parsing rule", () => {
    expect(text).toContain("json5.loads");
    expect(text).toContain("json.loads");
  });

  it("encodes secrets-in-env-only", () => {
    expect(text).toContain("environment variables only");
  });

  it("encodes human-only destructive infra", () => {
    expect(text).toContain("human-only");
    expect(text).toContain("never be automated");
  });

  it("instructs the model to treat PR content as untrusted", () => {
    expect(text).toContain("untrusted");
  });

  it("names all six criterion keys so they bind to the schema", () => {
    for (const key of [
      "correctness",
      "idiomaticity",
      "complexity",
      "testCoverageVsRisk",
      "security",
      "dataInfraSafety",
    ]) {
      expect(REVIEW_INSTRUCTIONS).toContain(key);
    }
  });
});
