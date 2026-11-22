import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiBlob, apiRequest, configureClient } from "./client";
import { ApiError } from "./errors";

const TOKEN = "header.payload.signature";
const API_BASE = "/api";

type FetchMock = ReturnType<typeof vi.fn<(input: string, init?: RequestInit) => Promise<Response>>>;

let unauthorized: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  unauthorized = vi.fn<() => void>();
  configureClient({ token: () => TOKEN, onUnauthorized: unauthorized });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(next: Response | Error | DOMException): FetchMock {
  const fetchMock: FetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    () => {
      if (next instanceof Response) return Promise.resolve(next.clone());
      return Promise.reject(next);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function errorOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("the request was expected to fail");
}

describe("API client", () => {
  it("sends the token in the Authorization header, and never in the URL", async () => {
    const fetchMock = stubFetch(json([], 200));
    await apiRequest<unknown[]>("/applications");
    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe(`${API_BASE}/applications`);
    expect(call?.[0]).not.toContain(TOKEN);
    const headers = call?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("does not send the token to public endpoints", async () => {
    const fetchMock = stubFetch(json({ access_token: "t", token_type: "bearer" }, 200));
    await apiRequest("/auth/login", {
      method: "POST",
      body: { email: "a@b.c", password: "x" },
      auth: false,
    });
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("turns a 422 into field errors, with the body's own location dropped from the path", async () => {
    stubFetch(json({ detail: [{ loc: ["body", "full_name"], msg: "Field required" }] }, 422));
    await expect(
      apiRequest("/applications/master-cv", { method: "PUT", body: {} }),
    ).rejects.toMatchObject({
      status: 422,
      code: "http_error",
      fieldErrors: [{ path: "full_name", message: "Field required" }],
    });
  });

  it("keeps the problems of a 409 refusal, with their code", async () => {
    stubFetch(
      json(
        {
          code: "cv_not_grounded",
          detail: "refused",
          problems: [
            { kind: "number", location: "line 1", term: "40%", message: "not in the master CV" },
          ],
        },
        409,
      ),
    );
    await expect(apiRequest("/applications/1/approve", { method: "POST" })).rejects.toMatchObject({
      status: 409,
      code: "cv_not_grounded",
      problems: [{ kind: "number", location: "line 1", term: "40%" }],
    });
  });

  it("reports a network failure as a network error, not as a crash", async () => {
    stubFetch(new TypeError("Failed to fetch"));
    const error = await errorOf(apiRequest("/applications"));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "network" });
  });

  it("ends the session on a 401 from an authenticated request", async () => {
    stubFetch(json({ detail: "expired" }, 401));
    await expect(apiRequest("/applications")).rejects.toBeInstanceOf(ApiError);
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not end the session on a wrong password at login", async () => {
    stubFetch(json({ detail: "wrong" }, 401));
    await expect(apiRequest("/auth/login", { method: "POST", auth: false })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("returns nothing for a 204 response", async () => {
    stubFetch(new Response(null, { status: 204 }));
    await expect(
      apiRequest("/applications/1", { method: "PATCH", body: {} }),
    ).resolves.toBeUndefined();
  });

  it("passes an abort through unchanged, so the caller can ignore it", async () => {
    stubFetch(new DOMException("aborted", "AbortError"));
    await expect(apiRequest("/applications")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("fetches the PDF preview with the token in the header", async () => {
    const fetchMock = stubFetch(new Response(new Blob(["%PDF-1.4"]), { status: 200 }));
    const blob = await apiBlob("/applications/3/cv.pdf");
    expect(blob).toBeInstanceOf(Blob);
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });
});
