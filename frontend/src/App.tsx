import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import { ApiError } from "./api/errors";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { EmptyState } from "./components/EmptyState";
import { Layout } from "./components/Layout";
import { AuthPage } from "./features/auth/AuthPage";
import { ApplicationsListPage } from "./features/applications/ApplicationsListPage";
import { NewApplicationPage } from "./features/applications/NewApplicationPage";
import { PreparationPage } from "./features/applications/PreparationPage";
import { ReviewPage } from "./features/applications/ReviewPage";
import { MasterCvPage } from "./features/master-cv/MasterCvPage";
import { I18nProvider, useI18n } from "./i18n/I18nProvider";

const MAX_QUERY_RETRIES = 1;
const CLIENT_ERROR_MIN_STATUS = 400;
const SERVER_ERROR_MIN_STATUS = 500;

/** Client errors (401, 404, 409, 422) are answers, not outages: they are not retried. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (
    error instanceof ApiError &&
    error.status >= CLIENT_ERROR_MIN_STATUS &&
    error.status < SERVER_ERROR_MIN_STATUS
  ) {
    return false;
  }
  return failureCount < MAX_QUERY_RETRIES;
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } },
});

function NotFoundPage() {
  const { t } = useI18n();
  return (
    <EmptyState action={<Link to="/applications">{t("nav.applications")}</Link>}>
      {t("common.notFound")}
    </EmptyState>
  );
}

function Routed() {
  const { t, locale } = useI18n();

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = t("app.name");
  }, [locale, t]);

  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/applications" replace />} />
          <Route path="master-cv" element={<MasterCvPage />} />
          <Route path="applications" element={<ApplicationsListPage />} />
          <Route path="applications/new" element={<NewApplicationPage />} />
          <Route path="applications/run/:runId" element={<PreparationPage />} />
          <Route path="applications/:applicationId" element={<ReviewPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export function App() {
  return (
    <I18nProvider>
      <ErrorBoundary>
        <AuthProvider>
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>
              <Routed />
            </BrowserRouter>
          </QueryClientProvider>
        </AuthProvider>
      </ErrorBoundary>
    </I18nProvider>
  );
}
