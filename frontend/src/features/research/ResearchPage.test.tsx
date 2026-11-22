import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { followRun } from "../../api/runFollow";
import type { ResearchResult } from "../../api/types";
import { renderPage } from "../../test/renderPage";
import { fr } from "../../i18n/fr";
import { ResearchAnswer, ResearchPage } from "./ResearchPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: { startResearch: vi.fn(), getResearch: vi.fn() },
}));
vi.mock("../../api/runFollow", () => ({ followRun: vi.fn(() => () => undefined) }));

const api = vi.mocked(endpoints);
const follow = vi.mocked(followRun);

const RESULT: ResearchResult = {
  answer: "Le poste est ouvert jusqu'en mars.",
  retrieved_at: "2026-11-22",
  language: "fr",
  claims: [
    { text: "Le poste est ouvert jusqu'en mars.", sources: [1], basis: "fetched" },
    { text: "Plus de 1 250 offres.", sources: [2], basis: "snippet" },
  ],
  sources: [
    { number: 1, title: "Offre", link: "https://jobs.example.com/1", fetched: true },
    { number: 2, title: "Annonce", link: "https://news.example.com/2", fetched: false },
  ],
  also_found: [{ title: "Autre", link: "javascript:void(0)", fetched: false }],
  unsupported: { numbers_snippet_only: 1, uncited: 0 },
  off_topic: 2,
  post_id: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.startResearch.mockResolvedValue({ run_id: "run-1", status: "queued" });
});

describe("research answer", () => {
  it("says on what each statement rests, lists the removed ones, and always reminds to check the source", () => {
    renderPage(<ResearchAnswer result={RESULT} draftPost={false} />);

    expect(screen.getByText(fr.research.basisFetched)).toBeInTheDocument();
    expect(screen.getByText(fr.research.basisSnippet)).toBeInTheDocument();
    expect(
      screen.getByText(/1 retirée\(s\) : chiffre présent seulement dans un extrait/),
    ).toBeInTheDocument();
    expect(screen.getByText(fr.research.verifyReminder)).toBeInTheDocument();
  });

  it("does not turn a javascript link from a page into a clickable link", () => {
    renderPage(<ResearchAnswer result={RESULT} draftPost={false} />);
    const links = screen.getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(links).toContain("https://jobs.example.com/1");
    expect(links.some((href) => href?.startsWith("javascript:"))).toBe(false);
  });
});

describe("research page", () => {
  it("starts a run with the question and the draft option, then follows that run", async () => {
    const user = userEvent.setup();
    renderPage(<ResearchPage />);

    await user.type(screen.getByLabelText(fr.research.questionLabel), "Combien d'offres ?");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: fr.research.submit }));

    await waitFor(() => {
      expect(api.startResearch).toHaveBeenCalledWith({
        request: "Combien d'offres ?",
        draft_post: true,
      });
    });
    await waitFor(() => {
      expect(follow).toHaveBeenCalledWith("run-1", expect.anything(), expect.anything());
    });
  });
});
