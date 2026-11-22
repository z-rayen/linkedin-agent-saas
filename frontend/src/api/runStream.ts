/** Progress of a background run over server-sent events, without polling.
 *
 * The stream is opened with a single-use ticket from POST /runs/{id}/stream-ticket, so the login token never reaches a
 * URL. A ticket works once: every reconnection asks for a new one. A dropped connection is reopened after a delay that
 * grows with each failed attempt, and the count starts again after every progress event, so a long run that loses its
 * connection now and then is not given up on. The stream ends on `done`, on cancellation, or when reconnection fails.
 */
import { apiUrl } from "./client";
import { endpoints } from "./endpoints";
import { ApiError } from "./errors";
import type { RunSnapshot, RunStatus, StreamTicket } from "./types";

export type StreamFailure = "not_found" | "ticket_refused" | "connection_lost";

/** The part of EventSource the stream uses. Injected in tests, where EventSource does not exist. */
export interface EventSourceLike {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

export interface RunStreamHandlers {
  readonly onProgress: (snapshot: RunSnapshot) => void;
  readonly onDone: (status: RunStatus) => void;
  readonly onFailure: (reason: StreamFailure) => void;
}

export interface RunStreamOptions {
  readonly signal?: AbortSignal;
  readonly maxReconnects?: number;
  readonly reconnectDelayMs?: (attempt: number) => number;
  readonly createEventSource?: (url: string) => EventSourceLike;
}

const DEFAULT_MAX_RECONNECTS = 5;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30_000;
const RUN_STATUSES: readonly RunStatus[] = [
  "queued",
  "running",
  "awaiting_review",
  "closed",
  "failed",
];
const NOT_FOUND = "not_found";

/** 1 s, 2 s, 4 s, ... capped at 30 s. `attempt` starts at 1. */
export function defaultReconnectDelay(attempt: number): number {
  return Math.min(BASE_DELAY_MS * 2 ** (attempt - 1), MAX_DELAY_MS);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readData(event: Event): Record<string, unknown> | null {
  if (!("data" in event) || typeof event.data !== "string") return null;
  try {
    const parsed: unknown = JSON.parse(event.data);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function isRunStatus(value: unknown): value is RunStatus {
  return typeof value === "string" && (RUN_STATUSES as readonly string[]).includes(value);
}

function snapshotOf(event: Event): RunSnapshot | null {
  const data = readData(event);
  if (data === null || !isRunStatus(data.status)) return null;
  if (typeof data.step !== "string" || typeof data.stalled !== "boolean") return null;
  return { status: data.status, step: data.step, stalled: data.stalled };
}

/** Server-sent events named "error" carry data (the server's own error). The browser's connection error does not. */
function isServerError(event: Event): boolean {
  return readData(event) !== null;
}

/** Opens the progress stream of a run. Returns a function that closes it for good. */
export function openRunStream(
  runId: string,
  handlers: RunStreamHandlers,
  options: RunStreamOptions = {},
): () => void {
  const maxReconnects = options.maxReconnects ?? DEFAULT_MAX_RECONNECTS;
  const delayFor = options.reconnectDelayMs ?? defaultReconnectDelay;
  const createSource =
    options.createEventSource ?? ((url: string): EventSourceLike => new EventSource(url));

  let source: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  // Annotated: the closures change it, and TypeScript must not narrow it to the literal false after an await.
  let closed = false as boolean;
  let attempts = 0;

  // A function call: TypeScript does not narrow across it, so the checks after each await see the current value.
  const isClosed = (): boolean => closed;

  function stop(): void {
    closed = true;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    source?.close();
    source = null;
  }

  function scheduleReconnect(): void {
    if (isClosed()) return;
    attempts += 1;
    if (attempts > maxReconnects) {
      stop();
      handlers.onFailure("connection_lost");
      return;
    }
    timer = setTimeout(() => {
      timer = null;
      void connect();
    }, delayFor(attempts));
  }

  /** Closes a stream that broke or ended without `done`, and schedules a reconnection with a new ticket. */
  function drop(current: EventSourceLike): void {
    if (isClosed() || source !== current) return;
    current.close();
    source = null;
    scheduleReconnect();
  }

  async function connect(): Promise<void> {
    if (isClosed()) return;
    let ticket: StreamTicket;
    try {
      ticket = await endpoints.streamTicket(runId);
    } catch (error) {
      if (isClosed()) return;
      if (error instanceof ApiError && error.isNetwork) {
        scheduleReconnect();
        return;
      }
      stop();
      handlers.onFailure(
        error instanceof ApiError && error.status === 404 ? "not_found" : "ticket_refused",
      );
      return;
    }
    if (isClosed()) return;

    const url = `${apiUrl(`/runs/${encodeURIComponent(runId)}/events`)}?ticket=${encodeURIComponent(ticket.ticket)}`;
    const current = createSource(url);
    source = current;

    current.addEventListener("progress", (event) => {
      const snapshot = snapshotOf(event);
      if (snapshot === null) return;
      attempts = 0;
      handlers.onProgress(snapshot);
    });
    current.addEventListener("done", (event) => {
      const data = readData(event);
      if (data === null || !isRunStatus(data.status)) {
        drop(current);
        return;
      }
      stop();
      handlers.onDone(data.status);
    });
    current.addEventListener("timeout", () => {
      // The server ends a very long stream on purpose; the run may still be going, so the stream is opened again.
      attempts = 0;
      drop(current);
    });
    current.addEventListener("error", (event) => {
      if (isServerError(event)) {
        const data = readData(event);
        stop();
        handlers.onFailure(data?.status === NOT_FOUND ? "not_found" : "connection_lost");
        return;
      }
      drop(current);
    });
  }

  options.signal?.addEventListener("abort", stop);
  if (options.signal?.aborted === true) {
    closed = true;
  } else {
    void connect();
  }

  return stop;
}
