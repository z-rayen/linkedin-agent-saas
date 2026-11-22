import type { RunStatus } from "../../api/types";

export const POLL_INTERVAL_MS = 2000;
export const TICK_INTERVAL_MS = 1000;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;

const TERMINAL_STATUSES: readonly RunStatus[] = ["awaiting_review", "closed", "failed"];

/** Once a run is terminal, polling stops. "awaiting_review" leads to the review page; the other two are final. */
export function isTerminalRun(status: RunStatus | undefined): boolean {
  return status !== undefined && TERMINAL_STATUSES.includes(status);
}

/** The workflow steps the server reports. A step outside this list is shown as "current step", never as raw text. */
export const KNOWN_STEPS = [
  "fetch_job",
  "analyze_job",
  "research_company",
  "retrieve_knowledge",
  "tailor_cv",
  "draft_email",
  "verify",
  "revise",
  "persist_prepared",
  "await_human",
  "finalize",
  "resuming",
] as const;

export type KnownStep = (typeof KNOWN_STEPS)[number];

export function isKnownStep(step: string): step is KnownStep {
  return (KNOWN_STEPS as readonly string[]).includes(step);
}

const pad = (value: number): string => value.toString().padStart(2, "0");

/** "mm:ss" for the elapsed time. Negative values (clock skew) are shown as zero. */
export function formatElapsed(milliseconds: number): string {
  const total = Math.max(0, Math.floor(milliseconds / MS_PER_SECOND));
  const minutes = Math.floor(total / SECONDS_PER_MINUTE);
  const seconds = total % SECONDS_PER_MINUTE;
  return `${pad(minutes)}:${pad(seconds)}`;
}
