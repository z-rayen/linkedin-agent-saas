import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import type { ContentItem } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { ContentPage } from "./ContentPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: {
    contentQueue: vi.fn(),
    generateContent: vi.fn(),
    editContent: vi.fn(),
    publishContent: vi.fn(),
  },
}));

const api = vi.mocked(endpoints);
const CREATED = "2026-11-20T10:00:00";

function post(overrides: Partial<ContentItem>): ContentItem {
  return {
    id: 3,
    title: "Agent RAG",
    post_text: "Hello LinkedIn",
    status: "draft",
    source: "manual",
    created_at: CREATED,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.publishContent.mockResolvedValue({ status: "published" });
});

describe("content queue", () => {
  it("asks once more before publishing, and publishes only after the confirmation", async () => {
    api.contentQueue.mockResolvedValue([post({})]);
    const user = userEvent.setup();
    renderPage(<ContentPage />);

    await user.click(await screen.findByRole("button", { name: fr.content.publish }));
    expect(screen.getByText(fr.content.publishTitle)).toBeInTheDocument();
    expect(api.publishContent).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: fr.content.publishConfirm }));
    await waitFor(() => {
      expect(api.publishContent).toHaveBeenCalledWith(3);
    });
  });

  it("cancelling the confirmation publishes nothing", async () => {
    api.contentQueue.mockResolvedValue([post({})]);
    const user = userEvent.setup();
    renderPage(<ContentPage />);

    await user.click(await screen.findByRole("button", { name: fr.content.publish }));
    await user.click(screen.getByRole("button", { name: fr.content.cancel }));

    expect(screen.queryByText(fr.content.publishTitle)).not.toBeInTheDocument();
    expect(api.publishContent).not.toHaveBeenCalled();
  });

  it("does not offer editing or publishing once a post is published", async () => {
    api.contentQueue.mockResolvedValue([post({ status: "published" })]);
    renderPage(<ContentPage />);

    expect(await screen.findByText(fr.content.publishedNoEdit)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: fr.content.edit })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: fr.content.publish })).not.toBeInTheDocument();
  });
});
