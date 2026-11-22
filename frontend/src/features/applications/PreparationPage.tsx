import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { Loading } from "../../components/Loading";
import { useI18n } from "../../i18n/I18nProvider";
import { applicationKeys } from "./keys";
import {
  POLL_INTERVAL_MS,
  TICK_INTERVAL_MS,
  formatElapsed,
  isKnownStep,
  isTerminalRun,
} from "./preparation";

/** Follows a background preparation until it needs the user. Leaving the page does not stop the run. */
export function PreparationPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { runId = "" } = useParams();
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, TICK_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const query = useQuery({
    queryKey: applicationKeys.preparation(runId),
    queryFn: ({ signal }) => endpoints.getPreparation(runId, signal),
    enabled: runId !== "",
    refetchInterval: (current) =>
      isTerminalRun(current.state.data?.status) ? false : POLL_INTERVAL_MS,
  });

  const status = query.data;

  useEffect(() => {
    if (status?.status === "awaiting_review" && status.application_id !== null) {
      void navigate(`/applications/${String(status.application_id)}`, { replace: true });
    }
  }, [status, navigate]);

  if (query.isError) return <ApiErrorMessage error={query.error} />;
  if (status === undefined) return <Loading />;

  const stepText =
    status.step !== null && isKnownStep(status.step)
      ? t(`progress.steps.${status.step}`)
      : t("progress.unknownStep");

  return (
    <section>
      <h1>{t("progress.title")}</h1>
      <p>{status.status === "queued" ? t("progress.queued") : t("progress.running")}</p>
      <p role="status" aria-live="polite">
        <strong>{t("progress.stepLabel")}: </strong>
        {stepText}
      </p>
      <p className="muted">{t("common.elapsed", { time: formatElapsed(now - startedAt) })}</p>

      {status.stalled ? <Banner tone="warning">{t("progress.stalled")}</Banner> : null}

      {status.status === "failed" ? (
        <Banner tone="danger">
          {status.error
            ? t("progress.failed", { error: status.error })
            : t("progress.failedGeneric")}
        </Banner>
      ) : null}

      {status.status === "closed" ? <Banner tone="warning">{t("progress.closed")}</Banner> : null}

      {status.status === "failed" || status.status === "closed" ? (
        <p>
          <Link to="/applications">{t("progress.backToList")}</Link>
        </p>
      ) : null}
    </section>
  );
}
