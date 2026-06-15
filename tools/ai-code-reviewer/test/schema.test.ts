import { describe, it, expect } from "vitest";
import { ReviewResultSchema } from "../src/schema.js";

const wellFormed = {
  scores: {
    correctness: 8,
    idiomaticity: 7,
    complexity: 9,
    testCoverageVsRisk: 6,
    security: 10,
    dataInfraSafety: 5,
  },
  verdict: "pass" as const,
  summary: "Looks good. Minor nit in `src/foo.py`.",
};

describe("ReviewResultSchema", () => {
  it("accepts a well-formed result", () => {
    expect(() => ReviewResultSchema.parse(wellFormed)).not.toThrow();
  });

  it("rejects a score above 10", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, correctness: 11 } };
    expect(() => ReviewResultSchema.parse(bad)).toThrow();
  });

  it("rejects a score below 1", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, security: 0 } };
    expect(() => ReviewResultSchema.parse(bad)).toThrow();
  });

  it("rejects a non-integer score", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, complexity: 7.5 } };
    expect(() => ReviewResultSchema.parse(bad)).toThrow();
  });

  it("rejects an unknown verdict", () => {
    const bad = { ...wellFormed, verdict: "maybe" };
    expect(() => ReviewResultSchema.parse(bad)).toThrow();
  });

  it("rejects a missing criterion", () => {
    const { dataInfraSafety, ...partial } = wellFormed.scores;
    void dataInfraSafety;
    const bad = { ...wellFormed, scores: partial };
    expect(() => ReviewResultSchema.parse(bad)).toThrow();
  });

  it("exposes all six criteria as keys", () => {
    const parsed = ReviewResultSchema.parse(wellFormed);
    expect(Object.keys(parsed.scores).sort()).toEqual(
      [
        "complexity",
        "correctness",
        "dataInfraSafety",
        "idiomaticity",
        "security",
        "testCoverageVsRisk",
      ].sort(),
    );
  });
});
