import { ApiError } from "../api/errors";
import { useI18n, type MessageKey } from "../i18n/I18nProvider";
import { Banner } from "./Banner";

const KNOWN_CODES = new Set([
  "authentication_failed",
  "cv_not_grounded",
  "content_changed",
  "send_policy_violation",
  "invalid_state",
  "already_running",
  "not_found",
  "validation_failed",
  "http_error",
  "password_too_short",
  "password_too_long",
  "rate_limited",
]);

/** The message for an error, in the user's language. The server's own text is not shown for known codes: they are
 * translated here. Unknown errors get a generic message, never a stack trace or a raw body. */
export function describeError(error: unknown, t: (key: MessageKey) => string): string {
  if (error instanceof ApiError) {
    if (error.isNetwork) return t("common.networkError");
    if (KNOWN_CODES.has(error.code)) return t(`errors.${error.code}` as MessageKey);
  }
  return t("common.unknownError");
}

export function ApiErrorMessage({
  error,
  title,
}: {
  readonly error: unknown;
  readonly title?: string;
}) {
  const { t } = useI18n();
  if (error === null || error === undefined) return null;
  return (
    <Banner tone="danger" title={title}>
      {describeError(error, t)}
    </Banner>
  );
}
