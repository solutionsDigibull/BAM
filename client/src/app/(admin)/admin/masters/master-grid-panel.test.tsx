import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { STALE_MESSAGE } from "@/components/ui-kit";
import type { MasterEntry } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { MasterGridPanel } from "./master-grid-panel";
import { entry, info } from "./test-fixtures";
import { ToolFactory } from "./tool-factory";

// jsdom can't lay out AG Grid's virtual rows; the rendered grid is covered by e2e/admin-masters-grids.spec.ts.
// The stub hands the panel's real actions to plain buttons.
vi.mock("next/dynamic", () => ({
  default: () =>
    function GridStub({
      rows,
      actions,
    }: {
      rows: MasterEntry[];
      actions: {
        onEdit: (e: MasterEntry, f: string, v: string) => void;
        onRetire: (e: MasterEntry) => void;
        onDuplicate?: (e: MasterEntry) => void;
      };
    }) {
      return (
        <ul>
          {rows.map((r) => (
            <li key={r.id}>
              <span>{r.stable_id}</span>
              <button onClick={() => actions.onEdit(r, "skill", "Renamed")}>
                edit {r.stable_id}
              </button>
              <button onClick={() => actions.onRetire(r)}>retire {r.stable_id}</button>
              {actions.onDuplicate && (
                <button onClick={() => actions.onDuplicate?.(r)}>copy {r.stable_id}</button>
              )}
            </li>
          ))}
        </ul>
      );
    },
}));

configure({ asyncUtilTimeout: 4_000 });
afterEach(() => vi.unstubAllGlobals());

const SKILLS = info("SKILLS", "Skills", [
  ["Skill ID", "skill_id", "Stable id"],
  ["Skill", "skill", "Name"],
]);
const S1 = entry({
  master_type: "SKILLS",
  stable_id: "SK-01",
  row_label: "Skills #1",
  payload: { skill_id: "SK-01", skill: "Intake" },
});
const LIST = "GET /admin/masters/SKILLS";
const ONE = `GET /admin/masters/SKILLS/${S1.id}`;
const VERSIONS = `POST /admin/masters/SKILLS/${S1.id}/versions`;
const RETIRE = `POST /admin/masters/SKILLS/${S1.id}/retire`;
const json = (init: RequestInit) => JSON.parse(String(init.body)) as Record<string, unknown>;
const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name);

