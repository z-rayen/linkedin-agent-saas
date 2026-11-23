import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { followRun } from "../../api/runFollow";
import type { WatchResult, WatchResultPage } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { PAGE_SIZE, WatchPage, prepareQuery } from "./WatchPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: {
    watchResults: vi.fn(),
    startWatchRun: vi.fn(),
    getWatchRun: vi.fn(),
    deleteWatchResult: vi.fn(),
  },
}));
vi.mock("../../api/runFollow", () => ({ followRun: vi.fn(() => () => undefined) }));

const api = vi.mocked(endpoints);
const follow = vi.mocked(followRun);
const LINK = "https://jobs.example.com/ml-intern?ref=watch";

const JOB: WatchResult = {
  id: 1,
  type: "job",
  title: "ML Intern",
  link: LINK,
  match_score: 82,
  justification: "Python and NLP match the profile.",
  cover_letter: null,
  company: "Ministry of Research",
  job_title: null,
  fetched: true,
  unverified: false,
  created_at: "2026-11-20T10:00:00",
};
const CERT: WatchResult = {
  id: 2,
  type: "cert",
  title: "Deep Learning certificate",
  link: "https://courses.example.com/dl",
  match_score: 60,
  justification: null,
  cover_letter: null,
  company: null,
  job_title: null,
  fetched: false,
  unverified: true,
  created_at: null,
};

function page(items: WatchResult[], total = items.length, offset = 0): WatchResultPage {
  return { items, total, limit: PAGE_SIZE, offset };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.watchResults.mockResolvedValue(page([JOB, CERT]));
  api.startWatchRun.mockResolvedValue({ run_id: "run-watch-1", status: "queued" });
});

describe("watch results", () => {
  it("shows whether each result was verified on its full page, and the grounded company", async () => {
    renderPage(<WatchPage />);

    expect(await screen.findByText(JOB.title)).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getByText(fr.watch.unverified)).toBeInTheDocument();
    expect(
      screen.getByText(fr.watch.company.replace("{company}", "Ministry of Research")),
    ).toBeInTheDocument();
  });

  it("offers 'Prepare application' for offers only, with the title and the link and never a recipient", async () => {
    renderPage(<WatchPage />);

    const prepare = await screen.findAllByRole("link", { name: fr.watch.prepare });
    expect(prepare).toHaveLength(1);
    const href = prepare[0]?.getAttribute("href") ?? "";
    expect(href.startsWith("/applications/new?")).toBe(true);
    const params = new URLSearchParams(href.split("?")[1] ?? "");
    expect(params.get("job_url")).toBe(LINK);
    expect(params.get("job_title")).toBe("ML Intern");
    expect(params.has("recipient_email")).toBe(false);
  });

  it("builds the prepare query without any recipient", () => {
    const query = prepareQuery(JOB);
    expect(query).not.toContain("recipient");
  });

  it("sends the filters to the server and returns to the first page when a filter changes", async () => {
    const user = userEvent.setup();
    renderPage(<WatchPage />);
    await screen.findByText(JOB.title);

    await user.selectOptions(screen.getByLabelText(fr.watch.filterKind), "job");
    await waitFor(() => {
      expect(api.watchResults).toHaveBeenLastCalledWith(
        { limit: PAGE_SIZE, offset: 0, kind: "job" },
        expect.anything(),
      );
    });
    await user.selectOptions(screen.getByLabelText(fr.watch.filterVerified), "yes");
    await waitFor(() => {
      expect(api.watchResults).toHaveBeenLastCalledWith(
        { limit: PAGE_SIZE, offset: 0, kind: "job", verified: true },
        expect.anything(),
      );
    });
  });

  it("pages through the results with the total from the server", async () => {
    api.watchResults.mockResolvedValue(page([JOB, CERT], PAGE_SIZE * 2));
    const user = userEvent.setup();
    renderPage(<WatchPage />);

    await user.click(await screen.findByRole("button", { name: fr.watch.next }));
    await waitFor(() => {
      expect(api.watchResults).toHaveBeenLastCalledWith(
        { limit: PAGE_SIZE, offset: PAGE_SIZE },
        expect.anything(),
      );
    });
  });

  it("shows the empty state, and a different one when filters hide everything", async () => {
    api.watchResults.mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderPage(<WatchPage />);
    expect(await screen.findByText(fr.watch.empty)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(fr.watch.filterKind), "cert");
    expect(await screen.findByText(fr.watch.emptyFiltered)).toBeInTheDocument();
  });

  it("reminds the user that offers must be checked on the source site", async () => {
    renderPage(<WatchPage />);
    expect(await screen.findByText(fr.watch.reminder)).toBeInTheDocument();
  });
});

describe("starting and following a watch run", () => {
  it("starts a run and follows it with the shared follower", async () => {
    const user = userEvent.setup();
    renderPage(<WatchPage />);

    await user.click(await screen.findByRole("button", { name: fr.watch.start }));
    await waitFor(() => {
      expect(api.startWatchRun).toHaveBeenCalledTimes(1);
      expect(follow).toHaveBeenCalledWith("run-watch-1", expect.anything(), expect.anything());
    });
  });
});

describe("removing a result", () => {
  it("removes a result only after a second click", async () => {
    api.deleteWatchResult.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage(<WatchPage />);

    const [firstRemove] = await screen.findAllByRole("button", { name: fr.watch.remove });
    await user.click(firstRemove as HTMLElement);
    expect(api.deleteWatchResult).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: fr.watch.delete }));
    await waitFor(() => {
      expect(api.deleteWatchResult).toHaveBeenCalledWith(1);
    });
  });
});
