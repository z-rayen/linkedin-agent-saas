import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import { AuthProvider } from "../../auth/AuthProvider";
import { I18nProvider } from "../../i18n/I18nProvider";
import { fr } from "../../i18n/fr";
import { AuthPage } from "./AuthPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: {
    login: vi.fn(),
    register: vi.fn(),
    passwordPolicy: vi.fn(),
  },
}));

const api = vi.mocked(endpoints);
const EMAIL = "jane@example.com";
const PASSWORD = "a-long-passphrase";

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <AuthProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/login" element={<AuthPage mode="login" />} />
              <Route path="/register" element={<AuthPage mode="register" />} />
              <Route path="/applications" element={<p>HOME</p>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.passwordPolicy.mockResolvedValue({ min_length: 12, max_bytes: 72 });
});

describe("login and registration screens", () => {
  it("shows the product name, a visible language switch, and the right autocomplete attributes", () => {
    renderAt("/login");

    expect(screen.getByText(fr.app.name)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: fr.app.languageLabel })).toBeInTheDocument();
    expect(screen.getByLabelText(fr.auth.email)).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(fr.auth.password)).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
  });

  it("uses new-password on registration, and shows the rule from the server's policy", async () => {
    renderAt("/register");

    expect(screen.getByLabelText(fr.auth.password)).toHaveAttribute("autocomplete", "new-password");
    expect(await screen.findByText(/Au moins 12 caractères/)).toBeInTheDocument();
  });

  it("shows and hides the password, and says which state it is in", async () => {
    const user = userEvent.setup();
    renderAt("/login");
    const password = screen.getByLabelText(fr.auth.password);
    const toggle = screen.getByRole("button", { name: fr.auth.showPassword });

    expect(password).toHaveAttribute("type", "password");
    await user.click(toggle);
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: fr.auth.hidePassword })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("submits the login form when Enter is pressed in the password field", async () => {
    api.login.mockResolvedValue({ access_token: "token-value", token_type: "bearer" });
    const user = userEvent.setup();
    renderAt("/login");

    await user.type(screen.getByLabelText(fr.auth.email), EMAIL);
    await user.type(screen.getByLabelText(fr.auth.password), `${PASSWORD}{Enter}`);

    await waitFor(() => {
      expect(api.login).toHaveBeenCalledWith(EMAIL, PASSWORD);
    });
    expect(await screen.findByText("HOME")).toBeInTheDocument();
  });

  it("says the credentials are wrong on a 401, without saying which part is wrong", async () => {
    api.login.mockRejectedValue(
      new ApiError({ status: 401, code: "authentication_failed", message: "" }),
    );
    const user = userEvent.setup();
    renderAt("/login");

    await user.type(screen.getByLabelText(fr.auth.email), EMAIL);
    await user.type(screen.getByLabelText(fr.auth.password), `${PASSWORD}{Enter}`);

    expect(await screen.findByText(fr.auth.invalidCredentials)).toBeInTheDocument();
  });

  it("tells the user the password is too short, with the minimum from the policy", async () => {
    api.register.mockRejectedValue(
      new ApiError({ status: 422, code: "password_too_short", message: "too short" }),
    );
    const user = userEvent.setup();
    renderAt("/register");

    await user.type(screen.getByLabelText(fr.auth.email), EMAIL);
    await user.type(screen.getByLabelText(fr.auth.password), `short{Enter}`);

    expect(
      await screen.findByText("Le mot de passe doit comporter au moins 12 caractères."),
    ).toBeInTheDocument();
  });

  it("says when too many attempts were made, without a retry loop", async () => {
    api.login.mockRejectedValue(new ApiError({ status: 429, code: "rate_limited", message: "" }));
    const user = userEvent.setup();
    renderAt("/login");

    await user.type(screen.getByLabelText(fr.auth.email), EMAIL);
    await user.type(screen.getByLabelText(fr.auth.password), `${PASSWORD}{Enter}`);

    expect(await screen.findByText(fr.errors.rate_limited)).toBeInTheDocument();
    expect(api.login).toHaveBeenCalledTimes(1);
  });
});
