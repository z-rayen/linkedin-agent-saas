import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configureClient } from "./client";
import { endpoints } from "./endpoints";
import { ApiError } from "./errors";
import {
  openRunStream,
  type EventSourceLike,
  type RunStreamHandlers,
  type RunStreamOptions,
  type StreamFailure,
} from "./runStream";
import type { RunSnapshot, RunStatus } from "./types";

vi.mock("./endpoints", () => ({
  endpoints: { streamTicket: vi.fn() },
}));

const RUN_ID = "run-abc";
const TOKEN = "header.payload.signature";
const NO_DELAY = () => 0;

/** A stand-in for EventSource: the test decides what the server sends and when the connection drops. */
class FakeSource implements EventSourceLike {
  readonly listeners = new Map<string, Array<(event: Event) => void>>();
  closed = false;

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  send(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  connectionError(): void {
    const event = new Event("error");
    for (const listener of this.listeners.get("error") ?? []) listener(event);
  }
}

let sources: FakeSource[];
let handlers: RunStreamHandlers;
const streamTicket = vi.mocked(endpoints.streamTicket);

function ticketsInOrder(...tickets: string[]): void {
  for (const ticket of tickets) {
    streamTicket.mockResolvedValueOnce({ ticket, expires_in: 60, events: "" });
  }
}

function createSource(url: string): EventSourceLike {
  const source = new FakeSource(url);
  sources.push(source);
  return source;
}

function open(options: Partial<RunStreamOptions> = {}) {
  return openRunStream(RUN_ID, handlers, {
    createEventSource: createSource,
    reconnectDelayMs: NO_DELAY,
    ...options,
  });
}

const snapshot: RunSnapshot = { status: "running", step: "tailor_cv", stalled: false };

beforeEach(() => {
  sources = [];
  handlers = {
    onProgress: vi.fn<(value: RunSnapshot) => void>(),
    onDone: vi.fn<(status: RunStatus) => void>(),
    onFailure: vi.fn<(reason: StreamFailure) => void>(),
  };
  streamTicket.mockReset();
  configureClient({ token: () => TOKEN, onUnauthorized: () => undefined });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("run progress stream", () => {
  it("opens the stream with a single-use ticket, and never with the login token", async () => {
    ticketsInOrder("ticket-1");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    expect(sources[0]?.url).toBe(`/api/runs/${RUN_ID}/events?ticket=ticket-1`);
    expect(sources[0]?.url).not.toContain(TOKEN);
    expect(sources[0]?.url).not.toMatch(/token|bearer/i);
  });

  it("delivers progress, then closes for good on done, without reconnecting", async () => {
    ticketsInOrder("ticket-1");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.send("progress", snapshot);
    expect(handlers.onProgress).toHaveBeenCalledWith(snapshot);

    sources[0]?.send("done", { status: "awaiting_review" });
    expect(handlers.onDone).toHaveBeenCalledWith("awaiting_review");
    expect(sources[0]?.closed).toBe(true);
    expect(streamTicket).toHaveBeenCalledTimes(1);
  });

  it("reconnects after a dropped connection with a new ticket, because each ticket works once", async () => {
    ticketsInOrder("ticket-1", "ticket-2");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.connectionError();
    expect(sources[0]?.closed).toBe(true);

    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(sources[1]?.url).toContain("ticket=ticket-2");
    sources[1]?.send("progress", snapshot);
    expect(handlers.onProgress).toHaveBeenCalledWith(snapshot);
  });

  it("gives up with connection_lost once the reconnection limit is reached", async () => {
    ticketsInOrder("t-1", "t-2", "t-3");
    open({ maxReconnects: 2 });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.connectionError();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    sources[1]?.connectionError();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(3);
    });
    sources[2]?.connectionError();

    await vi.waitFor(() => {
      expect(handlers.onFailure).toHaveBeenCalledWith("connection_lost");
    });
    expect(streamTicket).toHaveBeenCalledTimes(3);
  });

  it("starts the reconnection count again after every progress event", async () => {
    ticketsInOrder("t-1", "t-2", "t-3");
    open({ maxReconnects: 1 });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.connectionError();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    sources[1]?.send("progress", snapshot);
    sources[1]?.connectionError();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(3);
    });

    expect(handlers.onFailure).not.toHaveBeenCalled();
  });

  it("reopens a stream the server ended on timeout, since the run may still be going", async () => {
    ticketsInOrder("t-1", "t-2");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.send("timeout", { status: "still running" });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(handlers.onDone).not.toHaveBeenCalled();
  });

  it("stops at once, with not_found, when the server says the run does not exist", async () => {
    ticketsInOrder("t-1");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    sources[0]?.send("error", { status: "not_found" });
    expect(handlers.onFailure).toHaveBeenCalledWith("not_found");
    expect(sources[0]?.closed).toBe(true);
    expect(streamTicket).toHaveBeenCalledTimes(1);
  });

  it("closes the stream and stops reconnecting when the caller cancels", async () => {
    ticketsInOrder("t-1", "t-2");
    const controller = new AbortController();
    open({ signal: controller.signal });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });

    controller.abort();
    expect(sources[0]?.closed).toBe(true);
    sources[0]?.connectionError();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sources).toHaveLength(1);
    expect(streamTicket).toHaveBeenCalledTimes(1);
  });

  it("reports ticket_refused and opens nothing when the ticket cannot be issued", async () => {
    streamTicket.mockRejectedValueOnce(
      new ApiError({ status: 401, code: "authentication_failed", message: "" }),
    );
    open();
    await vi.waitFor(() => {
      expect(handlers.onFailure).toHaveBeenCalledWith("ticket_refused");
    });
    expect(sources).toHaveLength(0);
  });

  it("retries the ticket request after a network failure", async () => {
    streamTicket.mockRejectedValueOnce(
      new ApiError({ status: 0, code: "network", message: "network" }),
    );
    ticketsInOrder("t-2");
    open();
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });
    expect(sources[0]?.url).toContain("ticket=t-2");
    expect(handlers.onFailure).not.toHaveBeenCalled();
  });
});
