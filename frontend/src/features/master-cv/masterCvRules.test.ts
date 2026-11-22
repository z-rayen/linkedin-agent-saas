import { describe, expect, it } from "vitest";
import { checkMasterCv } from "./masterCvRules";

const valid = {
  full_name: "Jane Doe",
  headline: "ML engineer",
  contact: { email: "jane@example.com", phone: "", location: "", links: [] },
  facts: [{ id: "exp-1", kind: "experience", text: "Built a service" }],
};

describe("checkMasterCv", () => {
  it("accepts a valid master CV and counts its facts", () => {
    const result = checkMasterCv(JSON.stringify(valid));
    expect(result).toMatchObject({ ok: true, name: "Jane Doe", facts: 1 });
  });

  it("refuses a file marked as draft, with its own reason", () => {
    expect(checkMasterCv(JSON.stringify({ ...valid, draft: true }))).toEqual({
      ok: false,
      reason: "draft",
    });
  });

  it("refuses text that is not JSON, and says why", () => {
    const result = checkMasterCv("{ not json");
    expect(result.ok).toBe(false);
    expect(result).toMatchObject({ reason: "invalidJson" });
  });

  it("refuses an empty text and a JSON array", () => {
    expect(checkMasterCv("   ")).toEqual({ ok: false, reason: "empty" });
    expect(checkMasterCv("[1, 2]")).toEqual({ ok: false, reason: "notAnObject" });
  });

  it("requires a name and at least one fact", () => {
    expect(checkMasterCv(JSON.stringify({ ...valid, full_name: "  " }))).toEqual({
      ok: false,
      reason: "missingName",
    });
    expect(checkMasterCv(JSON.stringify({ ...valid, facts: [] }))).toEqual({
      ok: false,
      reason: "noFacts",
    });
  });
});