describe("MUI-4 masters grid panel", () => {
  test("mui4_t04 empty (with Add), error with the problem detail, and forbidden", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    const first = renderWithRefine(<MasterGridPanel info={SKILLS} />);
    expect(await screen.findByText(/No Skills entries yet/)).toBeDefined();
    expect(screen.getAllByRole("button", { name: /Add Row/ }).length).toBeGreaterThan(0);
    first.unmount();

    fakeApi({ [LIST]: problem(500, "Boom", "The database is down") });
    const second = renderWithRefine(<MasterGridPanel info={SKILLS} />);
    expect(await screen.findByText(/The database is down/)).toBeDefined();
    second.unmount();

    fakeApi({ [LIST]: problem(403, "Forbidden", "Admins only") });
    renderWithRefine(<MasterGridPanel info={SKILLS} />);
    expect(await screen.findByText(/Forbidden/)).toBeDefined();
  });

  test("mui4_t05 a cell edit reads the row, then posts a new version with If-Match", async () => {
    const { calls } = fakeApi({
      [LIST]: { body: { items: [S1] } },
      [ONE]: { body: S1, etag: '"4"' },
      [VERSIONS]: () => ({
        status: 201,
        body: { ...S1, version: 2, payload: { ...S1.payload, skill: "Renamed" } },
      }),
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} />);
    fireEvent.click(await screen.findByText("edit SK-01"));
    expect(await screen.findByText("Saved SK-01 (version 2)")).toBeDefined();
    const post = calls.find((c) => c.method === "POST")!;
    expect(header(post.init, "if-match")).toBe('"4"');
    expect(json(post.init).payload).toEqual({ skill_id: "SK-01", skill: "Renamed" });
  });

  test("mui4_t06 a row that moved on is stale: warning, no write, list reloaded", async () => {
    const { calls } = fakeApi({
      [LIST]: { body: { items: [S1] } },
      [ONE]: { body: { ...S1, version: 3 }, etag: '"9"' },
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} />);
    fireEvent.click(await screen.findByText("edit SK-01"));
    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    const lists = () =>
      calls.filter((c) => c.method === "GET" && c.path.startsWith("/admin/masters/SKILLS?"));
    await waitFor(() => expect(lists().length).toBeGreaterThan(1));
  });

  test("mui4_t07 a 412 from the server shows the stale message", async () => {
    fakeApi({
      [LIST]: { body: { items: [S1] } },
      [ONE]: { body: S1, etag: '"4"' },
      [VERSIONS]: problem(412, "Precondition Failed", "stale"),
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} />);
    fireEvent.click(await screen.findByText("edit SK-01"));
    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
  });

  test("mui4_t08 retire posts to the retire route with If-Match", async () => {
    const { calls } = fakeApi({
      [LIST]: { body: { items: [S1] } },
      [ONE]: { body: S1, etag: '"4"' },
      [RETIRE]: { body: { ...S1, status: "RETIRED" } },
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} />);
    fireEvent.click(await screen.findByText("retire SK-01"));
    expect(await screen.findByText("Retired SK-01")).toBeDefined();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toContain(`/SKILLS/${S1.id}/retire`);
    expect(header(post.init, "if-match")).toBe('"4"');
  });

  test("mui4_t09 Add N blank rows is one bulk upsert with blank ids (server assigns them)", async () => {
    const { calls } = fakeApi({
      [LIST]: { body: { items: [S1] } },
      "POST /admin/masters/SKILLS/bulk": { body: { applied: true, created: ["SK-02", "SK-03"] } },
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} projectId="p1" />);
    const n = await screen.findByLabelText("Number of rows to add");
    fireEvent.change(n, { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Add 2 blank rows/ }));
    expect(await screen.findByText("Added 2 blank rows")).toBeDefined();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toContain("bulk?project_id=p1");
    expect(json(post.init)).toEqual({ mode: "upsert", rows: [{ skill_id: "" }, { skill_id: "" }] });
  });

  test("mui4_t10 duplicate copies the row with a blank id so the server assigns the next", async () => {
    const { calls } = fakeApi({
      [LIST]: { body: { items: [S1] } },
      "POST /admin/masters/SKILLS": { status: 201, body: { ...S1, stable_id: "SK-02" } },
    });
    renderWithRefine(<MasterGridPanel info={SKILLS} duplicate />);
    fireEvent.click(await screen.findByText("copy SK-01"));
    expect(await screen.findByText("Duplicated SK-01")).toBeDefined();
    const post = calls.find((c) => c.method === "POST")!;
    expect(json(post.init).payload).toEqual({ skill_id: "", skill: "Intake (copy)" });
  });

  test("mui4_t11 Tool Factory: Tools, Agents and Assistants views and category toggles", async () => {
    const TOOLS = info("TOOLING", "Tooling", [
      ["Tooling ID", "tooling_id", ""],
      ["Category", "category", ""],
    ]);
    const AGENTS = info("AGENTS", "Agents", [
      ["Agent ID", "agent_id", ""],
      ["Kind", "kind", ""],
    ]);
    const t = (id: string, category: string) =>
      entry({
        id: `t-${id}`,
        master_type: "TOOLING",
        stable_id: id,
        payload: { tooling_id: id, category },
      });
    const a = (id: string, kind: string) =>
      entry({
        id: `a-${id}`,
        master_type: "AGENTS",
        stable_id: id,
        payload: { agent_id: id, kind },
      });
    fakeApi({
      "GET /admin/masters/TOOLING": { body: { items: [t("T-1", "API"), t("T-2", "MCP")] } },
      "GET /admin/masters/AGENTS": {
        body: { items: [a("AG-01", "Agent"), a("AS-01", "Assistant")] },
      },
    });
    renderWithRefine(<ToolFactory tools={TOOLS} agents={AGENTS} />);
    expect(await screen.findByText("T-1")).toBeDefined();
    expect(screen.getByText("T-2")).toBeDefined();
    fireEvent.click(await screen.findByText("MCP")); // the category toggle
    await waitFor(() => expect(screen.queryByText("T-1")).toBeNull());
    expect(screen.getByText("T-2")).toBeDefined();
    fireEvent.click(screen.getByText("Assistants"));
    expect(await screen.findByText("AS-01")).toBeDefined();
    expect(screen.queryByText("AG-01")).toBeNull();
    fireEvent.click(screen.getByText("Agents"));
    expect(await screen.findByText("AG-01")).toBeDefined();
    expect(within(document.body).queryByText("AS-01")).toBeNull();
  });

  test("mui4_t12 the add drawer prefills the Assistant kind and a 0.3 Status column is saved as its word", async () => {
    const AGENTS = info("AGENTS", "Agents", [
      ["Agent ID", "agent_id", ""],
      ["Kind", "kind", ""],
    ]);
    const TOOLS = info("TOOLING", "Tooling", [
      ["Tooling ID", "tooling_id", ""],
      ["Status", "record_status", ""],
    ]);
    const a = entry({ master_type: "AGENTS", stable_id: "AG-01", payload: { agent_id: "AG-01" } });
    fakeApi({
      "GET /admin/masters/AGENTS": { body: { items: [a] } },
      "GET /admin/masters/TOOLING": { body: { items: [a] } },
      "POST /admin/masters/AGENTS": { status: 201, body: a },
      "POST /admin/masters/TOOLING": { status: 201, body: a },
    });
    const first = renderWithRefine(
      <MasterGridPanel info={AGENTS} defaults={{ kind: "Assistant" }} />,
    );
    fireEvent.click((await screen.findAllByRole("button", { name: /Add Row/ }))[0]!);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByLabelText("Kind")).toHaveProperty("value", "Assistant");
    expect(within(drawer).queryByRole("combobox", { name: "Status" })).toBeNull(); // no Status column
    first.unmount();

    renderWithRefine(<MasterGridPanel info={TOOLS} />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Add Row/ }))[0]!);
    const second = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(second).getByRole("combobox", { name: "Status" }));
    fireEvent.click((await screen.findAllByText("APPROVED")).at(-1)!);
    expect(within(second).getByLabelText("Status", { selector: "textarea" })).toHaveProperty(
      "value",
      "Approved",
    );
  });
});
