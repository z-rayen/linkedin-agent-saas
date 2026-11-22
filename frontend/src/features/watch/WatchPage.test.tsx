import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import type { WatchResult } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { WatchPage, prepareQuery } from "./WatchPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: { watchResults: vi.fn() },
}));

const api = vi.mocked(endpoints);
const LINK = "https://jobs.example.com/ml-intern?ref=watch";

const RESULTS: WatchResult[] = [
  {
    id: 1,
    type: "job",
    title: "ML Intern",
    link: LINK,
    match_score: 82,
    justification: "Python and NLP match the profile.",
    cover_letter: null,
  },
  {
    id: 2,
    type: "cert",
    title: "Deep Learning certificate",
    link: "https://courses.example.com/dl",
    match_score: 60,
    justification: null,
    cover_letter: null,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.watchResults.mockResolvedValue(RESULTS);
});

describe("watch results", () => {
  it("offers 'Prepare application' for offers only, opening the form with the title and the link", async () => {
    renderPage(<WatchPage />);

    const prepare = await screen.findAllByRole("link", { name: fr.watch.prepare });
    expect(prepare).toHaveLength(1);
    const href = prepare[0]?.getAttribute("href") ?? "";
    expect(href.startsWith("/applications/new?")).toBe(true);
    expect(new URLSearchParams(href.split("?")[1] ?? "").get("job_url")).toBe(LINK);
    expect(new URLSearchParams(href.split("?")[1] ?? "").get("job_title")).toBe("ML Intern");
  });

  it("builds the query string without any recipient", () => {
    const query = prepareQuery(RESULTS[0] as WatchResult);
    expect(query).not.toContain("recipient");
    expect(new URLSearchParams(query).get("job_title")).toBe("ML Intern");
  });

  it("shows the empty state when the watch has found nothing yet", async () => {
    api.watchResults.mockResolvedValue([]);
    renderPage(<WatchPage />);
    expect(await screen.findByText(fr.watch.empty)).toBeInTheDocument();
  });
});
