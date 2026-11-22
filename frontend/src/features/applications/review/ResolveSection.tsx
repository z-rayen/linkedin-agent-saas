import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { endpoints } from "../../../api/endpoints";
import type { ApplicationDetail } from "../../../api/types";
import { ApiErrorMessage } from "../../../components/ApiErrorMessage";
import { Banner } from "../../../components/Banner";
import { useI18n } from "../../../i18n/I18nProvider";
import { applicationKeys } from "../keys";

type Outcome = "sent" | "failed";

/** An interrupted send is never retried automatically: a person checks the outbox and records what happened. */
export function ResolveSection({ detail }: { readonly detail: ApplicationDetail }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const headingId = useId();
  const groupName = useId();
  const noteId = useId();
  const [outcome, setOutcome] = useState<Outcome>("failed");
  const [note, setNote] = useState("");

  const resolve = useMutation({
    mutationFn: () => endpoints.resolveApplication(detail.id, outcome, note.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    },
  });

  if (detail.status !== "needs_verification") return null;

  return (
    <section className="card" aria-labelledby={headingId}>
      <h2 id={headingId}>{t("send.resolveTitle")}</h2>
      <p>{t("send.resolveIntro")}</p>
      {detail.last_error ? (
        <Banner tone="warning">{t("send.lastError", { error: detail.last_error })}</Banner>
      ) : null}

      <fieldset className="field">
        <legend className="visually-hidden">{t("send.resolveTitle")}</legend>
        <label>
          <input
            type="radio"
            name={groupName}
            checked={outcome === "sent"}
            onChange={() => {
              setOutcome("sent");
            }}
          />{" "}
          {t("send.outcomeSent")}
        </label>
        <label>
          <input
            type="radio"
            name={groupName}
            checked={outcome === "failed"}
            onChange={() => {
              setOutcome("failed");
            }}
          />{" "}
          {t("send.outcomeFailed")}
        </label>
      </fieldset>

      <div className="field">
        <label htmlFor={noteId}>{t("send.note")}</label>
        <textarea
          id={noteId}
          className="textarea"
          rows={3}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
      </div>

      <div className="actions">
        <button
          type="button"
          className="button button-primary"
          disabled={note.trim() === "" || resolve.isPending}
          onClick={() => {
            resolve.mutate();
          }}
        >
          {t("send.resolve")}
        </button>
      </div>
      <ApiErrorMessage error={resolve.error} />
    </section>
  );
}
