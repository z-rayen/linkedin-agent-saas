import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import type { ApplicationDetail } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { StatusChip } from "../../components/StatusChip";
import { useI18n } from "../../i18n/I18nProvider";
import { applicationKeys } from "./keys";
import { EmailSection, type EmailDraft } from "./review/EmailSection";
import { CvSection } from "./review/CvSection";
import { ProblemList } from "./review/ProblemList";
import { ProblemsSection } from "./review/ProblemsSection";
import { ResolveSection } from "./review/ResolveSection";
import { SendSection } from "./review/SendSection";
import styles from "./review/review.module.css";
import { canApprove, groupProblems } from "./review/rules";

/** A remount of the email form when the server state changes, so it always starts from what was saved. */
function sectionKey(detail: ApplicationDetail): string {
  return `${String(detail.id)}:${detail.status}:${detail.pdf_sha256 ?? ""}`;
}

/** Review of one prepared application: the email, the CV, the problems, approval, then sending. */
export function ReviewPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { applicationId = "" } = useParams();
  const id = Number(applicationId);
  const validId = Number.isInteger(id) && id > 0;

  const detailQuery = useQuery({
    queryKey: applicationKeys.detail(id),
    queryFn: ({ signal }) => endpoints.getApplication(id, signal),
    enabled: validId,
  });

  // An error means the mode is unknown and sending stays disabled. Retrying would not change that answer.
  const modeQuery = useQuery({
    queryKey: applicationKeys.mailMode,
    queryFn: ({ signal }) => endpoints.mailMode(signal),
    retry: false,
  });

  const edit = useMutation({
    mutationFn: (draft: EmailDraft) =>
      endpoints.editApplication(id, {
        subject: draft.subject,
        body: draft.body,
        recipient_email: draft.recipient,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(applicationKeys.detail(id), updated);
      void queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });

  const approve = useMutation({
    mutationFn: () => endpoints.approveApplication(id),
    onSuccess: (updated) => {
      queryClient.setQueryData(applicationKeys.detail(id), updated);
      void queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });

  if (!validId) return <EmptyState>{t("common.notFound")}</EmptyState>;
  if (detailQuery.isError) return <ApiErrorMessage error={detailQuery.error} />;
  if (detailQuery.data === undefined) return <Loading />;

  const detail = detailQuery.data;
  const mode = modeQuery.data?.mode ?? null;
  const grouped = groupProblems(detail.grounding_problems);
  const problemCount = detail.grounding_problems.length;
  const approvable = canApprove(detail.status, detail.grounding_problems);
  const refusal = approve.error instanceof ApiError ? approve.error.problems : [];
  const company = detail.company ? ` · ${detail.company}` : "";

  return (
    <article>
      <header className="page-header">
        <div>
          <h1>{t("review.title")}</h1>
          <p className="muted">
            {detail.job_title}
            {company}
          </p>
        </div>
        <StatusChip status={detail.status} />
      </header>

      <div className={styles.columns}>
        <div className={styles.main}>
          <EmailSection
            key={sectionKey(detail)}
            detail={detail}
            problems={grouped.email}
            saving={edit.isPending}
            saved={edit.isSuccess}
            saveError={edit.error}
            onSave={(draft) => {
              edit.mutate(draft);
            }}
          />
          <CvSection detail={detail} problemsByLine={grouped.byLine} />
        </div>

        <aside className={styles.side}>
          <ProblemsSection total={problemCount} problems={grouped.other} />

          <section className="card">
            <div className="actions">
              <button
                type="button"
                className="button button-primary"
                disabled={!approvable || approve.isPending}
                onClick={() => {
                  approve.mutate();
                }}
              >
                {t("review.approve")}
              </button>
            </div>
            {!approvable && problemCount > 0 ? (
              <p className="muted">{t("review.approveBlocked")}</p>
            ) : null}
            {approve.isSuccess ? <Banner tone="success">{t("review.approved")}</Banner> : null}
            {approve.isError ? (
              refusal.length > 0 ? (
                <Banner tone="danger">
                  {t("review.refusedByServer")}
                  <ProblemList problems={refusal} />
                </Banner>
              ) : (
                <ApiErrorMessage error={approve.error} />
              )
            ) : null}
          </section>

          <SendSection detail={detail} mode={mode} />
          <ResolveSection detail={detail} />
        </aside>
      </div>
    </article>
  );
}
