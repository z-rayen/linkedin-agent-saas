/** Errors of the API, as the frontend uses them. The raw body is never shown: only the fields below. */

export interface Problem {
  readonly kind: string;
  readonly location: string;
  readonly term: string;
  readonly message: string;
}

export interface FieldError {
  readonly path: string;
  readonly message: string;
}

export interface ApiErrorInit {
  readonly status: number;
  readonly code: string;
  readonly message: string;
  readonly problems?: readonly Problem[];
  readonly fieldErrors?: readonly FieldError[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly problems: readonly Problem[];
  readonly fieldErrors: readonly FieldError[];

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.problems = init.problems ?? [];
    this.fieldErrors = init.fieldErrors ?? [];
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function parseProblems(value: unknown): Problem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    kind: text(item.kind, "unknown"),
    location: text(item.location, ""),
    term: text(item.term, ""),
    message: text(item.message, ""),
  }));
}

/** FastAPI validation errors: a list of {loc, msg}. The first element of loc is "body" and is dropped from the path. */
function parseFieldErrors(detail: unknown): FieldError[] {
  if (!Array.isArray(detail)) return [];
  return detail.filter(isRecord).map((item) => {
    const loc = Array.isArray(item.loc) ? item.loc.map((part: unknown) => text(part, "")) : [];
    const path = loc.filter((part) => part !== "body").join(".");
    return { path, message: text(item.msg, "") };
  });
}

async function readBody(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

/** Turns a failed response into an ApiError. Never throws: an unreadable body becomes a generic error. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  const body = await readBody(response);
  const record = isRecord(body) ? body : {};
  const detail = record.detail;
  return new ApiError({
    status: response.status,
    code: text(record.code, "http_error"),
    message: typeof detail === "string" ? detail : "",
    problems: parseProblems(record.problems),
    fieldErrors: parseFieldErrors(detail),
  });
}
