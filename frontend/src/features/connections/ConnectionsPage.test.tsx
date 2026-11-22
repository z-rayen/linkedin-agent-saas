import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import type { Connection } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { ConnectionsPage } from "./ConnectionsPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: { connections: vi.fn(), authorizeUrl: vi.fn(), disconnect: vi.fn() },
}));

const api = vi.mocked(endpoints);

const CONNECTIONS: Connection[] = [
  { provider: "linkedin", status: "expired", expires_at: "2026-01-10T08:00:00" },
  { provider: "github", status: "not_connected", expires_at: null },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.connections.mockResolvedValue(CONNECTIONS);
});

describe("connections", () => {
  it("shows connected, expired or not connected for each provider, and what to do next", async () => {
    renderPage(<ConnectionsPage />);

    expect(await screen.findByText(fr.connections.expired)).toBeInTheDocument();
    expect(screen.getByText(fr.connections.notConnected)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: fr.connections.reconnect })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: fr.connections.connect })).toBeInTheDocument();
  });

  it("confirms a successful return from the provider", async () => {
    renderPage(<ConnectionsPage />, { route: "/connections?provider=github&status=connected" });
    expect(
      await screen.findByText(fr.connections.success.replace("{provider}", "GitHub")),
    ).toBeInTheDocument();
  });

  it("disconnects only after a confirmation, and says whether the provider also revoked the access", async () => {
    api.connections.mockResolvedValue([
      { provider: "github", status: "connected", expires_at: null },
    ]);
    api.disconnect.mockResolvedValue({
      provider: "github",
      status: "disconnected",
      provider_revoked: "revoked",
    });
    const user = userEvent.setup();
    renderPage(<ConnectionsPage />);

    await user.click(await screen.findByRole("button", { name: fr.connections.disconnect }));
    expect(
      screen.getByText(fr.connections.disconnectConfirm.replace(/\{provider\}/g, "GitHub")),
    ).toBeInTheDocument();
    expect(api.disconnect).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: fr.connections.confirmDisconnect }));
    await waitFor(() => {
      expect(api.disconnect).toHaveBeenCalledWith("github");
    });
    expect(await screen.findByText(/GitHub est déconnecté/)).toBeInTheDocument();
    expect(screen.getByText(/aussi été révoqué chez GitHub/)).toBeInTheDocument();
  });

  it("tells the user to revoke by hand when the provider has no revocation endpoint", async () => {
    api.connections.mockResolvedValue([
      { provider: "linkedin", status: "connected", expires_at: null },
    ]);
    api.disconnect.mockResolvedValue({
      provider: "linkedin",
      status: "disconnected",
      provider_revoked: "not_supported",
    });
    const user = userEvent.setup();
    renderPage(<ConnectionsPage />);

    await user.click(await screen.findByRole("button", { name: fr.connections.disconnect }));
    await user.click(screen.getByRole("button", { name: fr.connections.confirmDisconnect }));
    expect(
      await screen.findByText(/ne permet pas de révoquer l'accès depuis l'application/),
    ).toBeInTheDocument();
  });
});
