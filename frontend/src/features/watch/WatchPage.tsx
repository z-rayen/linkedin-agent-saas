import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import type { WatchKind, WatchResult, WatchResultsQuery, WatchRun } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { RunProgress } from "../../components/RunProgress";
import { useI18n, type MessageKey, type Translate } from "../../i18n/I18nProvider";
import { safeWebUrl } from "../../lib/safeUrl";
import { useFollowedRun } from "../shared/useFollowedRun";
import { watchKeys } from "./keys";

export const PAGE_SIZE = 20;
const MAX_SCORE = 100;
const FINISHED: readonly string[] = ["done", "failed"];
const WATCH_STEPS = ["search", "dedupe", "read", "score", "route", "persist", "resuming"] as const;

interface Filters {
  readonly kind: "" | WatchKind;
  readonly minScore: string;
  readonly verified: "all" | "yes" | "no";
}

const NO_FILTERS: Filters = { kind: "", minScore: "", verified: "all" };

/** The watch: start a run, follow it, then read the stored results with filters and pages. "Prepare application" only
 * opens the application form, prefilled. The preparation itself stays a reviewed step. */
export function WatchPage() {
  const { t } = useI18n();
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [offset, setOffset] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const filtersId = useId();

  const query: WatchResultsQuery = {
    limit: PAGE_SIZE,
    offset,
    ...(filters.kind === "" ? {} : { kind: filters.kind }),
    ...(filters.minScore.trim() === "" ? {} : { minScore: Number(filters.minScore) }),
    ...(filters.verified === "all" ? {} : { verified: filters.verified === "yes" }),
  };

  const results = useQuery({
    queryKey: watchKeys.results(query),
    queryFn: ({ signal }) => endpoints.watchResults(query, signal),
    placeholderData: keepPreviousData,
  });

  const start = useMutation({
    mutationFn: () => endpoints.startWatchRun(),
    onSuccess: (started) => {
      setRunId(started.run_id);
    },
  });

  function updateFilters(next: Filters) {
    setFilters(next);
    setOffset(0);
  }

  const total = results.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const filtered = filters !== NO_FILTERS;

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("watch.title")}</h1>
          <p className="muted">{t("watch.intro")}</p>
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={start.isPending}
          onClick={() => {
            start.mutate();
          }}
        >
          {t("watch.start")}
        </button>
      </div>
      <p className="hint">{t("watch.startHint")}</p>
      <ApiErrorMessage error={start.error} />

      {runId !== null ? <WatchRunPanel key={runId} runId={runId} /> : null}

      <Banner tone="warning">{t("watch.reminder")}</Banner>

      <form
        className="card"
        aria-labelledby={`${filtersId}-title`}
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <h2 id={`${filtersId}-title`}>{t("watch.filters")}</h2>
        <div className="actions">
          <label>
            {t("watch.filterKind")}{" "}
            <select
              className="input"
              value={filters.kind}
              onChange={(event) => {
                updateFilters({ ...filters, kind: event.target.value as Filters["kind"] });
              }}
            >
              <option value="">{t("watch.kindAll")}</option>
              <option value="job">{t("watch.typeJob")}</option>
              <option value="cert">{t("watch.typeCert")}</option>
            </select>
          </label>
          <label>
            {t("watch.filterScore")}{" "}
            <input
              className="input"
              type="number"
              min={0}
              max={MAX_SCORE}
              value={filters.minScore}
              onChange={(event) => {
                updateFilters({ ...filters, minScore: event.target.value });
              }}
            />
          </label>
          <label>
            {t("watch.filterVerified")}{" "}
            <select
              className="input"
              value={filters.verified}
              onChange={(event) => {
                updateFilters({ ...filters, verified: event.target.value as Filters["verified"] });
              }}
            >
              <option value="all">{t("watch.verifiedAll")}</option>
              <option value="yes">{t("watch.verifiedYes")}</option>
              <option value="no">{t("watch.verifiedNo")}</option>
            </select>
          </label>
        </div>
      </form>

      {results.isPending ? <Loading /> : null}
      {results.isError ? <ApiErrorMessage error={results.error} /> : null}
      {results.isSuccess && results.data.items.length === 0 ? (
        <EmptyState>{filtered ? t("watch.emptyFiltered") : t("watch.empty")}</EmptyState>
      ) : null}

      {results.isSuccess && results.data.items.length > 0 ? (
        <ul className="stack">
          {results.data.items.map((result) => (
            <WatchItem key={result.id} result={result} />
          ))}
        </ul>
      ) : null}

      {results.isSuccess && total > PAGE_SIZE ? (
        <nav className="actions" aria-label={t("watch.pages")}>
          <button
            type="button"
            className="button"
            disabled={offset === 0}
            onClick={() => {
              setOffset(Math.max(0, offset - PAGE_SIZE));
            }}
          >
            {t("watch.previous")}
          </button>
          <span>{t("watch.pageOf", { page, pages })}</span>
          <button
            type="button"
            className="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => {
              setOffset(offset + PAGE_SIZE);
            }}
          >
            {t("watch.next")}
          </button>
        </nav>
      ) : null}
    </section>
  );
}

