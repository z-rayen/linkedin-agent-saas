import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import type { Connection } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { ConnectionsPage } from "./ConnectionsPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: { connections: vi.fn(), authorizeUrl: vi.fn() },
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

  it("states that access cannot be revoked from the application", async () => {
    renderPage(<ConnectionsPage />);
    expect(await screen.findByText(fr.connections.noDisconnect)).toBeInTheDocument();
  });
});
