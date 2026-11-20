import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BudgetCaps, ProjectTokenCaps } from "@/contracts";

import { fakeApi, problem, project, renderWithRefine } from "../projects/test-harness";

import { DEFAULTS_MESSAGE, STALE_MESSAGE } from "./token-caps-api";
import { TokenCapsScreen } from "./token-caps-screen";

const P1 = project({ id: "0190f3a2-7b1c-7d4e-8f00-000000000001", name: "EMS RFQ costing" });
const P2 = project({ id: "0190f3a2-7b1c-7d4e-8f00-000000000002", name: "Quote bot" });
const DEFAULT: BudgetCaps = {
  sub_skill_tokens: 200_000,
  workflow_tokens: 2_000_000,
  knowledge_tokens: 30_000_000,
  first_alert_pct: 50,
  second_alert_pct: 80,
};
const view = (caps: BudgetCaps, version: number): ProjectTokenCaps => ({
  caps,
  row_version: version,
  is_default: version === 0,
});
const LIST = "GET /admin/projects";
const caps = (p: typeof P1) => `/admin/projects/${p.id}/token-caps`;

afterEach(() => vi.unstubAllGlobals());

const field = (label: RegExp) => screen.findByLabelText(label) as Promise<HTMLInputElement>;

// antd forms in jsdom are slow on a busy machine.
describe("Token caps screen (#776)", { timeout: 20_000 }, () => {
  test("test_wg23_ui_t01_a_project_with_no_limits_shows_the_defaults_and_the_banner", async () => {
    fakeApi({
      [LIST]: { body: { items: [P1, P2], next_cursor: null } },
      [`GET ${caps(P1)}`]: { body: view(DEFAULT, 0), etag: '"0"' },
    });
    renderWithRefine(<TokenCapsScreen />);
    expect(await screen.findByText(DEFAULTS_MESSAGE)).toBeDefined();
    expect((await field(/Workflow build/)).value).toBe("2000000");
  });

  test("test_wg23_ui_t01_save_sends_the_five_numbers_with_if_match_from_the_read", async () => {
    const saved = { ...DEFAULT, workflow_tokens: 3_000_000 };
    const api = fakeApi({
      [LIST]: { body: { items: [P1], next_cursor: null } },
      [`GET ${caps(P1)}`]: [
        { body: view(DEFAULT, 0), etag: '"0"' },
        { body: view(saved, 1), etag: '"1"' },
      ],
      [`PUT ${caps(P1)}`]: { body: view(saved, 1), etag: '"1"' },
    });
    renderWithRefine(<TokenCapsScreen />);
    const workflow = await field(/Workflow build/);
    fireEvent.change(workflow, { target: { value: "3000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
    const put = api.calls.find((c) => c.method === "PUT")!;
    expect(new Headers(put.init.headers).get("if-match")).toBe('"0"');
    expect(JSON.parse(String(put.init.body))).toEqual(saved);
    await waitFor(() => expect(screen.queryByText(DEFAULTS_MESSAGE)).toBeNull());
  });

  test("test_wg23_ui_t01_a_stale_write_offers_a_reload", async () => {
    fakeApi({
      [LIST]: { body: { items: [P1], next_cursor: null } },
      [`GET ${caps(P1)}`]: { body: view(DEFAULT, 1), etag: '"1"' },
      [`PUT ${caps(P1)}`]: problem(
        412,
        "PRECONDITION_FAILED",
        "Resource changed since you loaded it",
      ),
    });
    renderWithRefine(<TokenCapsScreen />);
    await field(/Workflow build/);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
  });

  test("test_wg23_ui_the_api_refusal_shows_its_detail", async () => {
    fakeApi({
      [LIST]: { body: { items: [P1], next_cursor: null } },
      [`GET ${caps(P1)}`]: { body: view(DEFAULT, 1), etag: '"1"' },
      [`PUT ${caps(P1)}`]: problem(500, "INTERNAL_ERROR", "The database is down"),
    });
    renderWithRefine(<TokenCapsScreen />);
    await field(/Workflow build/);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/The database is down/)).toBeDefined();
  });

  test("test_wg23_ui_choosing_another_project_reads_its_own_limits", async () => {
    const other = { ...DEFAULT, sub_skill_tokens: 1000 };
    const api = fakeApi({
      [LIST]: { body: { items: [P1, P2], next_cursor: null } },
      [`GET ${caps(P1)}`]: { body: view(DEFAULT, 0), etag: '"0"' },
      [`GET ${caps(P2)}`]: { body: view(other, 2), etag: '"2"' },
    });
    renderWithRefine(<TokenCapsScreen />);
    await field(/Workflow build/);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Project" }));
    fireEvent.click(await screen.findByTitle("Quote bot"));
    await waitFor(() => expect(api.calls.some((c) => c.path.startsWith(caps(P2)))).toBe(true));
    await waitFor(async () => expect((await field(/One Sub-Skill/)).value).toBe("1000"));
  });
});

describe("Token caps screen states (#776)", { timeout: 20_000 }, () => {
  test("test_wg23_ui_loading_state", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );
    const { container } = renderWithRefine(<TokenCapsScreen />);
    expect(container.querySelector(".ant-skeleton")).not.toBeNull();
  });

  test("test_wg23_ui_empty_state_no_projects", async () => {
    fakeApi({ [LIST]: { body: { items: [], next_cursor: null } } });
    renderWithRefine(<TokenCapsScreen />);
    expect(await screen.findByText(/No projects yet/)).toBeDefined();
  });

  test("test_wg23_ui_error_state_shows_the_problem_detail", async () => {
    fakeApi({ [LIST]: problem(500, "INTERNAL_ERROR", "The projects could not be read") });
    renderWithRefine(<TokenCapsScreen />);
    expect(await screen.findByText(/The projects could not be read/)).toBeDefined();
  });

  test("test_wg23_ui_forbidden_state", async () => {
    fakeApi({ [LIST]: problem(403, "WRONG_PORTAL", "Wrong portal") });
    renderWithRefine(<TokenCapsScreen />);
    expect(await screen.findByText("Forbidden")).toBeDefined();
  });

  test("test_wg23_ui_the_caps_of_a_project_that_is_not_found_shows_not_found", async () => {
    fakeApi({
      [LIST]: { body: { items: [P1], next_cursor: null } },
      [`GET ${caps(P1)}`]: problem(404, "NOT_FOUND", "Not found"),
    });
    renderWithRefine(<TokenCapsScreen />);
    expect((await screen.findAllByText("Not found")).length).toBeGreaterThan(0);
  });
});
