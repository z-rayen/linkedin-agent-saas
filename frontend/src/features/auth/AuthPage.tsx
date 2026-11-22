import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import type { PasswordPolicy } from "../../api/types";
import { useAuth, type SessionEndReason } from "../../auth/AuthProvider";
import { describeError } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { LocaleSwitch } from "../../components/LocaleSwitch";
import { useI18n, type MessageKey, type Translate } from "../../i18n/I18nProvider";
import { applicationKeys } from "../applications/keys";
import styles from "./AuthPage.module.css";

const DEFAULT_LANDING = "/applications";

/** Joins CSS class names. A module class that is missing is dropped rather than printed as "undefined". */
function classes(...names: Array<string | undefined>): string {
  return names.filter((name): name is string => name !== undefined).join(" ");
}
const UNAUTHORIZED = 401;
const CONFLICT = 409;
const SESSION_REASONS: readonly SessionEndReason[] = ["expired", "invalid", "logout"];

export interface AuthRouteState {
  readonly from?: string;
  readonly endReason?: SessionEndReason;
  readonly registered?: boolean;
}

/** Only in-application paths: never "//host" or an absolute URL, so login cannot send the user outside the app. */
export function isSafeLocalPath(path: string): boolean {
  return path.startsWith("/") && !path.startsWith("//");
}

/** The router state is not trusted: each field is checked before use. */
export function readAuthState(value: unknown): AuthRouteState {
  if (typeof value !== "object" || value === null) return {};
  const record = value as Record<string, unknown>;
  const state: { from?: string; endReason?: SessionEndReason; registered?: boolean } = {};
  if (typeof record.from === "string" && isSafeLocalPath(record.from)) state.from = record.from;
  const reason = SESSION_REASONS.find((candidate) => candidate === record.endReason);
  if (reason !== undefined) state.endReason = reason;
  if (record.registered === true) state.registered = true;
  return state;
}

/** The message for a failed submission. Password errors use the policy the server published, when it is known. */
export function formError(
  error: unknown,
  isLogin: boolean,
  t: Translate,
  policy: PasswordPolicy | undefined,
): string {
  if (error instanceof ApiError) {
    if (error.status === UNAUTHORIZED && isLogin) return t("auth.invalidCredentials");
    if (error.status === CONFLICT) return t("auth.emailExists");
    if (error.code === "password_too_short" && policy !== undefined) {
      return t("auth.passwordTooShort", { min: policy.min_length });
    }
    if (error.code === "password_too_long" && policy !== undefined) {
      return t("auth.passwordTooLong", { max: policy.max_bytes });
    }
  }
  return describeError(error, (key: MessageKey) => t(key));
}

/** Login and registration. The token goes straight into the auth provider: it is never put in a URL or a log. */
export function AuthPage({ mode }: { readonly mode: "login" | "register" }) {
  const { t } = useI18n();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const routeState = readAuthState(location.state);
  const emailId = useId();
  const passwordId = useId();
  const helpId = useId();
  const titleId = useId();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showingPassword, setShowingPassword] = useState(false);
  const isLogin = mode === "login";

  const policy = useQuery({
    queryKey: applicationKeys.passwordPolicy,
    queryFn: ({ signal }) => endpoints.passwordPolicy(signal),
    enabled: !isLogin,
    staleTime: Infinity,
  });

  const login = useMutation({
    mutationFn: () => endpoints.login(email, password),
    onSuccess: (response) => {
      signIn(response.access_token);
      void navigate(routeState.from ?? DEFAULT_LANDING, { replace: true });
    },
  });

  const register = useMutation({
    mutationFn: () => endpoints.register(email, password),
    onSuccess: () => {
      void navigate("/login", { replace: true, state: { registered: true } });
    },
  });

  const active = isLogin ? login : register;
  const errorText = active.error ? formError(active.error, isLogin, t, policy.data) : null;

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLogin) login.mutate();
    else register.mutate();
  }

  const sessionMessage =
    routeState.endReason === "expired"
      ? t("auth.sessionExpired")
      : routeState.endReason === "invalid"
        ? t("auth.sessionInvalid")
        : null;

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <LocaleSwitch />
      </div>
      <section className={styles.card} aria-labelledby={titleId}>
        <p className={styles.product}>{t("app.name")}</p>
        <h1 id={titleId} className={styles.title}>
          {isLogin ? t("auth.loginTitle") : t("auth.registerTitle")}
        </h1>

        {sessionMessage ? <Banner tone="warning">{sessionMessage}</Banner> : null}
        {routeState.registered ? <Banner tone="success">{t("auth.registered")}</Banner> : null}

        <form onSubmit={onSubmit} className={styles.form}>
          <div className="field">
            <label htmlFor={emailId}>{t("auth.email")}</label>
            <input
              id={emailId}
              className="input"
              type="email"
              name="email"
              autoComplete="username"
              spellCheck={false}
              required
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </div>

          <div className="field">
            <label htmlFor={passwordId}>{t("auth.password")}</label>
            <div className={styles.passwordRow}>
              <input
                id={passwordId}
                className={classes("input", styles.passwordInput)}
                type={showingPassword ? "text" : "password"}
                name="password"
                autoComplete={isLogin ? "current-password" : "new-password"}
                required
                aria-describedby={isLogin ? undefined : helpId}
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
              />
              <button
                type="button"
                className={classes("button", styles.toggle)}
                aria-pressed={showingPassword}
                aria-label={showingPassword ? t("auth.hidePassword") : t("auth.showPassword")}
                onClick={() => {
                  setShowingPassword((current) => !current);
                }}
              >
                {showingPassword ? t("auth.hidePassword") : t("auth.showPassword")}
              </button>
            </div>
            {isLogin ? null : (
              <p id={helpId} className="hint">
                {policy.data
                  ? `${t("auth.passwordRule", { min: policy.data.min_length })} ${t("auth.passwordHelp")}`
                  : null}
              </p>
            )}
          </div>

          {errorText ? <Banner tone="danger">{errorText}</Banner> : null}

          <button
            type="submit"
            className={classes("button", "button-primary", styles.submit)}
            disabled={active.isPending}
          >
            {isLogin ? t("auth.submitLogin") : t("auth.submitRegister")}
          </button>
        </form>

        <p className={styles.switch}>
          <Link to={isLogin ? "/register" : "/login"}>
            {isLogin ? t("auth.noAccount") : t("auth.haveAccount")}
          </Link>
        </p>
      </section>
    </div>
  );
}
