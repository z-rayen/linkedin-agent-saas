import { describe, expect, it } from "vitest";
import type { Problem } from "../../../api/errors";
import {
  canApprove,
  canEdit,
  canSend,
  groupProblems,
  isExactAddress,
  modeBanner,
  problemArea,
  problemKindKey,
  statusAfterEdit,
} from "./rules";

const problem = (location: string, kind = "claim"): Problem => ({
  kind,
  location,
  term: "Kubernetes",
  message: "not in the master CV",
});

describe("problem placement", () => {
  it("puts each problem next to its part: the email, one CV line, or the general list", () => {
    expect(problemArea(problem("email body"))).toEqual({ area: "email" });
    expect(problemArea(problem("line 3"))).toEqual({ area: "cv", lineIndex: 2 });
    expect(problemArea(problem("master CV"))).toEqual({ area: "other" });
  });

  it("groups problems without losing any", () => {
    const grouped = groupProblems([
      problem("email body"),
      problem("line 1"),
      problem("line 1"),
      problem("CV"),
    ]);
    expect(grouped.email).toHaveLength(1);
    expect(grouped.byLine.get(0)).toHaveLength(2);
    expect(grouped.other).toHaveLength(1);
  });

  it("shows an unknown problem kind as a generic problem, never as raw text", () => {
    expect(problemKindKey("number")).toBe("review.problemKind.number");
    expect(problemKindKey("something_new")).toBe("review.problemKind.unknown");
  });
});

describe("approval", () => {
  it("is blocked while any grounding problem remains", () => {
    expect(canApprove("prepared", [problem("line 1")])).toBe(false);
  });

  it("is allowed only for a prepared application with no problem", () => {
    expect(canApprove("prepared", [])).toBe(true);
    expect(canApprove("approved", [])).toBe(false);
    expect(canApprove("sent", [])).toBe(false);
  });
});

describe("editing", () => {
  it("is allowed while prepared or approved, and any edit makes the application prepared again", () => {
    expect(canEdit("prepared")).toBe(true);
    expect(canEdit("approved")).toBe(true);
    expect(canEdit("sent")).toBe(false);
    expect(statusAfterEdit()).toBe("prepared");
  });
});

describe("sending", () => {
  it("needs the exact recipient address, with surrounding spaces ignored", () => {
    expect(isExactAddress("jobs@acme.test", "jobs@acme.test")).toBe(true);
    expect(isExactAddress("  jobs@acme.test ", "jobs@acme.test")).toBe(true);
    expect(isExactAddress("hr@acme.test", "jobs@acme.test")).toBe(false);
    expect(isExactAddress("", "")).toBe(false);
  });

  it("is refused for an unapproved application, an unknown mode, or a different address", () => {
    const base = { typed: "jobs@acme.test", recipient: "jobs@acme.test" };
    expect(canSend({ ...base, status: "prepared", mode: "real" })).toBe(false);
    expect(canSend({ ...base, status: "approved", mode: null })).toBe(false);
    expect(canSend({ ...base, status: "approved", mode: "dry_run", typed: "x@acme.test" })).toBe(
      false,
    );
    expect(canSend({ ...base, status: "approved", mode: "dry_run" })).toBe(true);
  });
});

describe("mode banner", () => {
  it("states the mode in words, with a distinct tone for each", () => {
    expect(modeBanner("dry_run")).toEqual({ tone: "info", key: "send.modeDryRun" });
    expect(modeBanner("redirected")).toEqual({ tone: "warning", key: "send.modeRedirected" });
    expect(modeBanner("real")).toEqual({ tone: "danger", key: "send.modeReal" });
  });

  it("says the mode is unknown when the server does not report it", () => {
    expect(modeBanner(null)).toEqual({ tone: "warning", key: "send.modeUnknown" });
  });
});
