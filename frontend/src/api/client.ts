import { config } from "../config/env";
import { ApiError, errorFromResponse } from "./errors";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  readonly method?: HttpMethod;
  readonly body?: unknown;
  readonly signal?: AbortSignal;
  /** Public endpoints (login, register) send no token and do not trigger the session-expired flow. */
  readonly auth?: boolean;
}

type TokenProvider = () => string | null;
type UnauthorizedHandler = () => void;

let tokenProvider: TokenProvider = () => null;
let unauthorizedHandler: UnauthorizedHandler = () => undefined;

/** Wired once by the auth provider. The client never stores the token itself. */
export function configureClient(options: {
  token: TokenProvider;
  onUnauthorized: UnauthorizedHandler;
}): void {
  tokenProvider = options.token;
  unauthorizedHandler = options.onUnauthorized;
}

export function apiUrl(path: string): string {
  return `${config.apiBaseUrl}${path}`;
}

function authorization(auth: boolean): Record<string, string> {
  if (!auth) return {};
  const token = tokenProvider();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Checked by shape, not by class: an abort error may come from another realm (for example, a test environment). */
function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" && error !== null && "name" in error && error.name === "AbortError"
  );
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const auth = options.auth ?? true;
  const headers: Record<string, string> = { Accept: "application/json", ...authorization(auth) };
  const init: RequestInit = { method: options.method ?? "GET", headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json; charset=utf-8";
    init.body = JSON.stringify(options.body);
  }
  if (options.signal) init.signal = options.signal;
  try {
    return await fetch(apiUrl(path), init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError({ status: 0, code: "network", message: "network" });
  }
}

/** A JSON request. A 401 on an authenticated request ends the session (the auth provider redirects to login). */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 401 && (options.auth ?? true)) {
    unauthorizedHandler();
    throw await errorFromResponse(response);
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** A binary request, such as the PDF preview. The token goes in the header, never in the URL. */
export async function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const init: RequestOptions = signal ? { signal } : {};
  const response = await send(path, init);
  if (response.status === 401) {
    unauthorizedHandler();
    throw await errorFromResponse(response);
  }
  if (!response.ok) throw await errorFromResponse(response);
  return response.blob();
}
