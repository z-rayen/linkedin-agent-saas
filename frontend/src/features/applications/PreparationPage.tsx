import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import type { PreparationStatus } from "../../api/types";
import { Banner } from "../../components/Banner";
import { Loading } from "../../components/Loading";
import { RunProgress } from "../../components/RunProgress";
import { useI18n } from "../../i18n/I18nProvider";
import { useFollowedRun } from "../shared/useFollowedRun";
import { isKnownStep } from "./preparation";

/** Preparations end when the application needs the user, or when the workflow closes or fails. */
const PREPARATION_TERMINAL: readonly string[] = ["awaiting_review", "closed", "failed"];

const fetchPreparation = (runId: string): Promise<PreparationStatus> =>
  endpoints.getPreparation(runId);
const isPreparationTerminal = (status: string): boolean => PREPARATION_TERMINAL.includes(status);

/** Follows a background preparation until it needs the user. Leaving the page does not stop the run. */
export function PreparationPage() {
  const { runId = "" } = useParams();
  // Keyed by run: a different run starts from an empty state, never from the previous one's status.
  return <PreparationRun key={runId} runId={runId} />;
}

function PreparationRun({ runId }: { readonly runId: string }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const followed = useFollowedRun<PreparationStatus>(
    runId,
    fetchPreparation,
    isPreparationTerminal,
  );
  const status = followed.run;

  useEffect(() => {
    if (status?.status === "awaiting_review" && status.application_id !== null) {
      void navigate(`/applications/${String(status.application_id)}`, { replace: true });
    }
  }, [status, navigate]);

  if (followed.failure !== null)
    return <Banner tone="danger">{t("progress.failedGeneric")}</Banner>;
  if (status === null) return <Loading />;

  const stepText =
    status.step !== null && isKnownStep(status.step)
      ? t(`progress.steps.${status.step}`)
      : t("progress.unknownStep");

  return (
    <section>
      <h1>{t("progress.title")}</h1>
      <RunProgress
        statusText={status.status === "queued" ? t("progress.queued") : t("progress.running")}
        stepText={stepText}
        elapsedLabel={(time) => t("common.elapsed", { time })}
        stalled={status.stalled}
        stalledText={t("progress.stalled")}
      />

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
