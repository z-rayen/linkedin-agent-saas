import { useI18n } from "../i18n/I18nProvider";
import type { ApplicationStatus } from "../api/types";

const TONE: Record<ApplicationStatus, string> = {
  prepared: "info",
  approved: "success",
  sending: "info",
  needs_verification: "warning",
  sent: "success",
  failed: "danger",
  replied: "success",
};

/** The status as a label and a colour. The text carries the meaning; the colour only reinforces it. */
export function StatusChip({ status }: { readonly status: ApplicationStatus }) {
  const { t } = useI18n();
  return <span className={`chip chip-${TONE[status]}`}>{t(`applications.status.${status}`)}</span>;
}
