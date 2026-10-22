import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import type { Notification } from "@/contracts";

import { EMPTY_INBOX, POLL_MS } from "./notifications-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  push.mockClear();
});

const LIST = "GET /notifications";

const note = (over: Partial<Notification> = {}): Notification => ({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000a001",
  project_id: null,
  workflow_id: null,
  type: "SUBSKILL_NEEDS_REVIEW",
  title: "MS01.1 needs review",
  body: "A sub-skill failed its tests.",
  link: "/projects/0190f3a2-7b1c-7d4e-8f00-000000000001/build",
  created_at: "2026-10-20T10:00:00Z",
  read_at: null,
  ...over,
});

const page = (items: Notification[], next_cursor: string | null = null) => ({
  body: { items, next_cursor },
});

// Dynamic import so each test gets a fresh module instance isolated from the mocked router.
const { NotificationCenter } = await import("./notification-center");

describe("Notification bell + inbox (E10.5)", () => {
  test("e10_5_t01 deep link opens the linked page", async () => {
    const n = note();
    fakeApi({ [LIST]: page([n]) });
    renderWithRefine(<NotificationCenter />);
    fireEvent.click(await screen.findByRole("button", { name: /notifications/i }));
    fireEvent.click(await screen.findByText(n.title));
    expect(push).toHaveBeenCalledWith(n.link);
  });

  test("e10_5_t02 badge count shows 99+ for 105 unread", async () => {
    const many = Array.from({ length: 100 }, (_, i) => note({ id: `id-${i}` }));
    fakeApi({ [LIST]: page(many, "more") });
    renderWithRefine(<NotificationCenter />);
    expect(await screen.findByText("99+")).toBeDefined();
  });

  test("e10_5_t03 mark read reverts on 503 with an error toast", async () => {
    const n = note();
    const { calls } = fakeApi({
      [LIST]: [page([n]), page([n])],
      [`POST /notifications/${n.id}/read`]: problem(503, "SERVICE_UNAVAILABLE"),
    });
    renderWithRefine(<NotificationCenter />);
    fireEvent.click(await screen.findByRole("button", { name: /notifications/i }));
    fireEvent.click(await screen.findByText(n.title));
    await waitFor(() =>
      expect(screen.getByText("Could not mark as read. Try again.")).toBeDefined(),
    );
    // reverted: the "New" tag is back, the item is still unread
    expect(await screen.findByText("New")).toBeDefined();
    expect(calls.some((c) => c.path.includes(`notifications/${n.id}/read`))).toBe(true);
  });

  test("e10_5_t04 XSS title is rendered literally, not executed", async () => {
    const n = note({ title: "<script>alert(1)</script>" });
    fakeApi({ [LIST]: page([n]) });
    renderWithRefine(<NotificationCenter />);
    fireEvent.click(await screen.findByRole("button", { name: /notifications/i }));
    expect(await screen.findByText("<script>alert(1)</script>")).toBeDefined();
    expect(document.querySelector("script[data-injected]")).toBeNull();
  });

  test("e10_5_t05 empty state shows 'You're all caught up'", async () => {
    fakeApi({ [LIST]: page([]) });
    renderWithRefine(<NotificationCenter />);
    fireEvent.click(await screen.findByRole("button", { name: /notifications/i }));
    expect(await screen.findByText(EMPTY_INBOX)).toBeDefined();
  });

  test("e10_5_t06 stale link to a removed project does not crash", async () => {
    const n = note({ link: "/projects/does-not-exist/build" });
    fakeApi({ [LIST]: page([n]) });
    renderWithRefine(<NotificationCenter />);
    fireEvent.click(await screen.findByRole("button", { name: /notifications/i }));
    expect(() => fireEvent.click(screen.getByText(n.title))).not.toThrow();
    expect(push).toHaveBeenCalledWith("/projects/does-not-exist/build");
  });

  test("e10_5_t07 unmount (logout) stops polling", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const n = note();
    const { calls } = fakeApi({ [LIST]: [page([n]), page([n]), page([n])] });
    const { unmount } = renderWithRefine(<NotificationCenter />);
    await act(async () => void vi.advanceTimersByTime(POLL_MS + 10));
    const polls = calls.filter((c) => c.method === "GET").length;
    expect(polls).toBeGreaterThan(0);
    unmount();
    await act(async () => void vi.advanceTimersByTime(POLL_MS * 3));
    expect(calls.filter((c) => c.method === "GET").length).toBe(polls);
  });
});
