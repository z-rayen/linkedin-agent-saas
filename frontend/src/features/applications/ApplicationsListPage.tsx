import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { ApiErrorMessage } from "../../components/ApiErrorMessage";
import { EmptyState } from "../../components/EmptyState";
import { Loading } from "../../components/Loading";
import { StatusChip } from "../../components/StatusChip";
import { useI18n } from "../../i18n/I18nProvider";
import { applicationKeys } from "./keys";

const NO_VALUE = "—";

export function ApplicationsListPage() {
  const { t } = useI18n();
  const query = useQuery({
    queryKey: applicationKeys.all,
    queryFn: ({ signal }) => endpoints.listApplications(signal),
  });

  return (
    <section>
      <div className="page-header">
        <h1>{t("applications.title")}</h1>
        <Link className="button button-primary" to="/applications/new">
          {t("applications.newButton")}
        </Link>
      </div>

      {query.isPending ? <Loading /> : null}
      {query.isError ? <ApiErrorMessage error={query.error} /> : null}

      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState
          action={
            <Link className="button button-primary" to="/applications/new">
              {t("applications.newButton")}
            </Link>
          }
        >
          {t("applications.empty")}
        </EmptyState>
      ) : null}

      {query.isSuccess && query.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t("applications.columnJob")}</th>
                <th scope="col">{t("applications.columnCompany")}</th>
                <th scope="col">{t("applications.columnStatus")}</th>
                <th scope="col">{t("applications.columnRecipient")}</th>
                <th scope="col">
                  <span className="visually-hidden">{t("applications.open")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((application) => (
                <tr key={application.id}>
                  <td>{application.job_title}</td>
                  <td>{application.company ?? NO_VALUE}</td>
                  <td>
                    <StatusChip status={application.status} />
                  </td>
                  <td>{application.recipient_email}</td>
                  <td>
                    <Link
                      to={`/applications/${String(application.id)}`}
                      aria-label={`${t("applications.open")} : ${application.job_title}`}
                    >
                      {t("applications.open")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
