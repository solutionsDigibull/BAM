import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { MasterEntry } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { RulesEngine } from "./rules-engine";
import { entry, info } from "./test-fixtures";

// jsdom can't lay out AG Grid: the stub renders the rows with an Edit button (the real grid is covered by
// e2e/admin-masters-rules.spec.ts).
vi.mock("next/dynamic", () => ({
  default: () =>
    function GridStub({
      rows,
      actions,
    }: {
      rows: MasterEntry[];
      actions: { onOpen: (e: MasterEntry) => void; onRetire: (e: MasterEntry) => void };
    }) {
      return (
        <ul>
          {rows.map((r) => (
            <li key={r.id}>
              <span>{r.stable_id}</span>
              <button onClick={() => actions.onOpen(r)}>open {r.stable_id}</button>
              <button onClick={() => actions.onRetire(r)}>retire {r.stable_id}</button>
            </li>
          ))}
        </ul>
      );
    },
}));

configure({ asyncUtilTimeout: 8_000 });
vi.setConfig({ testTimeout: 30_000 });
afterEach(() => vi.unstubAllGlobals());

const RULES = info("RULES", "Rules", [
  ["Rule ID", "rule_id", "Stable id"],
  ["Rule / Type / Description", "rule_type_description", "Name and type"],
  ["Status", "record_status", "Status"],
  ["Skill ref", "skill_ref", "Skill"],
]);
const R1 = entry({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000f001",
  stable_id: "RULE-1",
  row_label: "Rule #1",
  payload: {
    rule_id: "RULE-1",
    rule_type_description: "BOM scrub. Policy: compare",
    record_status: "Proposed",
    skill_ref: "SK-01",
    prompts: { system: "Be exact", micro: [{ name: "m1", text: "check" }] },
  },
});
const R2 = entry({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000f002",
  stable_id: "RULE-2",
  row_label: "Rule #2",
  payload: {
    rule_id: "RULE-2",
    rule_type_description: "Price. Estimation: margin",
    record_status: "Approved",
    skill_ref: "SK-02",
  },
});
const sk = (id: string, name: string, next: string) =>
  entry({
    master_type: "SKILLS",
    stable_id: id,
    id: `0190f3a2-7b1c-7d4e-8f00-0000000000${id.slice(-2)}`,
    payload: { skill_id: id, skill: name, next },
  });
const SKILLS = [sk("SK-01", "Intake", "SK-02"), sk("SK-02", "Quote", "")];
const LIST = "GET /admin/masters/RULES";
const ONE = `GET /admin/masters/RULES/${R1.id}`;
const VERSIONS = `POST /admin/masters/RULES/${R1.id}/versions`;
const RETIRE = `POST /admin/masters/RULES/${R1.id}/retire`;
const base = {
  [LIST]: { body: { items: [R1, R2] } },
  "GET /admin/masters/SKILLS": { body: { items: SKILLS } },
};
const header = (init: RequestInit, n: string) => new Headers(init.headers).get(n);

