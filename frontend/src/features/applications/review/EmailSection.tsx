import { useId, useState } from "react";
import type { Problem } from "../../../api/errors";
import type { ApplicationDetail } from "../../../api/types";
import { ApiErrorMessage } from "../../../components/ApiErrorMessage";
import { Banner } from "../../../components/Banner";
import { useI18n } from "../../../i18n/I18nProvider";
import { ProblemList } from "./ProblemList";
import { canEdit } from "./rules";

export interface EmailDraft {
  readonly subject: string;
  readonly body: string;
  readonly recipient: string;
}

interface Props {
  readonly detail: ApplicationDetail;
  readonly problems: readonly Problem[];
  readonly saving: boolean;
  readonly saved: boolean;
  readonly saveError: unknown;
  readonly onSave: (draft: EmailDraft) => void;
}

/** The email, editable while it can be. Any saved change sends the application back to "prepared". */
export function EmailSection({ detail, problems, saving, saved, saveError, onSave }: Props) {
  const { t } = useI18n();
  const recipientId = useId();
  const subjectId = useId();
  const bodyId = useId();
  const [recipient, setRecipient] = useState(detail.recipient_email);
  const [subject, setSubject] = useState(detail.subject);
  const [body, setBody] = useState(detail.body);

  const editable = canEdit(detail.status);
  const changed =
    recipient !== detail.recipient_email || subject !== detail.subject || body !== detail.body;

  return (
    <section className="card" aria-labelledby={`${recipientId}-section`}>
      <h2 id={`${recipientId}-section`}>{t("review.emailSection")}</h2>

      <div className="field">
        <label htmlFor={recipientId}>{t("review.recipient")}</label>
        <input
          id={recipientId}
          className="input"
          type="email"
          value={recipient}
          disabled={!editable}
          onChange={(event) => {
            setRecipient(event.target.value);
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={subjectId}>{t("review.subject")}</label>
        <input
          id={subjectId}
          className="input"
          value={subject}
          disabled={!editable}
          onChange={(event) => {
            setSubject(event.target.value);
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={bodyId}>{t("review.body")}</label>
        <textarea
          id={bodyId}
          className="textarea"
          rows={12}
          value={body}
          disabled={!editable}
          onChange={(event) => {
            setBody(event.target.value);
          }}
        />
      </div>

      <ProblemList problems={problems} />

      {saved ? <Banner tone="success">{t("review.saved")}</Banner> : null}
      <ApiErrorMessage error={saveError} />

      {editable ? (
        <div className="actions">
          <button
            type="button"
            className="button button-primary"
            disabled={!changed || saving}
            onClick={() => {
              onSave({ subject, body, recipient });
            }}
          >
            {t("review.save")}
          </button>
        </div>
      ) : (
        <p className="muted">{t("review.readOnly")}</p>
      )}
    </section>
  );
}
