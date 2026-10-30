import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { SrimDefaultsView, SrimRuntimeDefaults } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";

import { DEFAULTS_MESSAGE, STALE_MESSAGE } from "./srim-api";
import { SrimDefaultsScreen } from "./srim-defaults-screen";

const ISA14: SrimRuntimeDefaults = {
  orchestration: {
    router: "Dify_Dynamic_Master_Router",
    trigger: "HTTP_POST_Payload_plus_Variables",
  },
  execution: { engine: "FastMCP_Execution_Engine" },
  gateway: { route_via: "MCP_Jungle_Gateway_Reverse_Proxy" },
  persistence: {
    database: "PostgreSQL_Database_State_Config",
    action: "Execute_SQL_Queries_via_Postgres_MCP",
  },
  audit: { telemetry: "PostgreSQL_Audit_Logs" },
  ui: { target: "SRIM_UI_Canvas", method: "Embedded_iFrames_Widgets" },
};
const ACME: SrimRuntimeDefaults = { ...ISA14, gateway: { route_via: "Acme_Gateway" } };

const view = (defaults: SrimRuntimeDefaults, is_default: boolean): SrimDefaultsView => ({
  defaults,
  is_default,
});
const GET = "GET /admin/srim-defaults";
const PUT = "PUT /admin/srim-defaults";
const DELETE = "DELETE /admin/srim-defaults";

afterEach(() => vi.unstubAllGlobals());

const field = (label: string) => screen.findByLabelText(label) as Promise<HTMLInputElement>;

// antd forms in jsdom are slow on a busy machine.
describe("SRIM runtime defaults screen (E9.1b)", { timeout: 20_000 }, () => {
  test("e9_1b_ui_t01 a tenant with no values sees the ISA14 ones and the banner; reset is off", async () => {
    fakeApi({ [GET]: { body: view(ISA14, true), etag: '"0"' } });
    renderWithRefine(<SrimDefaultsScreen />);
    expect(await screen.findByText(DEFAULTS_MESSAGE)).toBeDefined();
    expect((await field("Route via")).value).toBe("MCP_Jungle_Gateway_Reverse_Proxy");
    expect(
      (screen.getByRole("button", { name: "Reset to ISA14" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  test("e9_1b_ui_t02 save sends all six blocks with If-Match from the read, then shows the saved values", async () => {
    const api = fakeApi({
      [GET]: [
        { body: view(ISA14, true), etag: '"0"' },
        { body: view(ACME, false), etag: '"1"' },
      ],
      [PUT]: { body: view(ACME, false), etag: '"1"' },
    });
    renderWithRefine(<SrimDefaultsScreen />);
    fireEvent.change(await field("Route via"), { target: { value: "Acme_Gateway" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
    const put = api.calls.find((c) => c.method === "PUT")!;
    expect(new Headers(put.init.headers).get("if-match")).toBe('"0"');
    expect(JSON.parse(String(put.init.body))).toEqual(ACME);
    await waitFor(() => expect(screen.queryByText(DEFAULTS_MESSAGE)).toBeNull());
    expect((await field("Route via")).value).toBe("Acme_Gateway");
  });

  test("e9_1b_ui_t03 an empty field is flagged and nothing is sent", async () => {
    const api = fakeApi({ [GET]: { body: view(ISA14, true), etag: '"0"' } });
    renderWithRefine(<SrimDefaultsScreen />);
    fireEvent.change(await field("Engine"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Engine is required")).toBeDefined();
    expect(api.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  test("e9_1b_ui_t04 412 says someone else changed it and offers Reload", async () => {
    fakeApi({
      [GET]: { body: view(ISA14, true), etag: '"0"' },
      [PUT]: problem(412, "PRECONDITION_FAILED"),
    });
    renderWithRefine(<SrimDefaultsScreen />);
    fireEvent.change(await field("Telemetry"), { target: { value: "Other" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
  });

  test("e9_1b_ui_t05 a 422 from the API lands on the field it names", async () => {
    fakeApi({
      [GET]: { body: view(ISA14, true), etag: '"0"' },
      [PUT]: {
        status: 422,
        body: {
          type: "about:blank",
          title: "Validation error",
          status: 422,
          code: "VALIDATION_ERROR",
          errors: [{ path: "ui.method", message: "must not be empty" }],
        },
      },
    });
    renderWithRefine(<SrimDefaultsScreen />);
    fireEvent.change(await field("UI method"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("must not be empty")).toBeDefined();
  });

  test("e9_1b_ui_t06 reset asks first, then deletes with the current ETag and shows the ISA14 values", async () => {
    const api = fakeApi({
      [GET]: [
        { body: view(ACME, false), etag: '"1"' },
        { body: view(ISA14, true), etag: '"0"' },
      ],
      [DELETE]: { body: view(ISA14, true), etag: '"0"' },
    });
    renderWithRefine(<SrimDefaultsScreen />);
    expect((await field("Route via")).value).toBe("Acme_Gateway");
    fireEvent.click(screen.getByRole("button", { name: "Reset to ISA14" }));
    expect(api.calls.filter((c) => c.method === "DELETE")).toHaveLength(0); // not yet: it asks
    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(
      new Headers(api.calls.find((c) => c.method === "DELETE")!.init.headers).get("if-match"),
    ).toBe('"1"');
    expect(await screen.findByText(DEFAULTS_MESSAGE)).toBeDefined();
  });

  test("e9_1b_ui_t07 a forbidden or failed read shows the error state, not a form", async () => {
    fakeApi({ [GET]: problem(403, "WRONG_PORTAL") });
    renderWithRefine(<SrimDefaultsScreen />);
    expect(await screen.findByText(/not allowed|forbidden|permission/i)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
