import { useI18n } from "../i18n/I18nProvider";

export function Loading({ label }: { readonly label?: string }) {
  const { t } = useI18n();
  return (
    <p role="status" aria-live="polite" className="muted">
      {label ?? t("common.loading")}
    </p>
  );
}
