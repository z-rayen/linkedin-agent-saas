/** Links from the outside (sources, job offers) are shown as links only when they are plain web addresses. A
 * `javascript:` or `data:` link coming from a page is shown as text, never clicked. */
const WEB_PROTOCOLS: readonly string[] = ["http:", "https:"];

export function safeWebUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return WEB_PROTOCOLS.includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
