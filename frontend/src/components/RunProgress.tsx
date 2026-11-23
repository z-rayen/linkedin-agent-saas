import { useEffect, useState } from "react";
import { Banner } from "./Banner";
import styles from "./RunProgress.module.css";

const TICK_MS = 1000;
const MS_PER_SECOND = 1000;
const SECONDS_PER_MINUTE = 60;

export function formatElapsed(milliseconds: number): string {
  const total = Math.max(0, Math.floor(milliseconds / MS_PER_SECOND));
  const minutes = Math.floor(total / SECONDS_PER_MINUTE);
  const seconds = total % SECONDS_PER_MINUTE;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

interface Props {
  readonly statusText: string;
  readonly stepText: string;
  readonly elapsedLabel: (time: string) => string;
  readonly stalled: boolean;
  readonly stalledText: string;
}

/** Progress of a background run, shared by the research, the preparation and the watch: the status, the current step,
 * the elapsed time (counted from when the screen was opened) and a warning when the run stopped reporting. */
export function RunProgress({ statusText, stepText, elapsedLabel, stalled, stalledText }: Props) {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, TICK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className={styles.progress}>
      <p role="status" aria-live="polite">
        <strong>{statusText}</strong>
        {stepText ? <span>{` · ${stepText}`}</span> : null}
      </p>
      <p className="muted">{elapsedLabel(formatElapsed(now - startedAt))}</p>
      {stalled ? <Banner tone="warning">{stalledText}</Banner> : null}
    </div>
  );
}
