import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine, user } from "../projects/test-harness";

import {
  EMPTY_MESSAGE,
  LAST_ADMIN_MESSAGE,
  STALE_USER_MESSAGE,
  USER_EXISTS_MESSAGE,
} from "./user-api";
import { UsersScreen } from "./users-screen";

const LIST = "GET /admin/users"; // the list and the active-admin count (query string ignored)
const ADMIN_A = user("0190f3a2-7b1c-7d4e-8f00-0000000000a1", "admin-A", ["sbam-admin"]);
const ADMIN_B = user("0190f3a2-7b1c-7d4e-8f00-0000000000a2", "admin-B", ["sbam-admin"]);
const FDE_A1 = user("0190f3a2-7b1c-7d4e-8f00-0000000000f1", "fde-A1");

afterEach(() => vi.unstubAllGlobals());

const rowAction = async (label: RegExp) =>
  fireEvent.click(await screen.findByRole("button", { name: label }));

// antd tables and modals in jsdom are slow (several seconds a test on a busy machine).
describe("Users & Roles screen (E2.6)", { timeout: 20_000 }, () => {
  test("e2_6_t01 only the signed-in admin: table plus 'Invite your first FDE'", async () => {
    fakeApi({ [LIST]: { body: { items: [ADMIN_A] } } });
    renderWithRefine(<UsersScreen />);
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeDefined();
    expect(screen.getByText(ADMIN_A.email!)).toBeDefined(); // the table still shows
    expect(screen.getAllByRole("button", { name: /Invite user/ }).length).toBeGreaterThan(0);
  });

  test("e2_6_t02 an invalid email shows an inline error, keeps submit disabled, sends nothing", async () => {
    const api = fakeApi({ [LIST]: { body: { items: [ADMIN_A] } } });
    renderWithRefine(<UsersScreen />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Invite user/ }))[0]!);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "abc" } });
    expect(await within(dialog).findByText("Enter a valid email address.")).toBeDefined();
    const send = within(dialog).getByRole("button", { name: "Send invite" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(send);
    expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  test("invite sends the email and roles; 409 USER_EXISTS lands on the email field", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [ADMIN_A] } },
      "POST /admin/users/invite": problem(409, "USER_EXISTS"),
    });
    renderWithRefine(<UsersScreen />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Invite user/ }))[0]!);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Email"), {
      target: { value: " fde3@acme.com " },
    });
    const send = within(dialog).getByRole("button", { name: "Send invite" });
    await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(send);
    expect(await within(dialog).findByText(USER_EXISTS_MESSAGE)).toBeDefined();
    const post = api.calls.find((c) => c.method === "POST")!;
    expect(JSON.parse(String(post.init.body))).toEqual({
      email: "fde3@acme.com",
      roles: ["sbam-fde"],
    });
  });

  test("e2_6_t04 409 LAST_ADMIN shows the banner and the role chips stay as they were", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [ADMIN_A, ADMIN_B, FDE_A1] } }, // two admins: the UI allows it
      [`PATCH /admin/users/${ADMIN_A.id}`]: problem(409, "LAST_ADMIN"), // the other one left
    });
    renderWithRefine(<UsersScreen />);
    await rowAction(/Change roles of admin-A/);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "FDE" }));
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Admin" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save roles" }));
    expect(await within(dialog).findByText(LAST_ADMIN_MESSAGE)).toBeDefined();

    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(String(patch.init.body))).toEqual({ roles: ["sbam-fde"] });
    expect(new Headers(patch.init.headers).get("if-match")).toBe('"1"');
    const chips = screen.getByTestId(`roles-${ADMIN_A.id}`);
    expect(within(chips).getByText("Admin")).toBeDefined(); // no optimistic update
    expect(within(chips).queryByText("FDE")).toBeNull();
  });

  test("e2_6_t05 a stale PATCH (412) says someone else changed the user and Reload refetches", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [ADMIN_A, FDE_A1] } },
      [`PATCH /admin/users/${FDE_A1.id}`]: problem(412, "PRECONDITION_FAILED"),
    });
    renderWithRefine(<UsersScreen />);
    await rowAction(/Change roles of fde-A1/);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Admin" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save roles" }));
    expect(await within(dialog).findByText(STALE_USER_MESSAGE)).toBeDefined();

    const before = api.calls.filter((c) => c.method === "GET").length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Reload" }));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "GET").length).toBeGreaterThan(before),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  test("e2_6_t07 a display name with markup renders as literal text", async () => {
    const evil = { ...FDE_A1, display_name: "<img src=x onerror=alert(1)>" };
    fakeApi({ [LIST]: { body: { items: [ADMIN_A, evil] } } });
    const { container } = renderWithRefine(<UsersScreen />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeDefined();
    expect(container.querySelector("img")).toBeNull();
  });

  test("the only active admin can't be deactivated or lose the Admin role in the UI", async () => {
    const api = fakeApi({ [LIST]: { body: { items: [ADMIN_A, FDE_A1] } } });
    renderWithRefine(<UsersScreen />);
    const deactivate = await screen.findByRole("button", { name: /Deactivate admin-A/ });
    expect((deactivate as HTMLButtonElement).disabled).toBe(true);
    await rowAction(/Change roles of admin-A/);
    const dialog = await screen.findByRole("dialog");
    expect(
      (within(dialog).getByRole("checkbox", { name: "Admin" }) as HTMLInputElement).disabled,
    ).toBe(true);
    // The FDE row is unaffected; the count came from the role=sbam-admin&active=true query.
    expect(
      (screen.getByRole("button", { name: /Deactivate fde-A1/ }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(
      api.calls.some((c) => c.path.includes("role=sbam-admin") && c.path.includes("active=true")),
    ).toBe(true);
  });

  test("deactivate needs the email typed back and sends active=false with If-Match", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [ADMIN_A, FDE_A1] } },
      [`PATCH /admin/users/${FDE_A1.id}`]: {
        body: { ...FDE_A1, status: "DEACTIVATED", row_version: 2 },
      },
    });
    renderWithRefine(<UsersScreen />);
    await rowAction(/Deactivate fde-A1/);
    const dialog = await screen.findByRole("dialog");
    const ok = within(dialog).getByRole("button", { name: "Deactivate" });
    expect((ok as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Confirm deactivation"), {
      target: { value: FDE_A1.email },
    });
    await waitFor(() => expect((ok as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ok);
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(String(patch.init.body))).toEqual({ active: false });
    expect(new Headers(patch.init.headers).get("if-match")).toBe('"1"');
  });

  test.each([0, 1, 100])("renders with %i rows", async (n) => {
    const items = Array.from({ length: n }, (_, i) =>
      user(`0190f3a2-7b1c-7d4e-8f00-${String(i).padStart(12, "0")}`, `fde-${i}`),
    );
    fakeApi({ [LIST]: { body: { items } } });
    renderWithRefine(<UsersScreen />);
    expect(await screen.findByText(`${n} of ${n} rows`)).toBeDefined();
    if (n <= 1) expect(screen.getByText(EMPTY_MESSAGE)).toBeDefined();
    else expect(screen.queryByText(EMPTY_MESSAGE)).toBeNull();
  });

  test("list error shows the problem detail with a retry", async () => {
    fakeApi({ [LIST]: problem(503, "IDP_UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<UsersScreen />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("very long display names and emails are truncated with an ellipsis", async () => {
    const long = {
      ...FDE_A1,
      display_name: "N".repeat(300),
      email: `${"e".repeat(240)}@acme.example`,
    };
    fakeApi({ [LIST]: { body: { items: [ADMIN_A, long] } } });
    renderWithRefine(<UsersScreen />);
    const name = await screen.findByText(long.display_name);
    const email = screen.getByText(long.email);
    for (const el of [name, email]) expect(el.closest(".ant-typography-ellipsis")).not.toBeNull(); // clipped, full text on hover
  });
});
