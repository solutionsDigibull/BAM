import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { setBamApi } from "../api/adapter";
import type { UploadRecord } from "../api/types";
import { ApiFailure } from "../http/http";
import { OpenFileButton, openFile } from "./OpenFile";

const upload = (over: Partial<UploadRecord> = {}): UploadRecord =>
  ({ id: "d1", name: "spec.pdf", scan: "verified", released: true, status: "stored", clamavVerdict: "clean", ...over }) as UploadRecord;
const inAMinute = () => new Date(Date.now() + 60_000).toISOString();
const link = (url = "/api/v1/document-access/d1?sig=x") => ({ url, expiresAt: inAMinute() });

let clicked: HTMLAnchorElement[] = [];
beforeEach(() => {
  clicked = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push(this);
  });
});
afterEach(() => {
  cleanup();
  setBamApi(null);
  vi.restoreAllMocks();
});

describe("the Open action of a document row (#895)", () => {
  test("test_895_ui2b_t01_open_asks_for_a_link_and_follows_it_in_a_new_tab", async () => {
    const getVerifiedFileAccess = vi.fn().mockResolvedValue(link());
    setBamApi({ getVerifiedFileAccess } as never);
    render(<OpenFileButton upload={upload()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open spec.pdf" }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(getVerifiedFileAccess).toHaveBeenCalledWith("d1");
    expect(clicked[0]!.getAttribute("href")).toBe("/api/v1/document-access/d1?sig=x");
    expect(clicked[0]!.target).toBe("_blank");
    expect(clicked[0]!.rel).toContain("noopener");
  });

  test("test_895_ui2b_t02_the_button_shows_loading_and_cannot_be_clicked_twice", async () => {
    let resolve!: (v: ReturnType<typeof link>) => void;
    const getVerifiedFileAccess = vi.fn().mockReturnValue(new Promise((r) => (resolve = r)));
    setBamApi({ getVerifiedFileAccess } as never);
    render(<OpenFileButton upload={upload()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open spec.pdf" }));
    const busy = await screen.findByRole("button", { name: "Open spec.pdf" });
    expect(busy.textContent).toContain("Opening");
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(busy);
    resolve(link());
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(getVerifiedFileAccess).toHaveBeenCalledTimes(1);
  });

  test.each([
    ["scanning", { scan: "scanning", released: false, clamavVerdict: "pending" }, /still being checked/i],
    ["blocked", { scan: "blocked", released: false, clamavVerdict: "threat" }, /blocked by the scan and quarantined/i],
    ["scan error", { scanError: "The scan did not finish.", released: false }, /scan did not finish/i],
  ] as const)("test_895_ui2b_t03_a_%s_file_is_disabled_with_the_reason", (_n, over, reason) => {
    const getVerifiedFileAccess = vi.fn();
    setBamApi({ getVerifiedFileAccess } as never);
    render(<OpenFileButton upload={upload(over as Partial<UploadRecord>)} />);
    const b = screen.getByRole("button", { name: "Open spec.pdf" }) as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    expect(b.title).toMatch(reason);
    expect(document.body.textContent).toMatch(reason);
    fireEvent.click(b);
    expect(getVerifiedFileAccess).not.toHaveBeenCalled();
  });

  test("test_895_ui2b_t04_a_refusal_shows_the_servers_reason_in_place", async () => {
    const detail = "This file is in the recycle bin. Restore it to open it.";
    setBamApi({ getVerifiedFileAccess: () => Promise.reject(new ApiFailure("conflict", 409, { detail })) } as never);
    render(<OpenFileButton upload={upload()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open spec.pdf" }));
    expect((await screen.findByRole("alert")).textContent).toBe(detail);
    expect(clicked).toHaveLength(0);
    expect((screen.getByRole("button", { name: "Open spec.pdf" }) as HTMLButtonElement).disabled).toBe(false);
  });

  test.each([
    [403, /not allowed to open files/],
    [404, /no longer exists/],
    [410, /permanently deleted/],
  ])("test_895_ui2b_t05_a_%i_is_said_plainly", async (status, text) => {
    setBamApi({ getVerifiedFileAccess: () => Promise.reject(new ApiFailure("x", status)) } as never);
    render(<OpenFileButton upload={upload()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open spec.pdf" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(text);
  });

  test("test_895_ui2b_t06_a_link_that_already_ran_out_is_asked_for_once_more", async () => {
    const expired = { url: "/old", expiresAt: new Date(Date.now() - 1000).toISOString() };
    const getVerifiedFileAccess = vi.fn().mockResolvedValueOnce(expired).mockResolvedValueOnce(link("/fresh"));
    setBamApi({ getVerifiedFileAccess } as never);
    await openFile("d1");
    expect(getVerifiedFileAccess).toHaveBeenCalledTimes(2);
    expect(clicked[0]!.getAttribute("href")).toBe("/fresh");
  });

  test("test_895_ui2b_t06_a_link_that_ran_out_twice_is_an_error_not_a_loop", async () => {
    const expired = { url: "/old", expiresAt: new Date(Date.now() - 1000).toISOString() };
    const getVerifiedFileAccess = vi.fn().mockResolvedValue(expired);
    setBamApi({ getVerifiedFileAccess } as never);
    await expect(openFile("d1")).rejects.toThrow(/ran out/);
    expect(getVerifiedFileAccess).toHaveBeenCalledTimes(2);
    expect(clicked).toHaveLength(0);
  });
});
