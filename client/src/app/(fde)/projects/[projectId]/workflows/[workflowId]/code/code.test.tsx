import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";

import { decorations, diffKind, toLines } from "./code-api";
import { CodeScreen, EMPTY_MESSAGE } from "./code-screen";

// Monaco needs a real browser (workers, canvas). The screen's data and wiring are tested with the
// plain line view standing in for it; what Monaco paints comes from `decorations`, tested below.
vi.mock("./monaco-code", async () => ({ default: (await import("./code-lines")).default }));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const S = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const BASE = `/projects/${P}/workflows/${W}/sub-skills/${S}/code/attempts`;

const finding = {
  gate: "AST",
  code: "AST_FORBIDDEN_CALL",
  file: "action.py",
  line: 3,
  message: "no eval",
};
const attempt = (n: number, over: object = {}) => ({
  build_no: 1,
  cycle: 0,
  attempt: n,
  status: "ASSEMBLED",
  template_name: "t",
  template_version: "1",
  correction_reason: n ? "fix the call" : null,
  files: [{ path: "action.py", sha256: "a".repeat(64) }],
  findings: [finding],
  ...over,
});
const view = {
  path: "action.py",
  sha256: "a".repeat(64),
  content:
    "def action(ctx):\n    # >>> LLM_BODY:action_body\n        <img src=x onerror=alert(1)>\n    # <<< LLM_BODY:action_body\n",
  truncated: false,
  slots: [{ start: 3, end: 3 }],
  findings: [finding],
};

const show = () => renderWithRefine(<CodeScreen projectId={P} workflowId={W} subId={S} />);

describe("code viewer data rules (E11.5)", () => {
  test("e11_5_t02 lines outside the slots are protected", () => {
    const { lines } = toLines(view.content, view.slots, []);
    expect(lines.map((l) => l.editable)).toEqual([false, false, true, false]);
  });

  test("e11_5_t03 a finding marks its line; one past the end is kept as loose", () => {
    const { lines, loose } = toLines(view.content, view.slots, [
      finding,
      { ...finding, line: 99 },
      { ...finding, line: null },
    ]);
    expect(lines[2]!.findings).toHaveLength(1);
    expect(loose).toHaveLength(2);
  });

  test("e11_5_t02 the editor shades every protected line and only those", () => {
    const { lines } = toLines(view.content, view.slots, []);
    const shaded = decorations(lines).filter((d) => d.kind === "protected");
    expect(shaded.map((d) => d.line)).toEqual([1, 2, 4]);
  });

  test("e11_5_t03 the editor marks a finding's line with its code and message on hover", () => {
    const { lines } = toLines(view.content, view.slots, [finding]);
    const marks = decorations(lines).filter((d) => d.kind === "finding");
    expect(marks).toEqual([{ line: 3, kind: "finding", hover: "AST_FORBIDDEN_CALL: no eval" }]);
  });

  test("diff lines are classified", () => {
    expect(["+++ a", "--- b", "@@ -1 +1 @@", "+x", "-y", " z"].map(diffKind)).toEqual([
      "meta",
      "meta",
      "hunk",
      "add",
      "del",
      "context",
    ]);
  });
});

describe("code viewer screen (E11.5)", () => {
  test("loading, then the code with shading and a finding marker; code is text, not HTML", async () => {
    fakeApi({
      [`GET ${BASE}`]: { body: { items: [attempt(0)] } },
      [`GET ${BASE}/0/files`]: { body: view },
    });
    const { container } = show();
    expect(container.querySelector(".ant-skeleton")).not.toBeNull();
    await screen.findByRole("region", { name: "Code of action.py" });
    expect(container.querySelector("[data-line='3']")?.getAttribute("data-protected")).toBe(
      "false",
    );
    expect(container.querySelector("[data-line='1']")?.getAttribute("data-protected")).toBe("true");
    expect(screen.getAllByRole("img", { name: "Finding" })).toHaveLength(1);
    expect(container.querySelector("img")).toBeNull(); // the <img> in the code is shown as text
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  });

  test("empty: no attempts shows 'Code not generated yet'", async () => {
    fakeApi({ [`GET ${BASE}`]: { body: { items: [] } } });
    show();
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeTruthy();
  });

  test("an attempt without files says so and still lists its findings", async () => {
    fakeApi({
      [`GET ${BASE}`]: {
        body: { items: [attempt(0, { files: [], status: "ASSEMBLY_FAILED" })] },
      },
    });
    show();
    expect(await screen.findByText("This attempt produced no files")).toBeTruthy();
    expect(screen.getByText(/AST_FORBIDDEN_CALL: no eval/)).toBeTruthy();
  });

  test("error: the problem detail is shown", async () => {
    fakeApi({ [`GET ${BASE}`]: problem(500, "INTERNAL", "boom happened") });
    show();
    expect(await screen.findByText(/boom happened/)).toBeTruthy();
  });

  test("forbidden: a 403 is not shown as code", async () => {
    fakeApi({ [`GET ${BASE}`]: problem(403, "FORBIDDEN", "no access here") });
    show();
    expect(await screen.findByText("Forbidden")).toBeTruthy();
    expect(screen.queryByRole("region")).toBeNull();
  });

  test("e11_5_t01 the corrected attempt can be compared with the previous one", async () => {
    const { calls } = fakeApi({
      [`GET ${BASE}`]: { body: { items: [attempt(0), attempt(1)] } },
      [`GET ${BASE}/1/files`]: { body: view },
      [`GET ${BASE}/0/diff/1`]: {
        body: {
          from_attempt: 0,
          to_attempt: 1,
          path: "action.py",
          changed: true,
          truncated: false,
          unified: "--- a\n+++ b\n@@ -1 +1 @@\n-b = 2\n+b = 3\n",
        },
      },
    });
    const { container } = show();
    await screen.findByRole("region", { name: "Code of action.py" }); // the newest attempt first
    fireEvent.click(screen.getByText("Changes since attempt 0"));
    await screen.findByRole("region", { name: "Changes to action.py" });
    await waitFor(() => expect(container.querySelectorAll("[data-kind='add']")).toHaveLength(1));
    expect(container.querySelectorAll("[data-kind='del']")).toHaveLength(1);
    expect(calls.some((c) => c.path.startsWith(`${BASE}/0/diff/1?path=action.py`))).toBe(true);
  });

  test("an identical attempt says there are no changes", async () => {
    fakeApi({
      [`GET ${BASE}`]: { body: { items: [attempt(0), attempt(1)] } },
      [`GET ${BASE}/1/files`]: { body: view },
      [`GET ${BASE}/0/diff/1`]: {
        body: {
          from_attempt: 0,
          to_attempt: 1,
          path: "action.py",
          changed: false,
          truncated: false,
          unified: "",
        },
      },
    });
    show();
    await screen.findByRole("region", { name: "Code of action.py" });
    fireEvent.click(screen.getByText("Changes since attempt 0"));
    expect(await screen.findByText("No changes in this file")).toBeTruthy();
  });
});
