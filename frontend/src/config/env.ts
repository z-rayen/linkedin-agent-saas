/** Build-time configuration. Values come from Vite env variables (see .env.example); nothing is hardcoded here. */

function readString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : fallback;
}

export const config = {
  /** Base URL of the API. "/api" goes through the dev proxy or the production reverse proxy. */
  apiBaseUrl: readString(import.meta.env.VITE_API_BASE_URL, "/api"),
} as const;
