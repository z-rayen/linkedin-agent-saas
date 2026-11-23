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
  ProfileConfig,
  KnowledgeItem,
  Connection,
  Provider,
  ContentItem,
  ResearchRun,
  ResearchStart,
  ProviderRevocation,
  ProfileConfigBody,
  WatchResultPage,
  WatchResultsQuery,
  WatchRun,
  WatchRunStart,
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
  startResearch: (body: { request: string; draft_post: boolean }) =>
    apiRequest<ResearchStart>("/agent/research", { method: "POST", body }),

  getResearch: (runId: string, signal?: AbortSignal) =>
    apiRequest<ResearchRun>(
      `/agent/research/${encodeURIComponent(runId)}`,
      signal ? { signal } : {},
    ),

  getProfile: (signal?: AbortSignal) =>
    apiRequest<ProfileConfig>("/profile", signal ? { signal } : {}),

  saveProfile: (body: ProfileConfigBody) =>
    apiRequest<ProfileConfig>("/profile", { method: "POST", body }),

  listKnowledge: (signal?: AbortSignal) =>
    apiRequest<KnowledgeItem[]>("/profile/knowledge", signal ? { signal } : {}),

  addKnowledge: (content: string) =>
    apiRequest<KnowledgeItem>("/profile/knowledge", { method: "POST", body: { content } }),

  deleteKnowledge: (id: number) =>
    apiRequest<{ status: string }>(`/profile/knowledge/${String(id)}`, { method: "DELETE" }),

  startWatchRun: () => apiRequest<WatchRunStart>("/watch/runs", { method: "POST" }),

  getWatchRun: (runId: string, signal?: AbortSignal) =>
    apiRequest<WatchRun>(`/watch/runs/${encodeURIComponent(runId)}`, signal ? { signal } : {}),

  watchResults: (query: WatchResultsQuery, signal?: AbortSignal) => {
    const params = new URLSearchParams({
      limit: String(query.limit),
      offset: String(query.offset),
    });
    if (query.kind !== undefined) params.set("kind", query.kind);
    if (query.minScore !== undefined) params.set("min_score", String(query.minScore));
    if (query.verified !== undefined) params.set("verified", String(query.verified));
    return apiRequest<WatchResultPage>(
      `/watch/results?${params.toString()}`,
      signal ? { signal } : {},
    );
  },

  deleteWatchResult: (id: number) =>
    apiRequest<unknown>(`/watch/results/${String(id)}`, { method: "DELETE" }),

  /** Disconnects a provider: the stored token is deleted, and revoked at the provider when it can be. */
  disconnect: (provider: Provider) =>
    apiRequest<{ provider: Provider; status: string; provider_revoked: ProviderRevocation }>(
      `/oauth/${provider}`,
      { method: "DELETE" },
    ),

  connections: (signal?: AbortSignal) =>
    apiRequest<Connection[]>("/oauth/connections", signal ? { signal } : {}),

  /** The provider's consent page. The browser is sent there; the server answers with the page to go to. */
  authorizeUrl: (provider: Provider) =>
    apiRequest<{ url: string }>(`/oauth/${provider}/authorize-url`),

  contentQueue: (signal?: AbortSignal) =>
    apiRequest<ContentItem[]>("/content/queue", signal ? { signal } : {}),

  generateContent: (body: { title: string; context: string; source: string }) =>
    apiRequest<ContentItem>("/content/generate", { method: "POST", body }),

  editContent: (id: number, body: { title?: string; post_text?: string }) =>
    apiRequest<ContentItem>(`/content/${String(id)}`, { method: "PATCH", body }),

  publishContent: (id: number) =>
    apiRequest<{ status: string }>(`/content/${String(id)}/publish`, { method: "POST" }),

  /** Deletes the account and everything that belongs to it. The password is typed again: a stray call cannot do it. */
  deleteAccount: (password: string) =>
    apiRequest<{ status: string }>("/auth/account", { method: "DELETE", body: { password } }),
} as const;