describe("MUI-5 Rules Engine", () => {
  test("mui5_t06 the map shows each skill with its rule count and filters the rules", async () => {
    fakeApi(base);
    renderWithRefine(<RulesEngine info={RULES} />);
    const map = await screen.findByRole("group", { name: "Workflow map" });
    expect(await within(map).findByText(/1\. SK-01 Intake · 1 rule/)).toBeDefined();
    expect(within(map).getByText(/2\. SK-02 Quote · 1 rule/)).toBeDefined();
    fireEvent.click(within(map).getByText(/SK-02 Quote/));
    await waitFor(() => expect(screen.queryByText("RULE-1")).toBeNull());
    expect(screen.getByText("RULE-2")).toBeDefined();
    expect(screen.getByText("1 of 1 rows")).toBeDefined();
  });

  test("mui5_t07 empty, error with the problem detail, and forbidden", async () => {
    fakeApi({ ...base, [LIST]: { body: { items: [] } } });
    const a = renderWithRefine(<RulesEngine info={RULES} />);
    expect(await screen.findByText(/No Rules entries yet/)).toBeDefined();
    a.unmount();
    fakeApi({ ...base, [LIST]: problem(500, "Boom", "The database is down") });
    const b = renderWithRefine(<RulesEngine info={RULES} />);
    expect(await screen.findByText(/The database is down/)).toBeDefined();
    b.unmount();
    fakeApi({ ...base, [LIST]: problem(403, "Forbidden", "Admins only") });
    renderWithRefine(<RulesEngine info={RULES} />);
    expect(await screen.findByText(/Admins only|Forbidden/)).toBeDefined();
  });

  test("mui5_t08 a skills failure shows a warning and the rules still load", async () => {
    fakeApi({ ...base, "GET /admin/masters/SKILLS": problem(500, "Boom", "Skills are down") });
    renderWithRefine(<RulesEngine info={RULES} />);
    expect(await screen.findByText("Skills are down")).toBeDefined();
    expect(await screen.findByText("RULE-1")).toBeDefined();
  });

  test("mui5_t09 the editor saves the prompts as a new version with If-Match", async () => {
    const { calls } = fakeApi({
      ...base,
      [ONE]: { body: R1, etag: '"4"' },
      [VERSIONS]: (init) => ({
        status: 201,
        body: {
          ...R1,
          version: 2,
          payload: (JSON.parse(String(init.body)) as never as { payload: object }).payload,
        },
      }),
    });
    renderWithRefine(<RulesEngine info={RULES} />);
    fireEvent.click(await screen.findByRole("button", { name: "open RULE-1" }));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByDisplayValue("Be exact")).toBeDefined();
    fireEvent.change(within(drawer).getByLabelText("System prompt"), {
      target: { value: "Be very exact" },
    });
    fireEvent.change(within(drawer).getByLabelText("Micro prompt 1 text"), {
      target: { value: "check twice" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /Add micro prompt/, hidden: true }));
    fireEvent.click(within(drawer).getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/admin/masters/RULES/${R1.id}/versions`);
    expect(header(post.init, "if-match")).toBe('"4"');
    const { payload } = JSON.parse(String(post.init.body)) as {
      payload: { prompts: unknown; rule_id: string };
    };
    expect(payload.rule_id).toBe("RULE-1");
    expect(payload.prompts).toEqual({
      system: "Be very exact",
      micro: [{ name: "m1", text: "check twice" }], // the blank added one is dropped
    });
    expect(await screen.findByText("Saved Rule #1 (version 2)")).toBeDefined();
  });

  test("mui5_t10 a stale save asks to reload and never writes over the newer version", async () => {
    fakeApi({
      ...base,
      [ONE]: { body: R1, etag: '"4"' },
      [VERSIONS]: problem(412, "PRECONDITION_FAILED"),
    });
    renderWithRefine(<RulesEngine info={RULES} />);
    fireEvent.click(await screen.findByRole("button", { name: "open RULE-1" }));
    const drawer = await screen.findByRole("dialog");
    await within(drawer).findByDisplayValue("Be exact");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save new version" }));
    expect(await screen.findByRole("button", { name: "Reload" })).toBeDefined();
  });

  test("mui5_t11 retire goes through the retire route with If-Match", async () => {
    const { calls } = fakeApi({
      ...base,
      [ONE]: { body: R1, etag: '"4"' },
      [RETIRE]: { body: { ...R1, status: "RETIRED" } },
    });
    renderWithRefine(<RulesEngine info={RULES} />);
    fireEvent.click(await screen.findByRole("button", { name: "retire RULE-1" }));
    expect(await screen.findByText("Retired RULE-1")).toBeDefined();
    const post = calls.find((c) => c.path === `/admin/masters/RULES/${R1.id}/retire`)!;
    expect(header(post.init, "if-match")).toBe('"4"');
  });
});