const isFinished = (status: string): boolean => FINISHED.includes(status);

/** One watch run, followed to its end. When it ends, the stored results are read again. */
function WatchRunPanel({ runId }: { readonly runId: string }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const followed = useFollowedRun<WatchRun>(runId, endpoints.getWatchRun, isFinished);

  useEffect(() => {
    if (followed.settled) void queryClient.invalidateQueries({ queryKey: watchKeys.all });
  }, [followed.settled, queryClient]);

  const run = followed.run;
  return (
    <section className="card" aria-label={t("watch.runTitle")}>
      <h2>{t("watch.runTitle")}</h2>
      {followed.failure !== null ? <Banner tone="danger">{t("watch.runFailed")}</Banner> : null}
      {run === null && followed.failure === null ? <Loading /> : null}
      {run !== null ? (
        <>
          <RunProgress
            statusText={statusLabel(run.status, t)}
            stepText={
              run.step !== null && isWatchStep(run.step) ? t(`watch.steps.${run.step}`) : ""
            }
            elapsedLabel={(time) => t("common.elapsed", { time })}
            stalled={run.stalled && !isFinished(run.status)}
            stalledText={t("common.runStalled")}
          />
          {run.status === "done" ? <Banner tone="success">{t("watch.runDone")}</Banner> : null}
          {run.status === "failed" ? <Banner tone="danger">{t("watch.runFailed")}</Banner> : null}
        </>
      ) : null}
    </section>
  );
}

function statusLabel(status: string, t: Translate): string {
  if (status === "queued") return t("common.runQueued");
  if (status === "done") return t("watch.runDone");
  if (status === "failed") return t("watch.runFailed");
  return t("common.runRunning");
}

function isWatchStep(step: string): step is (typeof WATCH_STEPS)[number] {
  return (WATCH_STEPS as readonly string[]).includes(step);
}

export function typeLabel(type: WatchResult["type"], t: (key: MessageKey) => string): string {
  if (type === "job") return t("watch.typeJob");
  if (type === "cert") return t("watch.typeCert");
  return t("watch.typeOther");
}

/** The query string that opens the application form with this offer filled in: the title and the link only. The
 * recipient is never guessed, and the preparation reads the offer from the link, under the user's review. */
export function prepareQuery(result: WatchResult): string {
  return new URLSearchParams({ job_title: result.title, job_url: result.link }).toString();
}

function WatchItem({ result }: { readonly result: WatchResult }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const href = safeWebUrl(result.link);
  const remove = useMutation({
    mutationFn: () => endpoints.deleteWatchResult(result.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: watchKeys.all });
    },
  });

  return (
    <li className="card">
      <p>
        <span className="chip chip-info">{typeLabel(result.type, t)}</span>{" "}
        <strong>{result.title}</strong>{" "}
        <span className={result.unverified ? "chip chip-warning" : "chip chip-success"}>
          {result.unverified ? t("watch.unverified") : t("watch.verified")}
        </span>
      </p>
      <p className="muted">{t("watch.score", { score: result.match_score })}</p>
      {result.company !== null ? <p>{t("watch.company", { company: result.company })}</p> : null}
      {result.job_title !== null ? <p>{t("watch.jobTitle", { title: result.job_title })}</p> : null}
      {result.justification ? (
        <details>
          <summary>{t("watch.justification")}</summary>
          <p>{result.justification}</p>
        </details>
      ) : null}
      <div className="actions">
        {href !== null ? (
          <a className="button" href={href} target="_blank" rel="noopener noreferrer">
            {t("watch.open")}
          </a>
        ) : null}
        {result.type === "job" ? (
          <Link className="button button-primary" to={`/applications/new?${prepareQuery(result)}`}>
            {t("watch.prepare")}
          </Link>
        ) : null}
        {confirming ? (
          <>
            <span>{t("watch.confirmDelete")}</span>
            <button
              type="button"
              className="button"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              {t("watch.delete")}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirming(false);
              }}
            >
              {t("common.cancel")}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="button"
            onClick={() => {
              setConfirming(true);
            }}
          >
            {t("watch.remove")}
          </button>
        )}
      </div>
      <ApiErrorMessage error={remove.error} />
    </li>
  );
}
