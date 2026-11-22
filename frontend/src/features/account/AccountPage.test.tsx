import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "../../api/endpoints";
import { ApiError } from "../../api/errors";
import { fr } from "../../i18n/fr";
import { renderPage } from "../../test/renderPage";
import { AccountPage } from "./AccountPage";

vi.mock("../../api/endpoints", () => ({
  endpoints: { deleteAccount: vi.fn() },
}));

const api = vi.mocked(endpoints);
const PASSWORD = "s3cret-passphrase";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("account deletion", () => {
  it("cannot be submitted until the password is typed and the warning is acknowledged", async () => {
    const user = userEvent.setup();
    renderPage(<AccountPage />);
    const button = screen.getByRole("button", { name: fr.account.delete });

    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText(fr.account.passwordLabel), PASSWORD);
    expect(button).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
  });

  it("sends the password typed again, and nothing else", async () => {
    api.deleteAccount.mockResolvedValue({ status: "deleted" });
    const user = userEvent.setup();
    renderPage(<AccountPage />);

    await user.type(screen.getByLabelText(fr.account.passwordLabel), PASSWORD);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: fr.account.delete }));

    await waitFor(() => {
      expect(api.deleteAccount).toHaveBeenCalledWith(PASSWORD);
    });
  });

  it("a wrong password says nothing was deleted, and keeps the form on screen", async () => {
    api.deleteAccount.mockRejectedValue(
      new ApiError({ status: 403, code: "http_error", message: "" }),
    );
    const user = userEvent.setup();
    renderPage(<AccountPage />);

    await user.type(screen.getByLabelText(fr.account.passwordLabel), "wrong-password");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: fr.account.delete }));

    expect(await screen.findByText(fr.account.wrongPassword)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: fr.account.delete })).toBeInTheDocument();
  });

  it("states what is removed, and that other users' data is not touched", () => {
    renderPage(<AccountPage />);
    expect(screen.getByText(fr.account.deleteWhat)).toBeInTheDocument();
    expect(screen.getByText(fr.account.deleteOthers)).toBeInTheDocument();
  });
});
