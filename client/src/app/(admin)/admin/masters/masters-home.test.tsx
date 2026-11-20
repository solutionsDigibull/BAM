import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { fakeApi, problem, project, renderWithRefine } from "../projects/test-harness";
import { MastersHome } from "./masters-home";
import { MastersScreen } from "./masters-screen";
import { entry, filetype, LAYOUT } from "./test-fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const A = project({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000000a",
  name: "Alpha RFQ",
  updated_at: "2026-10-01T10:00:00Z",
});
const B = project({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000000b",
  name: "Beta costing",
  client_name: "Zeta Corp",
  updated_at: "2026-10-05T10:00:00Z",
});
const OLD = project({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000000c",
  name: "Old one",
  status: "ARCHIVED",
});

const names = () => screen.getAllByRole("listitem").map((li) => li.querySelector("a")!.textContent);

describe("Admin Masters project list (#869)", () => {
  test("test_mui_8_t03_rows_newest_first_search_archived_filter_and_the_tenant_card", async () => {
    fakeApi({ "GET /admin/projects": { body: { items: [A, OLD, B] } } });
    renderWithRefine(<MastersHome />);
    await screen.findByText("Alpha RFQ");
    expect(names()).toEqual(["Beta costing", "Alpha RFQ"]); // newest first, archived hidden
    expect(screen.getByRole("link", { name: /Tenant masters/ }).getAttribute("href")).toBe(
      "/admin/masters/tenant",
    );
    expect(screen.getByRole("link", { name: "Alpha RFQ" }).getAttribute("href")).toBe(
      `/admin/masters/projects/${A.id}`,
    );
    expect(screen.getByText(/Zeta Corp · Updated/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "zeta" } });
    expect(names()).toEqual(["Beta costing"]); // matches the client too
    fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "nothing" } });
    expect(screen.getByText("No projects match your search.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "" } });

    fireEvent.click(screen.getByLabelText("Show archived"));
    expect(names()).toContain("Old one");
    expect(screen.getByText("Archived")).toBeTruthy();
  });

  test("test_mui_8_t03_loading_empty_error_and_forbidden_states", async () => {
    fakeApi({ "GET /admin/projects": { body: { items: [] } } });
    const empty = renderWithRefine(<MastersHome />);
    expect(empty.container.querySelector(".ant-skeleton")).toBeTruthy(); // loading
    await screen.findByText(/No projects yet/);
    expect(screen.getByRole("link", { name: "Go to Admin: Projects" }).getAttribute("href")).toBe(
      "/admin/projects",
    );
    cleanup();

    fakeApi({ "GET /admin/projects": problem(500, "BOOM", "The database is down") });
    renderWithRefine(<MastersHome />);
    expect(await screen.findByText(/The database is down/)).toBeTruthy();
    cleanup();

    fakeApi({ "GET /admin/projects": problem(403, "FORBIDDEN") });
    renderWithRefine(<MastersHome />);
    expect(await screen.findByText("Forbidden")).toBeTruthy();
  });
});

describe("a project's masters page (#869)", () => {
  test("test_mui_8_t04_the_project_id_is_sent_on_the_list", async () => {
    const { calls } = fakeApi({
      "GET /admin/master-types": { body: LAYOUT },
      "GET /admin/masters/ORGANIZATION": { body: { items: [] } },
      "GET /admin/masters/RULES": { body: { items: [entry()] } },
      "GET /admin/filetypes": { body: [filetype(1, 1, false)] },
    });
    renderWithRefine(<MastersScreen projectId={A.id} projectName="Alpha RFQ" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Rules" }));
    await waitFor(() =>
      expect(calls.some((c) => c.path.startsWith("/admin/masters/RULES"))).toBe(true),
    );
    const list = calls.find((c) => c.path.startsWith("/admin/masters/RULES"))!;
    expect(new URL(list.path, "http://x").searchParams.get("project_id")).toBe(A.id);
    expect(screen.getByText("Alpha RFQ: Masters")).toBeTruthy();
  });

  test("test_mui_8_t04_the_tenant_page_sends_no_project_id", async () => {
    const { calls } = fakeApi({
      "GET /admin/master-types": { body: LAYOUT },
      "GET /admin/masters/ORGANIZATION": { body: { items: [] } },
      "GET /admin/masters/RULES": { body: { items: [] } },
      "GET /admin/filetypes": { body: [] },
    });
    renderWithRefine(<MastersScreen />);
    fireEvent.click(await screen.findByRole("tab", { name: "Rules" }));
    await waitFor(() =>
      expect(calls.some((c) => c.path.startsWith("/admin/masters/RULES"))).toBe(true),
    );
    expect(calls.every((c) => !c.path.includes("project_id"))).toBe(true);
  });
});
