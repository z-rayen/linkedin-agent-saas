import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApplicationDetail } from "../../../api/types";
import { I18nProvider } from "../../../i18n/I18nProvider";
import { fr } from "../../../i18n/fr";
import { ProblemsSection } from "./ProblemsSection";
import { SendSection } from "./SendSection";

const RECIPIENT = "hr.recipient@acme.example";

const approved: ApplicationDetail = {
  id: 1,
  status: "approved",
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

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </I18nProvider>,
  );
}

describe("the send confirmation", () => {
  it("never displays the recipient address under the confirmation field", () => {
    renderWithProviders(<SendSection detail={approved} mode="dry_run" />);

    expect(screen.queryByText(RECIPIENT, { exact: false })).toBeNull();
    expect(screen.getByText(fr.send.confirmHint)).toBeTruthy();
  });

  it("refuses a paste into the confirmation field, so the address must be typed", () => {
    renderWithProviders(<SendSection detail={approved} mode="dry_run" />);
    const input = screen.getByLabelText(fr.send.confirmLabel);

    const paste = createEvent.paste(input);
    fireEvent(input, paste);

    expect(paste.defaultPrevented).toBe(true);
  });
});

describe("the grounding banner", () => {
  it("says what was checked and that the meaning still needs the reader", () => {
    renderWithProviders(<ProblemsSection total={0} problems={[]} />);

    expect(screen.getByText(fr.review.noProblems)).toBeTruthy();
    expect(fr.review.noProblems).toContain("mots et les nombres");
    expect(fr.review.noProblems).toContain("relisez-la");
    expect(fr.review.noProblems).not.toMatch(/chaque affirmation/);
  });
});
