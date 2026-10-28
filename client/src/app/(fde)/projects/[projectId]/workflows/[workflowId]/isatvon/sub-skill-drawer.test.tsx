import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import type { WorkbenchSubSkill } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";

import type { GateEditorProps } from "./gate-editor";
import { KEPT_EDIT, SubSkillDrawer } from "./sub-skill-drawer";

// CodeMirror needs a real layout engine: here the editor is a textarea with the same props; the
// real one is covered by e2e/isatvon-workbench.spec.ts (E8.8-T05).
vi.mock("next/dynamic", () => ({
  default: () =>
    function EditorStub({ value, label, readOnly, onChange }: GateEditorProps) {
      return (
        <textarea
          aria-label={label}
          readOnly={readOnly}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    },
}));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = `/projects/${P}/workflows/${W}/sub-skills/s1`;

const definition = {
  i_instructions: { statement: "Triggered by login. Action: verify. Check: ok. Bound: 3." },
  s_source: { required_inputs: ["otp_payload"], fresh_state_parameters: ["user_status"] },
  a_automation: { orchestration_layer: "Dify" },
  t_tech_stack: { action_tool: "Auth_MCP", fixed_check_tool: "RBAC_MCP" },
  v_variables: {
    max_iterations: 3,
    success_gate: "otp_match == True",
    no_op_condition: "user_status is 'LOCKED'",
    escalation_condition: "attempts >= max_iterations",
  },
  o_outcome: { json_schema: { otp_match: "boolean", attempts: "integer" } },
  n_notification: { next: null },
};

const row = (over: Partial<WorkbenchSubSkill> = {}): WorkbenchSubSkill =>
  ({
    id: "s1",
    macro_id: "m1",
    state: "VALID",
    content_sha256: "a".repeat(64),
    validation_errors: [],
    sow_refs: ["F-ACCESS-1", "M-ACCESS"],
    sub_skill: {
      skill_metadata: { macro_category: "MS01_X", skill_id: "MS01.1_Auth", skill_name: "Auth" },
      isatvon_definition: definition,
    },
    ...over,
  }) as unknown as WorkbenchSubSkill;

function open(props: Partial<Parameters<typeof SubSkillDrawer>[0]> = {}) {
  const handlers = {
    onSaved: vi.fn(),
    onStale: vi.fn(async () => undefined),
    onLocked: vi.fn(),
    onClose: vi.fn(),
  };
  const view = renderWithRefine(
    <SubSkillDrawer
      projectId={P}
      workflowId={W}
      row={row()}
      editable
      cycle={null}
      {...handlers}
      {...props}
    />,
  );
  return { ...handlers, ...view };
}

describe("Sub-Skill drawer (E8.8)", () => {
  test("E8.8-T06 shows every ISATVON block, the citations and the history", async () => {
    fakeApi({
      [`GET ${SUB}/edits`]: {
        body: {
          sub_skill_id: "s1",
          edits: [
            {
              id: "e1",
              path: "isatvon_definition.v_variables.budget_limit",
              old: "5000ms",
              new: "6000ms",
              by: "fde-1",
              at: "2026-10-28T10:00:00Z",
            },
          ],
        },
      },
    });
    open();
    for (const letter of ["I", "S", "A", "T", "V", "O", "N"])
      expect(screen.getByTestId(`block-${letter}`)).toBeDefined();
    expect(screen.getByText(/Triggered by login/)).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Citations (2)" }));
    const citations = await screen.findByTestId("drawer-citations");
    const link = within(citations).getByRole("link", { name: "F-ACCESS-1" });
    expect(link.getAttribute("href")).toBe(`/projects/${P}/sow#F-ACCESS-1`);
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    const history = await screen.findByTestId("drawer-history");
    expect(history.textContent).toContain("budget_limit");
    expect(history.textContent).toContain("fde-1");
  });

  test("the server's validation errors and a dependency cycle are shown", () => {
    open({
      row: row({
        state: "INVALID",
        validation_errors: [
          {
            rule: 5,
            path: "isatvon_definition.v_variables.success_gate",
            message: "unresolved identifier 'foo'",
            code: "UNRESOLVED_IDENTIFIER",
          },
        ],
      }),
      cycle: "Dependency cycle: MS01.2 → MS01.3 → MS01.2",
    });
    expect(screen.getByTestId("drawer-errors").textContent).toContain("UNRESOLVED_IDENTIFIER");
    expect(screen.getByTestId("drawer-cycle").textContent).toContain("MS01.2 → MS01.3");
  });

  test("E8.8-T05 an unknown name is warned inline; a parse error disables Save", () => {
    open();
    fireEvent.click(screen.getByRole("tab", { name: "Gates" }));
    const gate = screen.getByLabelText("success_gate");
    fireEvent.change(gate, { target: { value: "foo == 1" } });
    expect(screen.getByTestId("gate-problems-success_gate").textContent).toBe(
      "Unknown identifier foo",
    );
    const save = screen.getByRole("button", { name: "Save success_gate" });
    expect((save as HTMLButtonElement).disabled).toBe(false); // a warning: the server decides
    fireEvent.change(gate, { target: { value: "status == " } });
    expect(screen.getByTestId("gate-problems-success_gate").textContent).toContain(
      "SYNTAX: unexpected 'end of expression' at 10",
    );
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(gate, { target: { value: "__import__('os').system('x')" } });
    expect(screen.getByTestId("gate-problems-success_gate").textContent).toContain(
      "GRAMMAR_FORBIDDEN",
    );
  });

  test("a gate saves through the same PATCH with If-Match", async () => {
    const api = fakeApi({ [`PATCH ${SUB}`]: { body: row() } });
    const { onSaved } = open();
    fireEvent.click(screen.getByRole("tab", { name: "Gates" }));
    fireEvent.change(screen.getByLabelText("success_gate"), {
      target: { value: "otp_match == False" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save success_gate" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const call = api.calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(String(call.init.body))).toEqual({
      changes: { "isatvon_definition.v_variables.success_gate": "otp_match == False" },
    });
    expect(new Headers(call.init.headers).get("if-match")).toBe(`"${"a".repeat(64)}"`);
  });

  test("E8.8-T08 a 412 reloads and keeps the user's edit beside the latest value", async () => {
    fakeApi({ [`PATCH ${SUB}`]: problem(412, "PRECONDITION_FAILED", "Resource changed") });
    const { onStale, onSaved } = open();
    fireEvent.click(screen.getByRole("tab", { name: "Gates" }));
    fireEvent.change(screen.getByLabelText("success_gate"), {
      target: { value: "otp_match == False" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save success_gate" }));
    const kept = await screen.findByTestId("drawer-conflict");
    expect(kept.textContent).toContain(KEPT_EDIT);
    expect(screen.getByTestId("conflict-mine").textContent).toBe("otp_match == False");
    expect(screen.getByTestId("conflict-theirs").textContent).toBe("otp_match == True");
    expect(onStale).toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  test("a block is edited as JSON; invalid JSON never reaches the server", async () => {
    const api = fakeApi({ [`PATCH ${SUB}`]: { body: row() } });
    open();
    const block = screen.getByTestId("block-A");
    fireEvent.click(within(block).getByRole("button", { name: "Edit" }));
    const text = within(block).getByLabelText("A — Automation JSON");
    fireEvent.change(text, { target: { value: "{oops" } });
    fireEvent.click(within(block).getByRole("button", { name: "Save" }));
    expect(within(block).getByText("Not valid JSON")).toBeDefined();
    expect(api.calls.filter((c) => c.method === "PATCH")).toEqual([]);
    fireEvent.change(text, { target: { value: '{"orchestration_layer": "Dify2"}' } });
    fireEvent.click(within(block).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
  });

  test("read-only: no Edit or Save buttons", () => {
    open({ editable: false });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Gates" }));
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  });
});
