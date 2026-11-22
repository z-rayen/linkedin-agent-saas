import { useMutation } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import { useAuth } from "../../auth/AuthProvider";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { useI18n } from "../../i18n/I18nProvider";

const FORBIDDEN = 403;

/** The account. Nothing changes here without the user's action. Deletion is described before it is offered, needs the
 * password typed again and a tick, and a wrong password deletes nothing and keeps the session. */
export function AccountPage() {
  const { t } = useI18n();
  const { signOut } = useAuth();
  const passwordId = useId();
  const understandId = useId();
  const [password, setPassword] = useState("");
  const [understood, setUnderstood] = useState(false);

  const remove = useMutation({
    mutationFn: () => endpoints.deleteAccount(password),
    onSuccess: () => {
      signOut("deleted");
    },
  });

  const wrongPassword = remove.error instanceof ApiError && remove.error.status === FORBIDDEN;

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (understood && password !== "") remove.mutate();
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("account.title")}</h1>
          <p className="muted">{t("account.intro")}</p>
        </div>
      </div>

      <section className="card" aria-labelledby={`${passwordId}-delete`}>
        <h2 id={`${passwordId}-delete`}>{t("account.deleteTitle")}</h2>
        <p>{t("account.deleteWhat")}</p>
        <p>{t("account.deleteOthers")}</p>
        <Banner tone="danger">{t("account.deleteIrreversible")}</Banner>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor={passwordId}>{t("account.passwordLabel")}</label>
            <input
              id={passwordId}
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
          </div>

          <div className="field">
            <label htmlFor={understandId}>
              <input
                id={understandId}
                type="checkbox"
                checked={understood}
                onChange={(event) => {
                  setUnderstood(event.target.checked);
                }}
              />{" "}
              {t("account.understand")}
            </label>
          </div>

          {wrongPassword ? <Banner tone="danger">{t("account.wrongPassword")}</Banner> : null}
          {wrongPassword ? null : <ApiErrorMessage error={remove.error} />}

          <button
            type="submit"
            className="button"
            disabled={!understood || password === "" || remove.isPending}
          >
            {t("account.delete")}
          </button>
        </form>
      </section>
    </section>
  );
}
