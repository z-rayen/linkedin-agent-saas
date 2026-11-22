/**
 * Where the login token lives in the browser. Chosen approach (see the frontend README, section Authentication):
 * a memory copy for the running page, mirrored in sessionStorage so a reload keeps the session, and cleared when the
 * tab closes. The token is never placed in a URL, never logged, and never written to localStorage.
 *
 * Trade-off: any script running on this origin could read sessionStorage. An httpOnly cookie avoids that, but needs a
 * backend change (cookie issuance, CSRF protection, logout). This module is the only place that would change.
 */

export const TOKEN_STORAGE_KEY = "linkedin-agent.session.v1";

let memory: string | null = null;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // storage can be blocked: the memory copy still works for this page
  }
}

export function readToken(): string | null {
  if (memory !== null) return memory;
  const stored = storage()?.getItem(TOKEN_STORAGE_KEY) ?? null;
  memory = stored;
  return stored;
}

export function writeToken(token: string): void {
  memory = token;
  storage()?.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  memory = null;
  storage()?.removeItem(TOKEN_STORAGE_KEY);
}
