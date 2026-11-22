import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ChangeEvent } from "react";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { Loading } from "../../components/Loading";
import { useI18n, type MessageKey, type Translate } from "../../i18n/I18nProvider";
import { applicationKeys } from "../applications/keys";
import { checkMasterCv, type MasterCvCheck } from "./masterCvRules";

const NOT_FOUND = 404;

type CheckFailure = Extract<MasterCvCheck, { readonly ok: false }>;

const REASON_KEYS: Readonly<Record<Exclude<CheckFailure["reason"], "invalidJson">, MessageKey>> = {
  empty: "masterCv.emptyText",
  notAnObject: "masterCv.notAnObject",
  draft: "masterCv.draftRefused",
  missingName: "masterCv.missingName",
  noFacts: "masterCv.noFacts",
};

function failureText(check: CheckFailure, t: Translate): string {
  if (check.reason === "invalidJson") return t("masterCv.invalidJson", { reason: check.detail });
  return t(REASON_KEYS[check.reason]);
}

/** Paste or import the master CV. The checks here are early feedback: the server still decides what it accepts. */
export function MasterCvPage() {
  const { t } = useI18n();
  const textId = useId();
  const fileId = useId();
  const [text, setText] = useState("");
  const [check, setCheck] = useState<MasterCvCheck | null>(null);
  const queryClient = useQueryClient();

  // No master CV yet is an answer (404), not a failure: it shows the empty state.
  const stored = useQuery({
    queryKey: applicationKeys.masterCv,
    queryFn: async ({ signal }) => {
      try {
        return await endpoints.getMasterCv(signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === NOT_FOUND) return null;
        throw error;
      }
    },
  });

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => endpoints.putMasterCv(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: applicationKeys.masterCv });
    },
  });

  function runCheck(): MasterCvCheck {
    const result = checkMasterCv(text);
    setCheck(result);
    return result;
  }

  function onSave() {
    const result = runCheck();
    if (!result.ok) return;
    save.mutate(result.payload);
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file === undefined) return;
    setText(await file.text());
    setCheck(null);
    save.reset();
  }

  const serverFieldErrors = save.error instanceof ApiError ? save.error.fieldErrors : [];

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("masterCv.title")}</h1>
          <p className="muted">{t("masterCv.intro")}</p>
        </div>
      </div>

      {stored.isPending ? <Loading /> : null}
      {stored.isError ? <ApiErrorMessage error={stored.error} /> : null}
      {stored.data === null ? <p className="muted">{t("masterCv.empty")}</p> : null}
      {stored.data ? (
        <Banner tone="info">
          {t("masterCv.stored", { name: stored.data.full_name, facts: stored.data.facts.length })}
        </Banner>
      ) : null}

      <div className="field">
        <label htmlFor={textId}>{t("masterCv.pasteLabel")}</label>
        <textarea
          id={textId}
          className="textarea mono"
          rows={16}
          spellCheck={false}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setCheck(null);
            save.reset();
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={fileId}>{t("masterCv.fileLabel")}</label>
        <input
          id={fileId}
          className="input"
          type="file"
          accept=".json,application/json"
          onChange={(event) => {
            void importFile(event);
          }}
        />
      </div>

      <div className="actions">
        <button
          type="button"
          className="button"
          onClick={() => {
            runCheck();
          }}
        >
          {t("masterCv.validate")}
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={save.isPending}
          onClick={onSave}
        >
          {t("masterCv.save")}
        </button>
      </div>

      {check !== null ? (
        check.ok ? (
          <Banner tone="success">
            {t("masterCv.factCount", { facts: check.facts, name: check.name })}
          </Banner>
        ) : (
          <Banner tone="danger">{failureText(check, t)}</Banner>
        )
      ) : null}

      {save.isSuccess ? (
        <Banner tone="success">{t("masterCv.saved", { facts: save.data.facts })}</Banner>
      ) : null}

      {serverFieldErrors.length > 0 ? (
        <Banner tone="danger">
          <p>{t("masterCv.serverErrors")}</p>
          <ul>
            {serverFieldErrors.map((item, index) => (
              <li key={`${item.path}-${String(index)}`}>
                {item.path ? <code>{item.path}</code> : null}
                {item.path ? ": " : ""}
                {item.message}
              </li>
            ))}
          </ul>
        </Banner>
      ) : (
        <ApiErrorMessage error={save.error} />
      )}
    </section>
  );
}
