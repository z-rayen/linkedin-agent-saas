import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../auth/AuthProvider";
import { I18nProvider } from "../i18n/I18nProvider";

/** Renders a page inside the providers the application uses: language, session, query cache and router. */
export function renderPage(
  ui: ReactElement,
  options: { readonly route?: string; readonly path?: string } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <I18nProvider>
      <AuthProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[options.route ?? "/"]}>
            <Routes>
              <Route path={options.path ?? "*"} element={ui} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthProvider>
    </I18nProvider>,
  );
}
