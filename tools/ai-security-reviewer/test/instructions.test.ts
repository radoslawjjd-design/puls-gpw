import { describe, it, expect } from "vitest";
import { SECURITY_REVIEW_INSTRUCTIONS } from "../src/instructions.js";

describe("SECURITY_REVIEW_INSTRUCTIONS", () => {
  const text = SECURITY_REVIEW_INSTRUCTIONS.toLowerCase();

  it("instructs the model to treat PR content as untrusted", () => {
    expect(text).toContain("untrusted");
  });

  it("names all five criterion keys so they bind to the schema", () => {
    for (const key of [
      "secretsLeakage",
      "injectionRisk",
      "inputValidation",
      "dependencySafety",
      "authPermissions",
    ]) {
      expect(SECURITY_REVIEW_INSTRUCTIONS).toContain(key);
    }
  });

  it("encodes the verdict output contract", () => {
    expect(text).toContain("verdict");
  });

  it("does not contain code-quality-only terms (no drift back toward code review)", () => {
    expect(text).not.toContain("idiomaticity");
    expect(text).not.toContain("datainfrasafety");
  });

  it("contains FastAPI / api.py context (project-specific boundary)", () => {
    expect(
      text.includes("fastapi") || text.includes("api.py"),
    ).toBe(true);
  });

  it("encodes secrets-in-env-only rule", () => {
    expect(text).toContain("environment variables");
  });
});
