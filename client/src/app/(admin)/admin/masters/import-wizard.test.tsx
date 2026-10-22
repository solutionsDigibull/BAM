import { configure, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { ImportReport } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { ImportWizard } from "./import-wizard";

const IMPORT = "POST /admin/masters/import";
const SLOW = 15_000; // multi-step flows; see entry-drawer.test.tsx

const report = (over: Partial<ImportReport> = {}): ImportReport => ({
  mode: "validate-only",
  applied: false,
  sha256: `sha256:${"a".repeat(64)}`,
  tabs: [
    { name: "Organization", created: 4, updated: 0, unchanged: 0, missing: 0, errors: [] },
    { name: "Rules", created: 6, updated: 1, unchanged: 0, missing: 2, errors: [] },
    { name: "Filetype", created: 0, updated: 0, unchanged: 43, missing: 0, errors: [] },
  ],
  stable_ids: ["ORG-EMS", "RULE-BOM"],
  statuses: { PROPOSED: 10 },
  ...over,
});

const modeOf = (init: RequestInit) => (init.body as FormData).get("mode");

function pick(name = "BuLLM - Admin Masters.xlsx", size = 40_000) {
  const file = new File([new Uint8Array(size)], name);
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

const button = (name: string) => screen.getByRole("button", { name });

// A loaded machine can take over findBy's 1 s default to render the next step (seen in a full run).
configure({ asyncUtilTimeout: 4_000 });

afterEach(() => vi.unstubAllGlobals());

describe("Masters import wizard (E3.10)", () => {
  test(
    "e3_10_t01 validate -> preview -> apply",
    async () => {
      const onApplied = vi.fn();
      const { calls } = fakeApi({
        [IMPORT]: (init) =>
          modeOf(init) === "upsert"
            ? { body: report({ mode: "upsert", applied: true }) }
            : { body: report() },
      });
      renderWithRefine(<ImportWizard onClose={() => {}} onApplied={onApplied} />);
      expect(button("Validate")).toHaveProperty("disabled", true);
      pick();
      await waitFor(() => expect(button("Validate")).toHaveProperty("disabled", false));
      fireEvent.click(button("Validate"));
      expect(await screen.findByText(/The workbook is valid/)).toBeDefined();
      expect(screen.getByText("Rules")).toBeDefined();
      expect(button("Apply")).toHaveProperty("disabled", false);
      fireEvent.click(button("Apply"));
      await waitFor(() => expect(onApplied).toHaveBeenCalled());
      expect(calls.map((c) => modeOf(c.init))).toEqual(["validate-only", "upsert"]);
      expect(onApplied.mock.calls[0]![0].applied).toBe(true);
    },
    SLOW,
  );

  test("row errors are listed and Apply stays disabled", async () => {
    const bad = report();
    bad.tabs[1]!.errors = [
      { tab: "Rules", row: 6, column: "RAG Reference", message: "RAG Reference is not valid JSON" },
    ];
    fakeApi({ [IMPORT]: { body: bad } });
    renderWithRefine(<ImportWizard onClose={() => {}} onApplied={() => {}} />);
    pick();
    await waitFor(() => expect(button("Validate")).toHaveProperty("disabled", false));
    fireEvent.click(button("Validate"));
    expect(await screen.findByText("RAG Reference is not valid JSON")).toBeDefined();
    expect(screen.getByText(/1 row error: fix the workbook/)).toBeDefined();
    expect(button("Apply")).toHaveProperty("disabled", true);
  });

  test("e3_10_t02 a structure mismatch shows the diff and Apply is impossible", async () => {
    const mismatch = problem(422, "STRUCTURE_MISMATCH", "Use the published template");
    (mismatch.body as Record<string, unknown>).errors = [
      {
        path: "Skills!row2:col1",
        message: "expected 'Documents', found 'RFQ / Inquiries'",
        diff: {
          tab: "Skills",
          expected: "Documents",
          actual: "RFQ / Inquiries",
          kind: "reordered",
        },
      },
      {
        path: "Skills!row2:col2",
        message: "expected 'RFQ / Inquiries', found 'Documents'",
        diff: {
          tab: "Skills",
          expected: "RFQ / Inquiries",
          actual: "Documents",
          kind: "reordered",
        },
      },
    ];
    fakeApi({ [IMPORT]: mismatch });
    renderWithRefine(<ImportWizard onClose={() => {}} onApplied={() => {}} />);
    pick();
    await waitFor(() => expect(button("Validate")).toHaveProperty("disabled", false));
    fireEvent.click(button("Validate"));
    expect(await screen.findByText("Use the published template")).toBeDefined();
    expect(screen.getAllByText("reordered")).toHaveLength(2);
    expect(screen.getAllByText("Documents").length).toBeGreaterThan(0);
    expect(button("Apply")).toHaveProperty("disabled", true);
  });

  test.each([
    ["masters.xlsm", 1000, "Only .xlsx workbooks can be imported."],
    ["masters.xlsx", 15 * 1024 * 1024 + 1, "The workbook is larger than 15 MB."],
  ])("%s (%i bytes) is refused before upload", async (name, size, message) => {
    const { calls } = fakeApi({});
    renderWithRefine(<ImportWizard onClose={() => {}} onApplied={() => {}} />);
    pick(name, size);
    expect(await screen.findByText(message)).toBeDefined();
    expect(button("Validate")).toHaveProperty("disabled", true);
    expect(calls).toEqual([]);
  });

  test("a refusal (Filetype frozen) shows the server's title, detail and rows", async () => {
    const frozen = problem(
      403,
      "FILETYPE_FROZEN",
      "Only a super-admin can change the 43 Filetype rows.",
    );
    Object.assign(frozen.body as Record<string, unknown>, {
      title: "The Filetype master is frozen",
      errors: [{ path: "Filetype!row7:colA", message: "Filetype #4 differs from the frozen row" }],
    });
    fakeApi({ [IMPORT]: frozen });
    renderWithRefine(<ImportWizard onClose={() => {}} onApplied={() => {}} />);
    pick();
    await waitFor(() => expect(button("Validate")).toHaveProperty("disabled", false));
    fireEvent.click(button("Validate"));
    expect(await screen.findByText("The Filetype master is frozen")).toBeDefined();
    expect(screen.getByText(/Only a super-admin/)).toBeDefined();
    expect(screen.getByText("Filetype #4 differs from the frozen row")).toBeDefined();
    expect(button("Apply")).toHaveProperty("disabled", true);
  });
});
