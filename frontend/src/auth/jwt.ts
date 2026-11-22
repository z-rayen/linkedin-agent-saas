/** Reads the expiry of a JWT without verifying it. Used only to tell "expired" from "invalid" in the UI: the server
 * remains the judge of every token. */

function base64UrlToString(part: string): string {
  const normalised = part.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalised.padEnd(normalised.length + ((4 - (normalised.length % 4)) % 4), "=");
  return atob(padded);
}

/** The expiry time in milliseconds since the epoch, or null when the token is not a readable JWT. */
export function tokenExpiresAt(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3 || parts[1] === undefined) return null;
  try {
    const payload: unknown = JSON.parse(base64UrlToString(parts[1]));
    if (typeof payload !== "object" || payload === null || !("exp" in payload)) return null;
    const exp = payload.exp;
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

export function isExpired(token: string, now: number = Date.now()): boolean {
  const expiresAt = tokenExpiresAt(token);
  return expiresAt !== null && expiresAt <= now;
}
