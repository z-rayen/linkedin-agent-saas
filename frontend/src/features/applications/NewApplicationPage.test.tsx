import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { NewApplicationPage } from "./NewApplicationPage";

describe("new application form", () => {
  it("is prefilled from a watch result, with the title and the link, and never with a recipient", () => {
    const query = "?job_title=ML%20Intern&job_url=https%3A%2F%2Fjobs.example.com%2Fml-intern";
    renderPage(<NewApplicationPage />, {
      route: `/applications/new${query}`,
      path: "/applications/new",
    });

    expect(screen.getByLabelText(fr.newApplication.jobTitle)).toHaveValue("ML Intern");
    expect(screen.getByLabelText(fr.newApplication.jobUrl)).toHaveValue(
      "https://jobs.example.com/ml-intern",
    );
    expect(screen.getByLabelText(fr.newApplication.recipient)).toHaveValue("");
    expect(screen.getByText(fr.watch.prefilled)).toBeInTheDocument();
  });

  it("is empty when opened without a watch result", () => {
    renderPage(<NewApplicationPage />, { route: "/applications/new", path: "/applications/new" });
    expect(screen.getByLabelText(fr.newApplication.jobTitle)).toHaveValue("");
    expect(screen.queryByText(fr.watch.prefilled)).not.toBeInTheDocument();
  });
});
