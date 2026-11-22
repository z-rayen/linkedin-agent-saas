import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import type { ApplicationDetail } from "../../api/types";
import { I18nProvider } from "../../i18n/I18nProvider";
import { fr } from "../../i18n/fr";
import { ReviewPage } from "./ReviewPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: {
    getApplication: vi.fn(),
    mailMode: vi.fn(),
    editApplication: vi.fn(),
    approveApplication: vi.fn(),
    sendApplication: vi.fn(),
    resolveApplication: vi.fn(),
    pdfBlob: vi.fn(),
  },
}));

const api = vi.mocked(endpoints);
const APPLICATION_ID = 7;
const RECIPIENT = "jobs@acme.test";
const NOT_FOUND = 404;

const base: ApplicationDetail = {
  id: APPLICATION_ID,
  status: "prepared",
  company: "Acme",
  company_key: "acme",
  job_title: "ML intern",
  recipient_email: RECIPIENT,
  created_at: null,
  subject: "Application",
  body: "Hello",
  grounding_problems: [],
  cv_lines: [],
  has_pdf: false,
  pdf_sha256: null,
  last_error: null,
  approved_at: null,
  sent_at: null,
};

function renderReview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/applications/${String(APPLICATION_ID)}`]}>
          <Routes>
            <Route path="/applications/:applicationId" element={<ReviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.pdfBlob.mockResolvedValue(new Blob(["%PDF"]));
});

describe("review rules on the screen", () => {
  it("keeps approval disabled while a grounding problem remains, and shows the problem", async () => {
    api.getApplication.mockResolvedValue({
      ...base,
      grounding_problems: [
        { kind: "number", location: "line 1", term: "40%", message: "not in the master CV" },
      ],
      cv_lines: [{ section: "skill", text: "Python", source_ids: ["s1"] }],
    });
    api.mailMode.mockResolvedValue({ mode: "redirected" });

    renderReview();

    expect(await screen.findByRole("button", { name: fr.review.approve })).toBeDisabled();
    expect(screen.getByText(fr.review.approveBlocked)).toBeInTheDocument();
    expect(screen.getByText(fr.review.problemKind.number)).toBeInTheDocument();
  });

  it("allows approval once no problem remains", async () => {
    api.getApplication.mockResolvedValue(base);
    api.mailMode.mockResolvedValue({ mode: "dry_run" });

    renderReview();

    expect(await screen.findByRole("button", { name: fr.review.approve })).toBeEnabled();
  });

  it("states the sending mode and keeps sending disabled until the exact address is typed", async () => {
    api.getApplication.mockResolvedValue({ ...base, status: "approved" });
    api.mailMode.mockResolvedValue({ mode: "redirected" });
    const user = userEvent.setup();

    renderReview();

    expect(await screen.findByText(fr.send.modeRedirected)).toBeInTheDocument();
    const send = screen.getByRole("button", { name: fr.send.send });
    const confirm = screen.getByLabelText(fr.send.confirmLabel);
    expect(send).toBeDisabled();

    await user.type(confirm, "hr@acme.test");
    expect(send).toBeDisabled();

    await user.clear(confirm);
    await user.type(confirm, RECIPIENT);
    expect(send).toBeEnabled();
  });

  it("keeps sending disabled and says the mode is unknown when the server does not report it", async () => {
    api.getApplication.mockResolvedValue({ ...base, status: "approved" });
    api.mailMode.mockRejectedValue(
      new ApiError({ status: NOT_FOUND, code: "http_error", message: "" }),
    );
    const user = userEvent.setup();

    renderReview();

    expect(await screen.findByText(fr.send.modeUnknown)).toBeInTheDocument();
    await user.type(screen.getByLabelText(fr.send.confirmLabel), RECIPIENT);
    expect(screen.getByRole("button", { name: fr.send.send })).toBeDisabled();
  });

  it("sends the application back to prepared after an edit", async () => {
    const prepared: ApplicationDetail = { ...base, status: "prepared", subject: "Application v2" };
    api.getApplication
      .mockResolvedValueOnce({ ...base, status: "approved" })
      .mockResolvedValue(prepared);
    api.mailMode.mockResolvedValue({ mode: "dry_run" });
    api.editApplication.mockResolvedValue(prepared);
    const user = userEvent.setup();

    renderReview();

    const subject = await screen.findByLabelText(fr.review.subject);
    await user.type(subject, " v2");
    await user.click(screen.getByRole("button", { name: fr.review.save }));

    await waitFor(() => {
      expect(api.editApplication).toHaveBeenCalledWith(APPLICATION_ID, {
        subject: "Application v2",
        body: "Hello",
        recipient_email: RECIPIENT,
      });
    });
    expect(await screen.findByText(fr.review.saved)).toBeInTheDocument();
    expect(await screen.findByText(fr.applications.status.prepared)).toBeInTheDocument();
  });
});
