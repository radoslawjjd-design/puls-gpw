import { describe, it, expect } from "vitest";
import { SecurityResultSchema } from "../src/schema.js";

const wellFormed = {
  scores: {
    secretsLeakage: 8,
    injectionRisk: 7,
    inputValidation: 9,
    dependencySafety: 6,
    authPermissions: 10,
  },
  verdict: "pass" as const,
  summary: "No security issues found.",
};

describe("SecurityResultSchema", () => {
  it("accepts a well-formed result", () => {
    expect(() => SecurityResultSchema.parse(wellFormed)).not.toThrow();
  });

  it("rejects a score above 10", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, secretsLeakage: 11 } };
    expect(() => SecurityResultSchema.parse(bad)).toThrow();
  });

  it("rejects a score below 1", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, authPermissions: 0 } };
    expect(() => SecurityResultSchema.parse(bad)).toThrow();
  });

  it("rejects a non-integer score", () => {
    const bad = { ...wellFormed, scores: { ...wellFormed.scores, injectionRisk: 7.5 } };
    expect(() => SecurityResultSchema.parse(bad)).toThrow();
  });

  it("rejects an unknown verdict", () => {
    const bad = { ...wellFormed, verdict: "maybe" };
    expect(() => SecurityResultSchema.parse(bad)).toThrow();
  });

  it("rejects a missing criterion", () => {
    const { authPermissions, ...partial } = wellFormed.scores;
    void authPermissions;
    const bad = { ...wellFormed, scores: partial };
    expect(() => SecurityResultSchema.parse(bad)).toThrow();
  });

  it("exposes all five criteria as keys", () => {
    const parsed = SecurityResultSchema.parse(wellFormed);
    expect(Object.keys(parsed.scores).sort()).toEqual(
      [
        "authPermissions",
        "dependencySafety",
        "injectionRisk",
        "inputValidation",
        "secretsLeakage",
      ].sort(),
    );
  });
});
