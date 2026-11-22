/** Every backend call the frontend makes, typed. Paths and methods are checked against the OpenAPI in contract.test.ts. */
import { apiBlob, apiRequest } from "./client";
import type {
  ApplicationCreateBody,
  ApplicationDetail,
  ApplicationEditBody,
  ApplicationSummary,
  MailModeResponse,
  MasterCvSaved,
  PasswordPolicy,
  PreparationStatus,
  SendResponse,
  StoredMasterCv,
  StreamTicket,
  TokenResponse,
} from "./types";

const applicationPath = (id: number): string => `/applications/${String(id)}`;

export const endpoints = {
  passwordPolicy: (signal?: AbortSignal) =>
    apiRequest<PasswordPolicy>("/auth/password-policy", {
      auth: false,
      ...(signal ? { signal } : {}),
    }),

  login: (email: string, password: string) =>
    apiRequest<TokenResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    }),

  register: (email: string, password: string) =>
    apiRequest<unknown>("/auth/register", {
      method: "POST",
      body: { email, password },
      auth: false,
    }),

  getMasterCv: (signal?: AbortSignal) =>
    apiRequest<StoredMasterCv>("/applications/master-cv", signal ? { signal } : {}),

  putMasterCv: (master: unknown) =>
    apiRequest<MasterCvSaved>("/applications/master-cv", { method: "PUT", body: master }),

  listApplications: (signal?: AbortSignal) =>
    apiRequest<ApplicationSummary[]>("/applications", signal ? { signal } : {}),

  getApplication: (id: number, signal?: AbortSignal) =>
    apiRequest<ApplicationDetail>(applicationPath(id), signal ? { signal } : {}),

  createApplication: (body: ApplicationCreateBody) =>
    apiRequest<PreparationStatus>("/applications", { method: "POST", body }),

  getPreparation: (runId: string, signal?: AbortSignal) =>
    apiRequest<PreparationStatus>(
      `/applications/preparations/${encodeURIComponent(runId)}`,
      signal ? { signal } : {},
    ),

  editApplication: (id: number, body: ApplicationEditBody) =>
    apiRequest<ApplicationDetail>(applicationPath(id), { method: "PATCH", body }),

  approveApplication: (id: number) =>
    apiRequest<ApplicationDetail>(`${applicationPath(id)}/approve`, { method: "POST" }),

  sendApplication: (id: number, confirmRecipient: string) =>
    apiRequest<SendResponse>(`${applicationPath(id)}/send`, {
      method: "POST",
      body: { confirm_recipient: confirmRecipient },
    }),

  resolveApplication: (id: number, outcome: "sent" | "failed", note: string) =>
    apiRequest<ApplicationDetail>(`${applicationPath(id)}/resolve`, {
      method: "POST",
      body: { outcome, note },
    }),

  pdfBlob: (id: number, signal?: AbortSignal) => apiBlob(`${applicationPath(id)}/cv.pdf`, signal),

  /** A single-use ticket for one progress stream. The stream itself is opened by runStream.ts, never with the token. */
  streamTicket: (runId: string) =>
    apiRequest<StreamTicket>(`/runs/${encodeURIComponent(runId)}/stream-ticket`, {
      method: "POST",
    }),

  /** The send mode, from the server's configuration. An error means "unknown": callers must never treat it as "dry run". */
  mailMode: (signal?: AbortSignal) =>
    apiRequest<MailModeResponse>("/applications/mail-mode", signal ? { signal } : {}),
} as const;
