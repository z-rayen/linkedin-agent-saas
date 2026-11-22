/** Query keys, in one place, so that invalidation and reads cannot drift apart. */
export const applicationKeys = {
  all: ["applications"] as const,
  detail: (id: number) => ["applications", id] as const,
  pdf: (id: number, sha: string | null) => ["application-pdf", id, sha] as const,
  preparation: (runId: string) => ["preparation", runId] as const,
  mailMode: ["mail-mode"] as const,
  masterCv: ["master-cv"] as const,
  passwordPolicy: ["password-policy"] as const,
};
