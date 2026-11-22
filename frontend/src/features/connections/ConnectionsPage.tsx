import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import type { Connection, ConnectionStatus, Provider } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { Loading } from "../../components/Loading";
import { useI18n, type MessageKey, type Translate } from "../../i18n/I18nProvider";
import { connectionKeys } from "./keys";

const PROVIDERS: readonly Provider[] = ["linkedin", "github"];
const CONNECTED_STATUS = "connected";

const STATUS_CHIP: Record<ConnectionStatus, string> = {
  connected: "chip chip-success",
  expired: "chip chip-warning",
  not_connected: "chip chip-info",
};

const STATUS_KEY: Record<ConnectionStatus, MessageKey> = {
  connected: "connections.connected",
  expired: "connections.expired",
  not_connected: "connections.notConnected",
};

function dateOf(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

/** Connected, expired or not connected, for LinkedIn and GitHub. Connecting sends the browser to the provider's own
 * consent page, through the authorize-url the server builds for this user. */
export function ConnectionsPage() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const query = useQuery({
    queryKey: connectionKeys.connections,
    queryFn: ({ signal }) => endpoints.connections(signal),
  });

  const returned = params.get("provider");
  const returnedOk = params.get("status") === CONNECTED_STATUS && returned !== null;

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("connections.title")}</h1>
          <p className="muted">{t("connections.intro")}</p>
        </div>
      </div>

      {returnedOk && isProvider(returned) ? (
        <Banner tone="success">
          {t("connections.success", { provider: providerName(returned, t) })}
        </Banner>
      ) : null}

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}

      {query.isSuccess
        ? PROVIDERS.map((provider) => {
            const connection = query.data.find((item) => item.provider === provider);
            return (
              <ConnectionCard key={provider} provider={provider} connection={connection ?? null} />
            );
          })
        : null}

      <p className="hint">{t("connections.noDisconnect")}</p>
    </section>
  );
}

function isProvider(value: string | null): value is Provider {
  return value === "linkedin" || value === "github";
}

function providerName(provider: Provider, t: Translate): string {
  return provider === "linkedin" ? t("connections.linkedin") : t("connections.github");
}

function ConnectionCard({
  provider,
  connection,
}: {
  readonly provider: Provider;
  readonly connection: Connection | null;
}) {
  const { t } = useI18n();
  const status: ConnectionStatus = connection?.status ?? "not_connected";
  const needed =
    provider === "linkedin" ? t("connections.neededForLinkedin") : t("connections.neededForGithub");

  const authorize = useMutation({
    mutationFn: () => endpoints.authorizeUrl(provider),
    onSuccess: (result) => {
      window.location.assign(result.url);
    },
  });

  const action = status === "not_connected" ? t("connections.connect") : t("connections.reconnect");

  return (
    <article className="card" aria-labelledby={`connection-${provider}`}>
      <div className="page-header">
        <div>
          <h2 id={`connection-${provider}`}>{providerName(provider, t)}</h2>
          <p className="muted">{needed}</p>
        </div>
        <span className={STATUS_CHIP[status]}>{t(STATUS_KEY[status])}</span>
      </div>

      {status === "expired" && connection?.expires_at ? (
        <p>{t("connections.expiredAt", { date: dateOf(connection.expires_at) })}</p>
      ) : null}
      {status === "connected" && connection?.expires_at ? (
        <p className="muted">
          {t("connections.validUntil", { date: dateOf(connection.expires_at) })}
        </p>
      ) : null}

      <ApiErrorMessage error={authorize.error} />
      {authorize.isPending ? <p className="muted">{t("connections.redirecting")}</p> : null}

      <div className="actions">
        <button
          type="button"
          className="button button-primary"
          disabled={authorize.isPending}
          onClick={() => {
            authorize.mutate();
          }}
        >
          {action}
        </button>
      </div>
    </article>
  );
}
