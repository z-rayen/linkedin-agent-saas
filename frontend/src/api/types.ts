/** Response and request shapes used by the frontend. Checked against the backend's OpenAPI in contract.test.ts. */
import type { Problem } from "./errors";

export type ApplicationStatus =
  "prepared" | "approved" | "sending" | "needs_verification" | "sent" | "failed" | "replied";

/** Preparations end in awaiting_review, closed or failed; research runs end in done or failed. */
export type RunStatus = "queued" | "running" | "awaiting_review" | "closed" | "failed" | "done";

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

/** Research: the basis of a statement. "fetched" = the full page was read; "snippet" = only the search snippet. */
export type ClaimBasis = "fetched" | "snippet" | "none";

export interface ResearchClaim {
  readonly text: string;
  readonly sources: readonly number[];
  readonly basis: ClaimBasis;
}

export interface ResearchSource {
  readonly number: number;
  readonly title: string;
  readonly link: string;
  readonly fetched: boolean;
}

/** A link the research retrieved but did not cite: no number, since the answer does not refer to it. */
export interface ResearchLink {
  readonly title: string;
  readonly link: string;
  readonly fetched: boolean;
}

export interface ResearchResult {
  readonly answer: string;
  readonly retrieved_at: string;
  readonly language: string;
  readonly claims: readonly ResearchClaim[];
  readonly sources: readonly ResearchSource[];
  readonly also_found: readonly ResearchLink[];
  /** Statements removed by the checks, counted by reason. */
  readonly unsupported: Readonly<Record<string, number>>;
  readonly off_topic: number;
  readonly post_id: number | null;
}

export type ResearchStatus = "queued" | "running" | "done" | "failed";

export interface ResearchRun {
  readonly run_id: string;
  readonly status: ResearchStatus;
  readonly step: string | null;
  readonly error: string | null;
  readonly stalled: boolean;
  readonly result: ResearchResult | null;
}

export interface ResearchStart {
  readonly run_id: string;
  readonly status: ResearchStatus;
}

export interface ProfileConfig {
  readonly field: string;
  readonly skills: string;
  readonly level: string;
  readonly min_match_score: number;
  readonly job_feed_url: string | null;
  readonly cert_feed_url: string | null;
}

export interface KnowledgeItem {
  readonly id: number;
  readonly content: string;
}

export interface WatchResult {
  readonly id: number;
  readonly type: "job" | "cert" | "other";
  readonly title: string;
  readonly link: string;
  readonly match_score: number;
  readonly justification: string | null;
  readonly cover_letter: string | null;
}

export type ConnectionStatus = "connected" | "expired" | "not_connected";
export type Provider = "linkedin" | "github";

export interface Connection {
  readonly provider: Provider;
  readonly status: ConnectionStatus;
  readonly expires_at: string | null;
}

export type ContentStatus = "draft" | "approved" | "published" | "error";

export interface ContentItem {
  readonly id: number;
  readonly title: string;
  readonly post_text: string;
  readonly status: ContentStatus;
  readonly source: string;
  readonly created_at: string;
}

/** A progress snapshot of a run, as the stream sends it: status and step only. */
export interface RunSnapshot {
  readonly status: RunStatus;
  readonly step: string;
  readonly stalled: boolean;
}
