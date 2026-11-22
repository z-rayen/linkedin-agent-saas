/** Response and request shapes used by the frontend. Checked against the backend's OpenAPI in contract.test.ts. */
import type { Problem } from "./errors";

export type ApplicationStatus =
  "prepared" | "approved" | "sending" | "needs_verification" | "sent" | "failed" | "replied";

export type RunStatus = "queued" | "running" | "awaiting_review" | "closed" | "failed";

export interface TokenResponse {
  readonly access_token: string;
  readonly token_type: string;
}

export interface MasterCvSaved {
  readonly status: string;
  readonly facts: number;
}

export interface CvLine {
  readonly section: "skill" | "experience" | "project";
  readonly text: string;
  readonly source_ids: readonly string[];
}

export interface ApplicationSummary {
  readonly id: number;
  readonly status: ApplicationStatus;
  readonly company: string | null;
  readonly job_title: string;
  readonly recipient_email: string;
  readonly created_at: string | null;
}

export interface ApplicationDetail extends ApplicationSummary {
  readonly company_key: string;
  readonly subject: string;
  readonly body: string;
  readonly grounding_problems: readonly Problem[];
  readonly cv_lines: readonly CvLine[];
  readonly has_pdf: boolean;
  readonly pdf_sha256: string | null;
  readonly last_error: string | null;
  readonly approved_at: string | null;
  readonly sent_at: string | null;
}

export interface PreparationStatus {
  readonly run_id: string;
  readonly status: RunStatus;
  readonly step: string | null;
  readonly application_id: number | null;
  readonly error: string | null;
  readonly stalled: boolean;
  readonly heartbeat_at: string | null;
  readonly grounding?: {
    readonly problems: number;
    readonly revision_limit_reached: boolean;
    readonly message: string;
  };
}

export interface ApplicationCreateBody {
  readonly job_title: string;
  readonly job_text?: string;
  readonly job_url?: string;
  readonly recipient_email: string;
  readonly company?: string;
}

export interface ApplicationEditBody {
  readonly subject?: string;
  readonly body?: string;
  readonly recipient_email?: string;
}

export interface SendResponse {
  readonly status: ApplicationStatus;
  readonly dry_run: boolean;
  readonly message_id?: string;
  readonly redirected?: boolean;
  readonly would_send_to?: string;
}

/** What the server says about how mail leaves the application. Not served yet: see the frontend README. */
export type MailMode = "dry_run" | "redirected" | "real";

export interface MailModeResponse {
  readonly mode: MailMode;
}

/** The user's own master CV, as stored. Only the fields the screens show are typed. */
export interface StoredMasterCv {
  readonly full_name: string;
  readonly facts: readonly unknown[];
}

export interface PasswordPolicy {
  readonly min_length: number;
  readonly max_bytes: number;
}

export interface StreamTicket {
  readonly ticket: string;
  readonly expires_in: number;
  readonly events: string;
}

/** A progress snapshot of a run, as the stream sends it: status and step only. */
export interface RunSnapshot {
  readonly status: RunStatus;
  readonly step: string;
  readonly stalled: boolean;
}
