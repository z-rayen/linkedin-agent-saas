import { useId } from "react";
import type { Problem } from "../../../api/errors";
import { Banner } from "../../../components/Banner";
import { useI18n } from "../../../i18n/I18nProvider";
import { ProblemList } from "./ProblemList";

interface Props {
  /** Every problem of the application, wherever it is shown. */
  readonly total: number;
  /** The problems that are not attached to the email or to a CV line. */
  readonly problems: readonly Problem[];
}

/** The summary of grounding problems. Each one is also shown beside its part, so nothing is listed twice. */
export function ProblemsSection({ total, problems }: Props) {
  const { t } = useI18n();
  const headingId = useId();

  if (total === 0) {
    return <Banner tone="success">{t("review.noProblems")}</Banner>;
  }

  return (
    <section className="card" aria-labelledby={headingId}>
      <h2 id={headingId}>{t("review.problemsSection")}</h2>
      <p className="muted">{t("review.problemsIntro")}</p>
      <ProblemList problems={problems} />
    </section>
  );
}
