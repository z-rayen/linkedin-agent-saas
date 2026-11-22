/** Follows a background run to its end: over the progress stream when it works, by polling when it does not.
 *
 * The stream carries status and step only, so every event (and the final `done`) is followed by a read of the run
 * itself, which holds the result. When the stream cannot be opened or keeps failing, the run is read every few seconds
 * instead. A run the server does not know (not_found) is an error, not a reason to poll forever.
 */
import { openRunStream, type RunStreamHandlers, type StreamFailure } from "./runStream";

export const DEFAULT_POLL_MS = 2000;

export interface FollowedRun {
  readonly status: string;
}

export interface FollowDeps<T extends FollowedRun> {
  readonly fetchRun: (runId: string) => Promise<T>;
  readonly isTerminal: (status: string) => boolean;
  /** Opens the progress stream. Defaults to the real one; tests replace it. */
  readonly stream?: (runId: string, handlers: RunStreamHandlers) => () => void;
  readonly pollMs?: number;
}

export interface FollowHandlers<T extends FollowedRun> {
  readonly onUpdate: (run: T) => void;
  readonly onSettled: (run: T) => void;
  readonly onError: (reason: StreamFailure | "fetch_failed") => void;
}

/** Starts following a run. Returns a function that stops following for good. */
export function followRun<T extends FollowedRun>(
  runId: string,
  deps: FollowDeps<T>,
  handlers: FollowHandlers<T>,
): () => void {
  const openStream =
    deps.stream ?? ((id: string, hooks: RunStreamHandlers) => openRunStream(id, hooks));
  const pollMs = deps.pollMs ?? DEFAULT_POLL_MS;
  let stopped = false as boolean;
  // A function call: TypeScript must not narrow the flag across the awaits below.
  const isStopped = (): boolean => stopped;
  let closeStream: () => void = () => undefined;
  let timer: ReturnType<typeof setInterval> | null = null;

  function stop(): void {
    stopped = true;
    closeStream();
    if (timer !== null) clearInterval(timer);
    timer = null;
  }

  async function refresh(): Promise<void> {
    if (isStopped()) return;
    let run: T;
    try {
      run = await deps.fetchRun(runId);
    } catch {
      if (!isStopped()) {
        stop();
        handlers.onError("fetch_failed");
      }
      return;
    }
    if (isStopped()) return;
    handlers.onUpdate(run);
    if (deps.isTerminal(run.status)) {
      stop();
      handlers.onSettled(run);
    }
  }

  function startPolling(): void {
    if (isStopped() || timer !== null) return;
    closeStream();
    timer = setInterval(() => {
      void refresh();
    }, pollMs);
  }

  closeStream = openStream(runId, {
    onProgress: () => {
      void refresh();
    },
    onDone: () => {
      void refresh();
    },
    onFailure: (reason) => {
      if (reason === "not_found") {
        stop();
        handlers.onError(reason);
        return;
      }
      startPolling();
    },
  });

  void refresh();
  return stop;
}
