import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { followRun } from "../../api/runFollow";
import type { PreparationStatus } from "../../api/types";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { PreparationPage } from "./PreparationPage";

vi.mock("../../api/runFollow", () => ({ followRun: vi.fn(() => () => undefined) }));

const follow = vi.mocked(followRun);

function preparation(overrides: Partial<PreparationStatus>): PreparationStatus {
  return {
    run_id: "run-1",
    status: "running",
    step: "tailor_cv",
    application_id: null,
    error: null,
    stalled: false,
    heartbeat_at: null,
    ...overrides,
  };
}

/** The handlers the page gave to the follower, so the test can play the server's updates. */
function followerHandlers() {
  const call = follow.mock.calls[0];
  if (call === undefined) throw new Error("the page did not follow the run");
  const handlers = call[2] as {
    onUpdate: (run: PreparationStatus) => void;
    onSettled: (run: PreparationStatus) => void;
  };
  return { handlers };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("preparation progress", () => {
  it("follows the run it was opened for, through the shared follower", () => {
    renderPage(<PreparationPage />, {
      route: "/applications/run/run-1",
      path: "/applications/run/:runId",
    });
    expect(follow).toHaveBeenCalledWith("run-1", expect.anything(), expect.anything());
  });

  it("warns when the run stopped reporting progress", () => {
    renderPage(<PreparationPage />, {
      route: "/applications/run/run-1",
      path: "/applications/run/:runId",
    });
    const { handlers } = followerHandlers();

    act(() => {
      handlers.onUpdate(preparation({ stalled: true }));
    });

    expect(screen.getByText(fr.progress.stalled)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(fr.progress.steps.tailor_cv))).toBeInTheDocument();
  });

  it("says the preparation failed, with the server's message", () => {
    renderPage(<PreparationPage />, {
      route: "/applications/run/run-1",
      path: "/applications/run/:runId",
    });
    const { handlers } = followerHandlers();

    act(() => {
      handlers.onUpdate(preparation({ status: "failed", error: "model unavailable" }));
    });

    expect(
      screen.getByText(fr.progress.failed.replace("{error}", "model unavailable")),
    ).toBeInTheDocument();
  });
});
