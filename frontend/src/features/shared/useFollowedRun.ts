import { useEffect, useState } from "react";
import { followRun, type FollowedRun } from "../../api/runFollow";
import type { StreamFailure } from "../../api/runStream";

export type FollowFailure = StreamFailure | "fetch_failed";

export interface FollowedState<T> {
  readonly run: T | null;
  readonly failure: FollowFailure | null;
  readonly settled: boolean;
}

/** Follows a background run while the component is mounted: over the progress stream when it works, polling otherwise.
 * `fetchRun` and `isTerminal` must be stable (module-level), or the following restarts on every render. The caller
 * keys the component by run id, so a new run starts from an empty state. */
export function useFollowedRun<T extends FollowedRun>(
  runId: string,
  fetchRun: (runId: string) => Promise<T>,
  isTerminal: (status: string) => boolean,
): FollowedState<T> {
  const [run, setRun] = useState<T | null>(null);
  const [failure, setFailure] = useState<FollowFailure | null>(null);
  const [settled, setSettled] = useState(false);

  useEffect(
    () =>
      followRun(
        runId,
        { fetchRun, isTerminal },
        {
          onUpdate: (current) => {
            setRun(current);
          },
          onSettled: () => {
            setSettled(true);
          },
          onError: (reason) => {
            setFailure(reason);
          },
        },
      ),
    [runId, fetchRun, isTerminal],
  );

  return { run, failure, settled };
}
