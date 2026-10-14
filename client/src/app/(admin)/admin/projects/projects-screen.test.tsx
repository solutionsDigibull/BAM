import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { STALE_MESSAGE } from "@/components/ui-kit";

import { CONTEXT_NOTES_TEMPLATE } from "./master-guide";
import { DUPLICATE_NAME_MESSAGE, EMPTY_MESSAGE, PIPELINE_RUNNING_MESSAGE } from "./project-api";
import { ProjectsScreen } from "./projects-screen";
import { fakeApi, problem, project, renderWithRefine, user } from "./test-harness";

const P = project();
const LIST = "GET /admin/projects";
const ONE = `GET /admin/projects/${P.id}`;

afterEach(() => vi.unstubAllGlobals());

const openRowAction = async (label: RegExp) => {
  fireEvent.click(await screen.findByRole("button", { name: label }));
};

describe("Admin Projects screens (E3.9)", () => {
  test("e3_9_t01 empty list shows 'Create your first project'", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    renderWithRefine(<ProjectsScreen />);
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeDefined();
    expect(screen.getByRole("button", { name: /New project/ })).toBeDefined();
  });

  test("list error shows the problem detail with a retry", async () => {
    fakeApi({ [LIST]: problem(503, "ORCHESTRATOR_UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<ProjectsScreen />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("list splits Active and Archived and archived rows are read-only", async () => {
    const old = project({
      id: "0190f3a2-7b1c-7d4e-8f00-000000000002",
      name: "Cable assembly",
      status: "ARCHIVED",
    });
    fakeApi({ [LIST]: { body: { items: [P, old] } } });
    renderWithRefine(<ProjectsScreen />);
    expect(await screen.findByText(P.name)).toBeDefined();
    expect(screen.queryByText("Cable assembly")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Archived/ }));
    expect(await screen.findByText("Cable assembly")).toBeDefined();
    expect(screen.getByText("Read-only")).toBeDefined();
    expect(screen.queryByRole("button", { name: /Archive Cable assembly/ })).toBeNull();
  });

  test("e3_9_t03 409 DUPLICATE_NAME lands on the name field", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [] } },
      "POST /admin/projects": problem(409, "DUPLICATE_NAME"),
    });
    renderWithRefine(<ProjectsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /New project/ }));
    fireEvent.change(await screen.findByLabelText("Project name"), {
      target: { value: "  EMS RFQ costing  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText(DUPLICATE_NAME_MESSAGE)).toBeDefined();
    const post = api.calls.find((c) => c.method === "POST")!;
    expect(JSON.parse(String(post.init.body))).toEqual({
      name: "EMS RFQ costing",
      client_name: null,
      description: null,
      context_notes: null,
    });
  });

  test("create rejects an empty name before calling the API", async () => {
    const api = fakeApi({ [LIST]: { body: { items: [] } } });
    renderWithRefine(<ProjectsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /New project/ }));
    fireEvent.change(await screen.findByLabelText("Project name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("Name is required.")).toBeDefined();
    expect(api.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  test("context notes template comes from the admin masters workbook", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    renderWithRefine(<ProjectsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /New project/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Insert template" }));
    const notes = screen.getByLabelText("Context notes") as HTMLTextAreaElement;
    expect(notes.value).toBe(CONTEXT_NOTES_TEMPLATE);
    expect(notes.value).toContain("RFQ ID + revision + scenario + FG");
  });

  test("e3_9_t04 archive blocked by 409 PIPELINE_RUNNING shows the banner", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [P] } },
      [`POST /admin/projects/${P.id}/archive`]: problem(409, "PIPELINE_RUNNING"),
    });
    renderWithRefine(<ProjectsScreen />);
    await openRowAction(new RegExp(`Archive ${P.name.replace(/[()]/g, "\\$&")}`));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Archive" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true); // name not typed yet
    fireEvent.change(within(dialog).getByLabelText("Project name to confirm"), {
      target: { value: P.name },
    });
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(confirm);
    expect(await screen.findByText(PIPELINE_RUNNING_MESSAGE)).toBeDefined();
    expect(api.calls.filter((c) => c.path.endsWith("/archive"))).toHaveLength(1);
  });

  test("e3_9_t05 a stale edit (412) opens the reload dialog and keeps the admin's edits", async () => {
    const fresh = project({ name: "Renamed by someone else", row_version: 2 });
    const api = fakeApi({
      [LIST]: { body: { items: [P] } },
      [ONE]: [
        { body: P, etag: '"1"' },
        { body: fresh, etag: '"2"' },
      ],
      [`PATCH /admin/projects/${P.id}`]: problem(412, "PRECONDITION_FAILED"),
    });
    renderWithRefine(<ProjectsScreen />);
    await openRowAction(/^Edit /);
    const name = (await screen.findByLabelText("Project name")) as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe(P.name));
    fireEvent.change(name, { target: { value: "My edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
    expect(name.value).toBe("My edit"); // not overwritten silently
    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(new Headers(patch.init.headers).get("if-match")).toBe('"1"');

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() =>
      expect((screen.getByLabelText("Project name") as HTMLInputElement).value).toBe(fresh.name),
    );
  });

  test("member picker offers only active FDEs and saves with replace semantics", async () => {
    const api = fakeApi({
      [LIST]: { body: { items: [P] } },
      "GET /admin/users": {
        body: {
          items: [
            user("u1", "Sourcing Engineer"),
            user("u2", "Cost Estimator"),
            user("u3", "Tenant Admin", ["sbam-admin"]),
            user("u4", "Former FDE", ["sbam-fde"], "DEACTIVATED"),
            user("u5", "Invited FDE", ["sbam-fde"], "INVITED"),
          ],
        },
      },
      [`GET /admin/projects/${P.id}/members`]: {
        body: { project_id: P.id, members: [user("u1", "Sourcing Engineer")] },
      },
      [`PUT /admin/projects/${P.id}/members`]: {
        body: { project_id: P.id, members: [user("u1", "Sourcing Engineer")] },
      },
    });
    renderWithRefine(<ProjectsScreen />);
    await openRowAction(/^FDEs of /);
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("Sourcing Engineer")).toBeDefined(); // the saved member
    fireEvent.mouseDown(within(drawer).getByRole("combobox"));
    await screen.findAllByRole("option");
    const listbox = document.querySelector(".ant-select-dropdown")!;
    expect(within(listbox as HTMLElement).getAllByText("Cost Estimator").length).toBeGreaterThan(0);
    expect(screen.queryByText("Tenant Admin")).toBeNull();
    expect(screen.queryByText("Former FDE")).toBeNull();
    expect(screen.queryByText("Invited FDE")).toBeNull();

    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
    const put = api.calls.find((c) => c.method === "PUT")!;
    expect(JSON.parse(String(put.init.body))).toEqual({ user_ids: ["u1"] });
    expect(api.calls.find((c) => c.path.startsWith("/admin/users"))!.path).toBe(
      "/admin/users?role=sbam-fde&active=true",
    );
  });

  test("zero FDEs in the tenant shows the picker empty state with an invite link", async () => {
    fakeApi({
      [LIST]: { body: { items: [P] } },
      "GET /admin/users": { body: { items: [] } },
      [`GET /admin/projects/${P.id}/members`]: { body: { project_id: P.id, members: [] } },
    });
    renderWithRefine(<ProjectsScreen />);
    await openRowAction(/^FDEs of /);
    expect(await screen.findByText("No active FDEs in this tenant yet.")).toBeDefined();
    expect(screen.getByRole("link", { name: /Invite an FDE/ }).getAttribute("href")).toBe(
      "/admin/users",
    );
  });
});
