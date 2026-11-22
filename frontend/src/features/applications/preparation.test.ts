import { describe, expect, it } from "vitest";
import { formatElapsed, isKnownStep, isTerminalRun } from "./preparation";

describe("preparation progress", () => {
  it("shows the elapsed time as mm:ss and never a negative value", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(65_000)).toBe("01:05");
    expect(formatElapsed(-4_000)).toBe("00:00");
  });

  it("recognises only the steps it has a label for", () => {
    expect(isKnownStep("verify")).toBe(true);
    expect(isKnownStep("internal_debug_step")).toBe(false);
  });

  it("stops polling once the run needs the user or has ended", () => {
    expect(isTerminalRun("awaiting_review")).toBe(true);
    expect(isTerminalRun("closed")).toBe(true);
    expect(isTerminalRun("running")).toBe(false);
    expect(isTerminalRun(undefined)).toBe(false);
  });
});
