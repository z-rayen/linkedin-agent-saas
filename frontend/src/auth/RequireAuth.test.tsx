import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AuthPage } from "../features/auth/AuthPage";
import { I18nProvider } from "../i18n/I18nProvider";
import { fr } from "../i18n/fr";
import { AuthProvider } from "./AuthProvider";
import { RequireAuth } from "./RequireAuth";
import { TOKEN_STORAGE_KEY } from "./tokenStorage";

const PAST_SECONDS = 1_000;
const PROTECTED_TEXT = "PROTECTED CONTENT";

function base64Url(value: unknown): string {
  return btoa(JSON.stringify(value)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

/** A token whose expiry is in the past. Only the payload is read by the app, and only to tell "expired" apart. */
function expiredToken(): string {
  return `${base64Url({ alg: "HS256", typ: "JWT" })}.${base64Url({ sub: "1", exp: PAST_SECONDS })}.signature`;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <AuthProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/login" element={<AuthPage mode="login" />} />
              <Route element={<RequireAuth />}>
                <Route path="/applications" element={<p>{PROTECTED_TEXT}</p>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthProvider>
    </I18nProvider>,
  );
}

describe("route protection", () => {
  it("sends a visitor without a session to the login page, and shows none of the protected page", () => {
    renderAt("/applications");
    expect(screen.getByRole("heading", { name: fr.auth.loginTitle })).toBeInTheDocument();
    expect(screen.queryByText(PROTECTED_TEXT)).not.toBeInTheDocument();
  });

  it("says why the session ended when the stored token has expired", () => {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, expiredToken());
    renderAt("/applications");
    expect(screen.getByText(fr.auth.sessionExpired)).toBeInTheDocument();
    expect(screen.queryByText(PROTECTED_TEXT)).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  });
});
