/** Client-side checks of a master CV before it is sent. The server stays the judge: these checks only give a clear
 * message early, and they refuse the file the server would also refuse ("draft": true). */

export type MasterCvCheck =
  | {
      readonly ok: true;
      readonly name: string;
      readonly facts: number;
      readonly payload: Record<string, unknown>;
    }
  | { readonly ok: false; readonly reason: "empty" }
  | { readonly ok: false; readonly reason: "invalidJson"; readonly detail: string }
  | { readonly ok: false; readonly reason: "notAnObject" }
  | { readonly ok: false; readonly reason: "draft" }
  | { readonly ok: false; readonly reason: "missingName" }
  | { readonly ok: false; readonly reason: "noFacts" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function checkMasterCv(text: string): MasterCvCheck {
  if (text.trim() === "") return { ok: false, reason: "empty" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return {
      ok: false,
      reason: "invalidJson",
      detail: error instanceof SyntaxError ? error.message : "syntax",
    };
  }
  if (!isRecord(parsed)) return { ok: false, reason: "notAnObject" };
  if (parsed.draft === true) return { ok: false, reason: "draft" };

  const name = typeof parsed.full_name === "string" ? parsed.full_name.trim() : "";
  if (name === "") return { ok: false, reason: "missingName" };

  const facts = Array.isArray(parsed.facts) ? parsed.facts.length : 0;
  if (facts === 0) return { ok: false, reason: "noFacts" };

  return { ok: true, name, facts, payload: parsed };
}
