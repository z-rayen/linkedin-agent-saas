import { useMutation } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import type { ApplicationCreateBody } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { useI18n } from "../../i18n/I18nProvider";

interface FormState {
  readonly jobTitle: string;
  readonly jobText: string;
  readonly jobUrl: string;
  readonly recipient: string;
  readonly company: string;
}

interface FieldErrors {
  readonly jobTitle: boolean;
  readonly recipient: boolean;
  readonly source: boolean;
}

const EMPTY_FORM: FormState = { jobTitle: "", jobText: "", jobUrl: "", recipient: "", company: "" };
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateNewApplication(form: FormState): FieldErrors {
  return {
    jobTitle: form.jobTitle.trim() === "",
    recipient: !EMAIL_PATTERN.test(form.recipient.trim()),
    source: form.jobText.trim() === "" && form.jobUrl.trim() === "",
  };
}

function hasErrors(errors: FieldErrors): boolean {
  return errors.jobTitle || errors.recipient || errors.source;
}

/** Only the fields that have a value are sent: optional fields are left out rather than sent empty. */
function toBody(form: FormState): ApplicationCreateBody {
  const body: {
    job_title: string;
    recipient_email: string;
    job_text?: string;
    job_url?: string;
    company?: string;
  } = { job_title: form.jobTitle.trim(), recipient_email: form.recipient.trim() };
  if (form.jobText.trim() !== "") body.job_text = form.jobText.trim();
  if (form.jobUrl.trim() !== "") body.job_url = form.jobUrl.trim();
  if (form.company.trim() !== "") body.company = form.company.trim();
  return body;
}

export function NewApplicationPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const ids = {
    jobTitle: useId(),
    jobText: useId(),
    jobUrl: useId(),
    recipient: useId(),
    company: useId(),
    source: useId(),
  };
  const [params] = useSearchParams();
  // Prefilled from the watch ("Prepare application"): the title and the link only. The recipient is never prefilled.
  const [prefilled] = useState(() => params.has("job_title") || params.has("job_url"));
  const [form, setForm] = useState<FormState>(() => ({
    ...EMPTY_FORM,
    jobTitle: params.get("job_title") ?? "",
    jobUrl: params.get("job_url") ?? "",
  }));
  const [errors, setErrors] = useState<FieldErrors>({
    jobTitle: false,
    recipient: false,
    source: false,
  });

  const create = useMutation({
    mutationFn: (body: ApplicationCreateBody) => endpoints.createApplication(body),
    onSuccess: (run) => {
      void navigate(`/applications/run/${encodeURIComponent(run.run_id)}`);
    },
  });

  function update(key: keyof FormState, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateNewApplication(form);
    setErrors(found);
    if (hasErrors(found)) return;
    create.mutate(toBody(form));
  }

  return (
    <section>
      <h1>{t("newApplication.title")}</h1>
      <p className="muted">{t("newApplication.intro")}</p>
      {prefilled ? <Banner tone="info">{t("watch.prefilled")}</Banner> : null}

      <form onSubmit={onSubmit} noValidate>
        <div className="field">
          <label htmlFor={ids.jobTitle}>{t("newApplication.jobTitle")}</label>
          <input
            id={ids.jobTitle}
            className="input"
            value={form.jobTitle}
            aria-invalid={errors.jobTitle}
            aria-describedby={`${ids.jobTitle}-error`}
            onChange={(event) => {
              update("jobTitle", event.target.value);
            }}
          />
          <p id={`${ids.jobTitle}-error`} className="field-error">
            {errors.jobTitle ? t("newApplication.required") : null}
          </p>
        </div>

        <div className="field">
          <label htmlFor={ids.recipient}>{t("newApplication.recipient")}</label>
          <input
            id={ids.recipient}
            className="input"
            type="email"
            autoComplete="off"
            value={form.recipient}
            aria-invalid={errors.recipient}
            aria-describedby={`${ids.recipient}-error`}
            onChange={(event) => {
              update("recipient", event.target.value);
            }}
          />
          <p id={`${ids.recipient}-error`} className="field-error">
            {errors.recipient ? t("newApplication.required") : null}
          </p>
        </div>

        <div className="field">
          <label htmlFor={ids.company}>{t("newApplication.company")}</label>
          <input
            id={ids.company}
            className="input"
            value={form.company}
            onChange={(event) => {
              update("company", event.target.value);
            }}
          />
        </div>

        <div className="field">
          <label htmlFor={ids.jobUrl}>{t("newApplication.jobUrl")}</label>
          <input
            id={ids.jobUrl}
            className="input"
            type="url"
            value={form.jobUrl}
            aria-describedby={ids.source}
            onChange={(event) => {
              update("jobUrl", event.target.value);
            }}
          />
        </div>

        <div className="field">
          <label htmlFor={ids.jobText}>{t("newApplication.jobText")}</label>
          <p className="hint">{t("newApplication.hintText")}</p>
          <textarea
            id={ids.jobText}
            className="textarea"
            rows={10}
            value={form.jobText}
            aria-describedby={ids.source}
            onChange={(event) => {
              update("jobText", event.target.value);
            }}
          />
          <p id={ids.source} className="field-error">
            {errors.source ? t("newApplication.needTextOrUrl") : null}
          </p>
        </div>

        <ApiErrorMessage error={create.error} />

        <button type="submit" className="button button-primary" disabled={create.isPending}>
          {t("newApplication.start")}
        </button>
      </form>
    </section>
  );
}
