/** The client's calls checked against the backend's OpenAPI snapshot (frontend/openapi.json, dumped from the backend).
 * Every path the client uses must exist in the spec with the right method. The snapshot is refreshed with
 * backend/scripts/dump_openapi.py whenever the API changes. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { endpoints } from "./endpoints";

/** Run from the frontend folder (npm scripts do). The jsdom test environment has no file URL for this module. */
const OPENAPI_SNAPSHOT = resolve(process.cwd(), "openapi.json");

interface OpenApiSpec {
  readonly paths: Readonly<
    Record<
      string,
      Readonly<Record<string, { readonly responses?: Readonly<Record<string, unknown>> }>>
    >
  >;
}

interface Call {
  readonly method: "get" | "post" | "put" | "patch" | "delete";
  readonly path: string;
}

/** Every call the client makes, one per endpoint. Keep this in step with endpoints.ts. */
const CLIENT_CALLS: readonly Call[] = [
  { method: "get", path: "/auth/password-policy" },
  { method: "post", path: "/auth/login" },
  { method: "post", path: "/auth/register" },
  { method: "put", path: "/applications/master-cv" },
  { method: "get", path: "/applications/master-cv" },
  { method: "get", path: "/applications" },
  { method: "get", path: "/applications/{id}" },
  { method: "post", path: "/applications" },
  { method: "get", path: "/applications/preparations/{run_id}" },
  { method: "patch", path: "/applications/{id}" },
  { method: "post", path: "/applications/{id}/approve" },
  { method: "post", path: "/applications/{id}/send" },
  { method: "post", path: "/applications/{id}/resolve" },
  { method: "get", path: "/applications/{id}/cv.pdf" },
  { method: "post", path: "/runs/{run_id}/stream-ticket" },
  { method: "get", path: "/applications/mail-mode" },
  { method: "post", path: "/agent/research" },
  { method: "get", path: "/agent/research/{run_id}" },
  { method: "get", path: "/profile" },
  { method: "post", path: "/profile" },
  { method: "get", path: "/profile/knowledge" },
  { method: "post", path: "/profile/knowledge" },
  { method: "delete", path: "/profile/knowledge/{item_id}" },
  { method: "get", path: "/watch/results" },
  { method: "get", path: "/oauth/connections" },
  { method: "get", path: "/oauth/linkedin/authorize-url" },
  { method: "delete", path: "/oauth/{provider}" },
  { method: "get", path: "/content/queue" },
  { method: "post", path: "/content/generate" },
  { method: "patch", path: "/content/{content_id}" },
  { method: "post", path: "/content/{content_id}/publish" },
  { method: "delete", path: "/auth/account" },
];

const normalise = (path: string): string => path.replace(/\{[^}]+\}/g, "{}");

const spec = JSON.parse(readFileSync(OPENAPI_SNAPSHOT, "utf8")) as OpenApiSpec;

function operationOf({ method, path }: Call) {
  const wanted = normalise(path);
  for (const [specPath, operations] of Object.entries(spec.paths)) {
    if (normalise(specPath) === wanted && method in operations) return operations[method];
  }
  return undefined;
}

describe("API client against the backend OpenAPI", () => {
  it("lists every endpoint of the client", () => {
    expect(CLIENT_CALLS).toHaveLength(Object.keys(endpoints).length);
  });

  it.each(CLIENT_CALLS)("$method $path is served by the backend", (call) => {
    expect(operationOf(call)).toBeDefined();
  });

  it("declares the PDF preview as application/pdf", () => {
    const responses =
      operationOf({ method: "get", path: "/applications/{id}/cv.pdf" })?.responses ?? {};
    expect(JSON.stringify(responses["200"])).toContain("application/pdf");
  });

  it("serves the GitHub authorize-url the connections page uses", () => {
    expect(operationOf({ method: "get", path: "/oauth/github/authorize-url" })).toBeDefined();
  });

  it("serves the progress stream that runStream.ts opens with a ticket", () => {
    expect(operationOf({ method: "get", path: "/runs/{run_id}/events" })).toBeDefined();
  });
});
