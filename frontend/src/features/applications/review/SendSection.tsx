import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { endpoints } from "../../../api/endpoints";
import type { ApplicationDetail, MailMode } from "../../../api/types";
import { ApiErrorMessage } from "../../../components/ApiErrorMessage";
import { Banner } from "../../../components/Banner";
import { useI18n } from "../../../i18n/I18nProvider";
import { applicationKeys } from "../keys";
import { canSend, modeBanner } from "./rules";

const NO_VALUE = "—";

interface Props {
  readonly detail: ApplicationDetail;
  readonly mode: MailMode | null;
}

/** Sending. The mode is always shown first. The recipient must be typed exactly, and the server checks it again. */
export function SendSection({ detail, mode }: Props) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const headingId = useId();
  const confirmId = useId();
  const hintId = useId();
  const [typed, setTyped] = useState("");

  const banner = modeBanner(mode);
  const allowed = canSend({
    status: detail.status,
    mode,
    typed,
    recipient: detail.recipient_email,
  });

  const send = useMutation({
    mutationFn: () => endpoints.sendApplication(detail.id, typed.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });

  const result = send.data;

  return (
    <section className="card" aria-labelledby={headingId}>
      <h2 id={headingId}>{t("send.title")}</h2>
      <Banner tone={banner.tone}>{t(banner.key)}</Banner>

      {detail.status === "prepared" ? <p className="muted">{t("send.needsApproval")}</p> : null}

      {detail.status === "approved" ? (
        <>
          <div className="field">
            <label htmlFor={confirmId}>{t("send.confirmLabel")}</label>
            <input
              id={confirmId}
              className="input"
              autoComplete="off"
              spellCheck={false}
              value={typed}
              aria-describedby={hintId}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
            <p id={hintId} className="hint">
              {t("send.confirmHint", { address: detail.recipient_email || NO_VALUE })}
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="button button-primary"
              disabled={!allowed || send.isPending}
              onClick={() => {
                send.mutate();
              }}
            >
              {t("send.send")}
            </button>
          </div>
          {allowed ? null : <p className="muted">{t("send.sendBlocked")}</p>}
        </>
      ) : null}

      {result ? (
        result.dry_run ? (
          <Banner tone="info">{t("send.simulated", { address: detail.recipient_email })}</Banner>
        ) : (
          <Banner tone="success">{t("send.sent", { id: result.message_id ?? NO_VALUE })}</Banner>
        )
      ) : null}
      <ApiErrorMessage error={send.error} />
    </section>
  );
}
