import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";

import {
  TAG_LABEL,
  attemptsLink,
  problemGates,
  splitPath,
  tagCounts,
  wholeFile,
} from "./workflow-code-api";
import { EMPTY_WORKFLOW_MESSAGE, WorkflowCodeScreen } from "./workflow-code-screen";

// Monaco needs a real browser: the plain line view stands in for it (same props), as in code.test.tsx.
vi.mock("./monaco-code", async () => ({ default: (await import("./code-lines")).default }));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const S = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const TREE = `/projects/${P}/workflows/${W}/code`;
const REPO = `lib@${"a".repeat(40)}`;

const finding = {
  gate: "AST_SYNTAX",
  code: "AST_FORBIDDEN_CALL",
  file: "action_tool.py",
  line: 3,
  message: "no eval",
};
const file = (path: string, tag: string, findings: object[] = []) => ({
  path,
  sha256: "a".repeat(64),
  tag,
  findings,
});
const subGroup = {
  kind: "sub_skill",
  group_id: S,
  label: "MS01.1_Check",
  build_no: 1,
  cycle: 0,
  attempt: 1,
  status: "ASSEMBLED",
  files: [
    file("action_tool.py", "generated", [finding]),
    file(`vendor/${REPO}/lib.py`, "reused"),
    file(`patches/${REPO}/0001-fix.patch`, "customized"),
  ],
  gates: [
    { gate: "AST_SYNTAX", status: "PASSED" },
    { gate: "AST_DECOUPLING", status: "FAILED" },
  ],
  findings: [],
};
const workflowGroup = {
  kind: "workflow",
  group_id: "workflow",
  label: "Workflow",
  files: [file("manifest.json", "generated")],
  gates: [],
  findings: [],
};
const tree = { workflow_id: W, groups: [subGroup, workflowGroup] };
const view = {
  group_id: S,
  path: "action_tool.py",
  tag: "generated",
  sha256: "a".repeat(64),
  content: "def action(ctx):\n    x = 1\n    eval(x)\n",
  truncated: false,
  findings: [finding],
};

const show = () => renderWithRefine(<WorkflowCodeScreen projectId={P} workflowId={W} />);

describe("whole-Workflow code preview data rules (WG-15)", () => {
  test("wg15_t01 the counts follow the tags and the failed gates are named", () => {
    expect(tagCounts([subGroup, workflowGroup] as never)).toEqual({
      generated: 2,
      reused: 1,
      customized: 1,
    });
    expect(problemGates(subGroup as never)).toEqual(["AST_DECOUPLING"]);
    expect(TAG_LABEL.customized).toBe("Customized");
  });

  test("a Sub-Skill links to its attempts; the Workflow group does not", () => {
    expect(attemptsLink(P, W, subGroup as never)).toBe(
      `/projects/${P}/workflows/${W}/code?sub=${S}`,
    );
    expect(attemptsLink(P, W, workflowGroup as never)).toBeNull();
  });

  test("paths split into a directory and a name; a whole file is one range", () => {
    expect(splitPath(`vendor/${REPO}/lib.py`)).toEqual({ dir: `vendor/${REPO}`, name: "lib.py" });
    expect(splitPath("a.py")).toEqual({ dir: "", name: "a.py" });
    expect(wholeFile("a\nb\n")).toEqual([{ start: 1, end: 2 }]);
    expect(wholeFile("a")).toEqual([{ start: 1, end: 1 }]);
    expect(wholeFile("")).toEqual([{ start: 1, end: 1 }]);
  });
});

