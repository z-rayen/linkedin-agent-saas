import type { Problem } from "../../../api/errors";
import { useI18n } from "../../../i18n/I18nProvider";
import { problemKindKey } from "./rules";
import styles from "./review.module.css";

/** A list of grounding problems: the kind, the term that failed, and the message. The raw location is not shown:
 * the problem is already displayed next to its part of the application. */
export function ProblemList({ problems }: { readonly problems: readonly Problem[] }) {
  const { t } = useI18n();
  if (problems.length === 0) return null;
  return (
    <ul className={styles.problemList}>
      {problems.map((problem, index) => (
        <li key={index} className={styles.problem}>
          <strong>{t(problemKindKey(problem.kind))}</strong>
          {problem.term ? `: « ${problem.term} »` : null}
          {problem.message ? ` — ${problem.message}` : null}
        </li>
      ))}
    </ul>
  );
}
