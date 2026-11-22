import { describe, expect, it, vi } from "vitest";
import { followRun, type FollowedRun } from "./runFollow";
import type { RunStreamHandlers } from "./runStream";

interface Run extends FollowedRun {
  readonly result: string | null;
}

const TERMINAL = ["done", "failed"];
const isTerminal = (status: string): boolean => TERMINAL.includes(status);

/** A stream the test drives: it records its handlers, and reports whether it was closed. */
function fakeStream() {
  let hooks: RunStreamHandlers | null = null;
  const close = vi.fn<() => void>();
  const open = vi.fn((_runId: string, handlers: RunStreamHandlers) => {
    hooks = handlers;
    return close;
  });
  return {
    open,
    close,
    hooks: (): RunStreamHandlers => {
      if (hooks === null) throw new Error("the stream was not opened");
      return hooks;
    },
  };
}

function handlersSpy() {
  return {
    onUpdate: vi.fn<(run: Run) => void>(),
    onSettled: vi.fn<(run: Run) => void>(),
    onError: vi.fn<(reason: string) => void>(),
  };
}

describe("following a run", () => {
  it("reads the run after each stream event and settles on the terminal status", async () => {
    const stream = fakeStream();
    const fetchRun = vi
      .fn<(id: string) => Promise<Run>>()
      .mockResolvedValueOnce({ status: "running", result: null })
      .mockResolvedValueOnce({ status: "done", result: "answer" });
    const spy = handlersSpy();

    followRun("run-1", { fetchRun, isTerminal, stream: stream.open }, spy);
    await vi.waitFor(() => {
      expect(spy.onUpdate).toHaveBeenCalledTimes(1);
    });

    stream.hooks().onDone("done");
    await vi.waitFor(() => {
      expect(spy.onSettled).toHaveBeenCalledWith({ status: "done", result: "answer" });
    });
    expect(stream.close).toHaveBeenCalled();
  });

  it("falls back to polling when the stream cannot be opened", async () => {
    const stream = fakeStream();
    const fetchRun = vi
      .fn<(id: string) => Promise<Run>>()
      .mockResolvedValueOnce({ status: "running", result: null })
      .mockResolvedValueOnce({ status: "running", result: null })
      .mockResolvedValue({ status: "done", result: "answer" });
    const spy = handlersSpy();

    followRun("run-2", { fetchRun, isTerminal, stream: stream.open, pollMs: 0 }, spy);
    await vi.waitFor(() => {
      expect(spy.onUpdate).toHaveBeenCalledTimes(1);
    });
    stream.hooks().onFailure("ticket_refused");

    await vi.waitFor(() => {
      expect(spy.onSettled).toHaveBeenCalled();
    });
    expect(fetchRun.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(stream.close).toHaveBeenCalled();
  });

  it("stops with an error, and does not poll, when the run is not found", async () => {
    const stream = fakeStream();
    const fetchRun = vi
      .fn<(id: string) => Promise<Run>>()
      .mockResolvedValue({ status: "running", result: null });
    const spy = handlersSpy();

    followRun("run-3", { fetchRun, isTerminal, stream: stream.open, pollMs: 0 }, spy);
    await vi.waitFor(() => {
      expect(spy.onUpdate).toHaveBeenCalledTimes(1);
    });
    stream.hooks().onFailure("not_found");

    expect(spy.onError).toHaveBeenCalledWith("not_found");
    const readsSoFar = fetchRun.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchRun.mock.calls.length).toBe(readsSoFar);
  });

  it("stops at once when the caller stops following, and reads nothing more", async () => {
    const stream = fakeStream();
    const fetchRun = vi
      .fn<(id: string) => Promise<Run>>()
      .mockResolvedValue({ status: "running", result: null });
    const spy = handlersSpy();

    const stop = followRun("run-4", { fetchRun, isTerminal, stream: stream.open, pollMs: 0 }, spy);
    await vi.waitFor(() => {
      expect(spy.onUpdate).toHaveBeenCalledTimes(1);
    });
    stop();

    expect(stream.close).toHaveBeenCalled();
    const readsSoFar = fetchRun.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetchRun.mock.calls.length).toBe(readsSoFar);
    expect(spy.onSettled).not.toHaveBeenCalled();
  });

  it("reports a failed read as fetch_failed and stops", async () => {
    const stream = fakeStream();
    const fetchRun = vi.fn<(id: string) => Promise<Run>>().mockRejectedValue(new Error("network"));
    const spy = handlersSpy();

    followRun("run-5", { fetchRun, isTerminal, stream: stream.open }, spy);
    await vi.waitFor(() => {
      expect(spy.onError).toHaveBeenCalledWith("fetch_failed");
    });
    expect(stream.close).toHaveBeenCalled();
  });
});
