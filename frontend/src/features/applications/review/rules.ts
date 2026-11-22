/** The review rules, as pure functions: the screen asks them, and the tests check them. The server enforces the same
 * rules at each action; these keep the screen honest about what it will accept. */
import type { Problem } from "../../../api/errors";
import type { ApplicationStatus, MailMode } from "../../../api/types";
import type { BannerTone } from "../../../components/Banner";
import type { MessageKey } from "../../../i18n/I18nProvider";

const EMAIL_LOCATION = "email body";
const LINE_LOCATION = /^line (\d+)$/;
const FIRST_LINE_NUMBER = 1;

export type ProblemArea =
  | { readonly area: "email" }
  | { readonly area: "cv"; readonly lineIndex: number }
  | { readonly area: "other" };

/** Where a problem belongs on the page: next to the email, next to one CV line, or in the general list. */
export function problemArea(problem: Problem): ProblemArea {
  if (problem.location === EMAIL_LOCATION) return { area: "email" };
  const line = LINE_LOCATION.exec(problem.location)?.[1];
  if (line !== undefined) return { area: "cv", lineIndex: Number(line) - FIRST_LINE_NUMBER };
  return { area: "other" };
}

export interface GroupedProblems {
  readonly email: readonly Problem[];
  readonly byLine: ReadonlyMap<number, readonly Problem[]>;
  readonly other: readonly Problem[];
}

export function groupProblems(problems: readonly Problem[]): GroupedProblems {
  const email: Problem[] = [];
  const other: Problem[] = [];
  const byLine = new Map<number, Problem[]>();
  for (const problem of problems) {
    const area = problemArea(problem);
    if (area.area === "email") {
      email.push(problem);
    } else if (area.area === "cv") {
      const list = byLine.get(area.lineIndex) ?? [];
      list.push(problem);
      byLine.set(area.lineIndex, list);
    } else {
      other.push(problem);
    }
  }
  return { email, byLine, other };
}

const KNOWN_KINDS: ReadonlySet<string> = new Set([
  "number",
  "term",
  "technology",
  "claim",
  "placeholder",
  "unknown_source",
  "mandatory",
  "missing_cv",
  "legacy",
  "unknown",
]);

export function problemKindKey(kind: string): MessageKey {
  return KNOWN_KINDS.has(kind)
    ? (`review.problemKind.${kind}` as MessageKey)
    : "review.problemKind.unknown";
}

/** Only a prepared application with no problem left can be approved. */
export function canApprove(status: ApplicationStatus, problems: readonly Problem[]): boolean {
  return status === "prepared" && problems.length === 0;
}

/** Content can be changed while it is prepared or approved. Any change makes it prepared again. */
export function canEdit(status: ApplicationStatus): boolean {
  return status === "prepared" || status === "approved";
}

export function statusAfterEdit(): ApplicationStatus {
  return "prepared";
}

/** The typed address must match the recipient exactly, once surrounding spaces are removed. */
export function isExactAddress(typed: string, recipient: string): boolean {
  const expected = recipient.trim();
  return expected !== "" && typed.trim() === expected;
}

export interface SendCheck {
  readonly status: ApplicationStatus;
  readonly mode: MailMode | null;
  readonly typed: string;
  readonly recipient: string;
}

/** Sending needs an approved application, a known mode (never a guessed one), and the exact typed address. */
export function canSend(check: SendCheck): boolean {
  return (
    check.status === "approved" &&
    check.mode !== null &&
    isExactAddress(check.typed, check.recipient)
  );
}

export interface ModeBanner {
  readonly tone: BannerTone;
  readonly key: MessageKey;
}

export function modeBanner(mode: MailMode | null): ModeBanner {
  switch (mode) {
    case "dry_run":
      return { tone: "info", key: "send.modeDryRun" };
    case "redirected":
      return { tone: "warning", key: "send.modeRedirected" };
    case "real":
      return { tone: "danger", key: "send.modeReal" };
    case null:
      return { tone: "warning", key: "send.modeUnknown" };
  }
}
