import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { KnowledgePanel } from "./KnowledgePanel";

vi.mock("../../api/endpoints", () => ({
  endpoints: { listKnowledge: vi.fn(), addKnowledge: vi.fn(), deleteKnowledge: vi.fn() },
}));

const api = vi.mocked(endpoints);

beforeEach(() => {
  vi.clearAllMocks();
  api.listKnowledge.mockResolvedValue([{ id: 5, content: "Built a RAG service on pgvector" }]);
});

describe("knowledge base", () => {
  it("explains what the knowledge base is used for", async () => {
    renderPage(<KnowledgePanel />);
    expect(await screen.findByText(fr.knowledge.explain)).toBeInTheDocument();
  });

  it("deletes an entry only after a second click", async () => {
    api.deleteKnowledge.mockResolvedValue({ status: "deleted" });
    const user = userEvent.setup();
    renderPage(<KnowledgePanel />);

    await user.click(await screen.findByRole("button", { name: fr.knowledge.remove }));
    expect(screen.getByText(fr.knowledge.confirmRemove)).toBeInTheDocument();
    expect(api.deleteKnowledge).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: fr.knowledge.confirm }));
    await waitFor(() => {
      expect(api.deleteKnowledge).toHaveBeenCalledWith(5);
    });
  });
});
