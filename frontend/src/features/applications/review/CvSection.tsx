import { useEffect, useId, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Problem } from "../../../api/errors";
import { endpoints } from "../../../api/endpoints";
import type { ApplicationDetail } from "../../../api/types";
import { Banner } from "../../../components/Banner";
import { useI18n } from "../../../i18n/I18nProvider";
import { applicationKeys } from "../keys";
import { ProblemList } from "./ProblemList";
import styles from "./review.module.css";

interface Props {
  readonly detail: ApplicationDetail;
  readonly problemsByLine: ReadonlyMap<number, readonly Problem[]>;
}

/** The tailored CV: each line with its sources, the problems of that line, and the PDF preview. */
export function CvSection({ detail, problemsByLine }: Props) {
  const { t } = useI18n();
  const headingId = useId();

  const pdf = useQuery({
    queryKey: applicationKeys.pdf(detail.id, detail.pdf_sha256),
    queryFn: ({ signal }) => endpoints.pdfBlob(detail.id, signal),
    enabled: detail.has_pdf,
  });

  const pdfUrl = useMemo(() => (pdf.data ? URL.createObjectURL(pdf.data) : null), [pdf.data]);

  useEffect(() => {
    return () => {
      if (pdfUrl !== null) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  return (
    <section className="card" aria-labelledby={headingId}>
      <h2 id={headingId}>{t("review.cvSection")}</h2>

      {detail.cv_lines.length === 0 ? (
        <p className="muted">{t("review.cvEmpty")}</p>
      ) : (
        <ol className={styles.lines}>
          {detail.cv_lines.map((line, index) => (
            <li key={index}>
              <p className={styles.lineText}>
                <span className="chip chip-info">{t(`review.cvSections.${line.section}`)}</span>{" "}
                {line.text}
              </p>
              <p className="muted">
                {t("review.sourcesLabel")}: {line.source_ids.join(", ")}
              </p>
              <ProblemList problems={problemsByLine.get(index) ?? []} />
            </li>
          ))}
        </ol>
      )}

      <h3>{t("review.pdfSection")}</h3>
      {!detail.has_pdf ? <p className="muted">{t("review.pdfMissing")}</p> : null}
      {pdf.isError ? <Banner tone="danger">{t("review.pdfError")}</Banner> : null}
      {pdfUrl !== null ? (
        <iframe className={styles.pdf} title={t("review.pdfSection")} src={pdfUrl} />
      ) : null}
    </section>
  );
}