describe("whole-Workflow code preview screen (WG-15)", () => {
  test("wg15_t01 every file is listed once with its tag; gates and findings show beside them", async () => {
    fakeApi({
      [`GET ${TREE}`]: { body: tree },
      [`GET ${TREE}/files`]: { body: view },
    });
    const { container } = show();
    expect(container.querySelector(".ant-skeleton")).not.toBeNull(); // loading
    const nav = await screen.findByRole("navigation", { name: "Workflow files" });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons).toHaveLength(4); // 3 Sub-Skill files + manifest.json, each once
    expect(within(nav).getAllByText("Generated").length).toBe(2);
    expect(within(nav).getAllByText("Reused")).toHaveLength(1);
    expect(within(nav).getAllByText("Customized")).toHaveLength(1);
    expect(within(nav).getByText("AST_DECOUPLING")).toBeTruthy(); // a failed gate
    expect(within(nav).getByRole("img", { name: "1 findings" })).toBeTruthy();
    expect(screen.getByText("Generated: 2")).toBeTruthy(); // the legend
  });

  test("wg15_t03 a finding marks its line in the opened file", async () => {
    const { calls } = fakeApi({
      [`GET ${TREE}`]: { body: tree },
      [`GET ${TREE}/files`]: { body: view },
    });
    const { container } = show();
    await screen.findByRole("region", { name: "Code of action_tool.py" });
    expect(screen.getAllByRole("img", { name: "Finding" })).toHaveLength(1);
    expect(
      container.querySelector("[data-line='3']")?.querySelector("[role='img']"),
    ).not.toBeNull();
    expect(
      calls.some((c) => c.path.startsWith(`${TREE}/files?group=${S}&path=action_tool.py`)),
    ).toBe(true);
  });

  test("picking another file asks for that file, and nothing on the screen can edit", async () => {
    const { calls } = fakeApi({
      [`GET ${TREE}`]: { body: tree },
      [`GET ${TREE}/files`]: { body: view },
    });
    show();
    await screen.findByRole("region", { name: "Code of action_tool.py" });
    fireEvent.click(screen.getByTitle("manifest.json"));
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes("group=workflow&path=manifest.json"))).toBe(true),
    );
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /save|edit|apply|delete|upload/i })).toBeNull();
    expect(calls.every((c) => c.method === "GET")).toBe(true); // no write request was made
  });

  test("a Sub-Skill group links to the attempt viewer", async () => {
    fakeApi({ [`GET ${TREE}`]: { body: tree }, [`GET ${TREE}/files`]: { body: view } });
    show();
    const link = await screen.findByRole("link", { name: "Attempts and changes" });
    expect(link.getAttribute("href")).toBe(`/projects/${P}/workflows/${W}/code?sub=${S}`);
  });

  test("a cut file is flagged", async () => {
    fakeApi({
      [`GET ${TREE}`]: { body: tree },
      [`GET ${TREE}/files`]: { body: { ...view, truncated: true } },
    });
    show();
    expect(await screen.findByText(/longer than the viewer shows/)).toBeTruthy();
  });

  test("code is shown as text, never as HTML", async () => {
    fakeApi({
      [`GET ${TREE}`]: { body: tree },
      [`GET ${TREE}/files`]: {
        body: { ...view, content: "<img src=x onerror=alert(1)>\n", findings: [] },
      },
    });
    const { container } = show();
    await screen.findByRole("region", { name: "Code of action_tool.py" });
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  });

  test("empty: a Workflow with no stored files says so", async () => {
    fakeApi({
      [`GET ${TREE}`]: {
        body: {
          workflow_id: W,
          groups: [
            { ...subGroup, files: [] },
            { ...workflowGroup, files: [] },
          ],
        },
      },
    });
    show();
    expect(await screen.findByText(EMPTY_WORKFLOW_MESSAGE)).toBeTruthy();
  });

  test("error: the problem detail is shown", async () => {
    fakeApi({ [`GET ${TREE}`]: problem(500, "INTERNAL", "boom happened") });
    show();
    expect(await screen.findByText(/boom happened/)).toBeTruthy();
  });

  test("forbidden: a 403 is not shown as code", async () => {
    fakeApi({ [`GET ${TREE}`]: problem(403, "FORBIDDEN", "no access here") });
    show();
    expect(await screen.findByText("Forbidden")).toBeTruthy();
    expect(screen.queryByRole("region")).toBeNull();
  });
});
