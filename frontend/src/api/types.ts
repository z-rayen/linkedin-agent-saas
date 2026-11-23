/** Response and request shapes used by the frontend. Checked against the backend's OpenAPI in contract.test.ts. */
import type { Problem } from "./errors";
import type { components } from "./schema.gen";

/** Request bodies are taken from the generated OpenAPI types: a change of the backend that they do not match fails
 * the type check, not a request at runtime. */
type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name];

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

export type ApplicationCreateBody = Schema<"ApplicationCreate">;

export type ApplicationEditBody = Schema<"ApplicationEdit">;

export type ProfileConfigBody = Schema<"ProfileConfigIn">;

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

/** The profile as the server returns it (watch preferences included). Derived from the generated schema. */
export type ProfileConfig = Schema<"ProfileConfigOut">;
export interface KnowledgeItem {
  readonly id: number;
  readonly content: string;
}

/** A stored watch result. `unverified` is true when it was judged on the search snippet, not on the full page. */
export interface WatchResult {
  readonly id: number;
  readonly type: "job" | "cert" | "other";
  readonly title: string;
  readonly link: string;
  readonly match_score: number;
  readonly justification: string | null;
  readonly cover_letter: string | null;
  readonly company: string | null;
  readonly job_title: string | null;
  readonly fetched: boolean;
  readonly unverified: boolean;
  readonly created_at: string | null;
}

export interface WatchResultPage {
  readonly items: readonly WatchResult[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface WatchRunStart {
  readonly run_id: string;
  readonly status: RunStatus;
}

/** A watch run as the server reports it: the same shape as the other background runs, without a research result. */
export interface WatchRun {
  readonly run_id: string;
  readonly status: RunStatus;
  readonly step: string | null;
  readonly error: string | null;
  readonly stalled: boolean;
  readonly result: unknown;
}

export type WatchKind = "job" | "cert";

export interface WatchResultsQuery {
  readonly kind?: WatchKind;
  readonly minScore?: number;
  readonly verified?: boolean;
  readonly limit: number;
  readonly offset: number;
}

export type ConnectionStatus = "connected" | "expired" | "not_connected";
export type Provider = "linkedin" | "github";

/** What the provider side of a disconnect did. `not_supported`: the provider has no revocation endpoint. */
export type ProviderRevocation = "revoked" | "not_supported" | "failed";

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
