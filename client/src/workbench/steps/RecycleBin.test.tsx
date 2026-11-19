import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, test, vi } from "vitest";

import { setBamApi } from "../api/adapter";
import type { Snapshot } from "../api/types";
import { ApiFailure } from "../http/http";
import { RecycleBin } from "./RecycleBin";

const snap = (deleted: Snapshot["deleted"], editable = true) =>
  ({ uploads: [], deleted, steps: [{ editable }] }) as unknown as Snapshot;
const bin = [{ id: "d1", name: "old-rfq.pdf", size: 2048, deletedAt: "2026-11-05T10:00:00Z", purgeAfter: "2027-02-03T10:00:00Z" }];
const view = (s: Snapshot, hide = false) =>
  render(<QueryClientProvider client={new QueryClient()}><RecycleBin snap={s} hideWhenEmpty={hide} /></QueryClientProvider>);

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the recycle bin on Upload (#712)", () => {
  test("test_712_empty_bin_says_so_and_can_stay_hidden", () => {
    view(snap([]));
    expect(screen.getByText(/No removed files/)).toBeTruthy();
    cleanup();
    const { container } = view(snap([]), true);
    expect(container.textContent).toBe("");
  });

  test("test_712_a_removed_file_is_listed_with_restore_and_restores_through_the_api", async () => {
    const restoreUpload = vi.fn().mockResolvedValue(undefined);
    setBamApi({ restoreUpload } as never);
    view(snap(bin));
    expect(screen.getByText("old-rfq.pdf")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Restore/ }));
    await waitFor(() => expect(restoreUpload).toHaveBeenCalledWith("d1"));
  });

  test("test_712_a_refused_restore_shows_the_servers_reason_in_place", async () => {
    setBamApi({ restoreUpload: () => Promise.reject(new ApiFailure("gone", 410)) } as never);
    view(snap(bin));
    fireEvent.click(screen.getByRole("button", { name: /Restore/ }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/permanently deleted/);
  });

  test("test_712_forbidden_or_closed_project_is_read_only_without_a_restore_button", () => {
    view(snap(bin, false));
    expect(screen.queryByRole("button", { name: /Restore/ })).toBeNull();
    expect(screen.getByText("Read only")).toBeTruthy();
  });
});
