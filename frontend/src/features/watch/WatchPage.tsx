import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import type { WatchResult } from "../../api/types";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { Banner } from "../../components/Banner";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { safeWebUrl } from "../../lib/safeUrl";
import { watchKeys } from "./keys";

/** The watch: offers and certifications found for the profile. Preparing an application only opens the form, prefilled.
 * The preparation itself is a separate, reviewed step. */
export function WatchPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: watchKeys.results,
    queryFn: ({ signal }) => endpoints.watchResults(signal),
  });

  const run = useMutation({
    mutationFn: () => endpoints.runWatch(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: watchKeys.results });
    },
  });

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>{t("watch.title")}</h1>
          <p className="muted">{t("watch.intro")}</p>
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={run.isPending}
          onClick={() => {
            run.mutate();
          }}
        >
          {run.isPending ? t("watch.running") : t("watch.run")}
        </button>
      </div>

      {run.isSuccess ? (
        <Banner tone="success">{t("watch.ran", { count: run.data.new_items })}</Banner>
      ) : null}
      <ApiErrorMessage error={run.error} />

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}
      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState>{t("watch.empty")}</EmptyState>
      ) : null}

      {query.isSuccess ? (
        <ul className="stack">
          {query.data.map((result) => (
            <WatchItem key={result.id} result={result} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function typeLabel(type: WatchResult["type"], t: Translate): string {
  if (type === "job") return t("watch.typeJob");
  if (type === "cert") return t("watch.typeCert");
  return t("watch.typeOther");
}

/** The query string that opens the application form with this offer filled in. Only the title and the link: the
 * recipient is never guessed, and the offer text is read by the preparation from the link. */
export function prepareQuery(result: WatchResult): string {
  return new URLSearchParams({ job_title: result.title, job_url: result.link }).toString();
}

function WatchItem({ result }: { readonly result: WatchResult }) {
  const { t } = useI18n();
  const href = safeWebUrl(result.link);
  return (
    <li className="card">
      <div className="page-header">
        <div>
          <p>
            <span className="chip chip-info">{typeLabel(result.type, t)}</span>{" "}
            <strong>{result.title}</strong>
          </p>
          <p className="muted">{t("watch.score", { score: result.match_score })}</p>
        </div>
      </div>
      {result.justification ? (
        <details>
          <summary>{t("watch.justification")}</summary>
          <p>{result.justification}</p>
        </details>
      ) : null}
      <div className="actions">
        {href !== null ? (
          <a className="button" href={href} target="_blank" rel="noopener noreferrer">
            {t("watch.open")}
          </a>
        ) : null}
        {result.type === "job" ? (
          <>
            <Link
              className="button button-primary"
              to={`/applications/new?${prepareQuery(result)}`}
            >
              {t("watch.prepare")}
            </Link>
            <p className="hint">{t("watch.prepareHint")}</p>
          </>
        ) : null}
      </div>
    </li>
  );
}
