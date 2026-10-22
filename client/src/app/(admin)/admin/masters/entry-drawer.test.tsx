import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { STALE_MESSAGE } from "@/components/ui-kit";
import type { MasterEntry } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { MasterTable } from "./master-table";
import { ragError, RAG_KEYS, RAG_TEMPLATE, withStatus } from "./masters-api";
import { entry, LAYOUT } from "./test-fixtures";

const RULES = LAYOUT.types.find((t) => t.master_type === "RULES")!;
const E = entry();
const LIST = "GET /admin/masters/RULES";
const ONE = `GET /admin/masters/RULES/${E.id}`;
const VERSION = `POST /admin/masters/RULES/${E.id}/versions`;

const saved = (over: Partial<MasterEntry>) => ({
  status: 201,
  body: { ...E, version: 2, ...over },
});
// ponytail: the drawer flows (open, pick from a dropdown, save, toast) pass in ~2s alone but can pass 5s
// when the whole suite runs in parallel on a slow machine; raise the budget, not the risk of flakes.
const SLOW = 15_000;

const body = (init: RequestInit) =>
  JSON.parse(String(init.body)) as { payload: Record<string, unknown> };

async function openEditor() {
  fireEvent.click((await screen.findAllByRole("button", { name: `Edit ${E.row_label}` }))[0]!);
  const drawer = await screen.findByRole("dialog");
  await within(drawer).findByDisplayValue(E.payload.domain_rule as string);
  return drawer;
}

const pickStatus = async (drawer: HTMLElement, label: string) => {
  fireEvent.mouseDown(within(drawer).getByRole("combobox", { name: "Status" }));
  fireEvent.click((await screen.findAllByText(label)).at(-1)!);
};

// A loaded machine can take over findBy's 1 s default to render the next step (seen in a full run).
configure({ asyncUtilTimeout: 4_000 });

afterEach(() => vi.unstubAllGlobals());

describe("Status in Misc (TD §7.3.1)", () => {
  test.each([
    ["Status: Proposed", "APPROVED", "Status: Approved"],
    ["Policy note; Status: Proposed.", "RETIRED", "Policy note; Status: Retired"],
    ["Owner: QA", "APPROVED", "Owner: QA; Status: Approved"],
    ["", "ILLUSTRATIVE", "Status: Illustrative"],
    ["Version and QA owner TBD.", "APPROVED", "Version and QA owner TBD. Status: Approved"],
  ] as const)("%j -> %s", (misc, status, want) => {
    expect(withStatus(misc, status)).toBe(want);
  });
});

describe("RAG Reference editor (E3.10)", () => {
  test("empty and the 7-key template are valid", () => {
    expect(ragError("")).toBeNull();
    expect(ragError(RAG_TEMPLATE)).toBeNull();
  });

  test.each([
    ["not json", "RAG Reference is not valid JSON"],
    ["[]", "RAG Reference must be a JSON object"],
    [
      JSON.stringify({ ...Object.fromEntries(RAG_KEYS.map((k) => [k, ""])), Extra: "" }),
      "Unknown key: Extra",
    ],
  ])("%s is rejected", (text, message) => {
    expect(ragError(text)).toBe(message);
  });
});

describe("Masters edit drawer (E3.10)", () => {
  test(
    "e3_10_t04 status chip edit writes Status: Approved into Misc through the API",
    async () => {
      const { calls } = fakeApi({
        [LIST]: { body: { items: [E] } },
        [ONE]: { body: E, etag: '"3"' },
        [VERSION]: (init) => saved({ status: "APPROVED", payload: body(init).payload }),
      });
      renderWithRefine(<MasterTable info={RULES} />);
      const drawer = await openEditor();
      await pickStatus(drawer, "APPROVED");
      expect(within(drawer).getByLabelText("Misc")).toHaveProperty("value", "Status: Approved");
      fireEvent.click(within(drawer).getByRole("button", { name: "Save new version" }));
      await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
      const post = calls.find((c) => c.method === "POST")!;
      expect(new Headers(post.init.headers).get("if-match")).toBe('"3"');
      expect(body(post.init).payload.misc).toBe("Status: Approved");
      expect(body(post.init).payload.rag_reference).toBeNull(); // empty RAG cell stays empty
      expect(await screen.findByText(`Saved ${E.row_label} (version 2)`)).toBeDefined();
    },
    SLOW,
  );

  test(
    "e3_10_t05 removing a RAG key shows the error and disables Save",
    async () => {
      fakeApi({ [LIST]: { body: { items: [E] } }, [ONE]: { body: E, etag: '"1"' } });
      renderWithRefine(<MasterTable info={RULES} />);
      const drawer = await openEditor();
      const rag = within(drawer).getByLabelText("RAG Reference");
      const sixKeys = Object.fromEntries(
        RAG_KEYS.filter((k) => k !== "Parent Entity").map((k) => [k, ""]),
      );
      fireEvent.change(rag, { target: { value: JSON.stringify(sixKeys) } });
      expect(await within(drawer).findByText("Missing key: Parent Entity")).toBeDefined();
      expect(within(drawer).getByRole("button", { name: "Save new version" })).toHaveProperty(
        "disabled",
        true,
      );
      fireEvent.change(rag, { target: { value: RAG_TEMPLATE } });
      await waitFor(() =>
        expect(within(drawer).getByRole("button", { name: "Save new version" })).toHaveProperty(
          "disabled",
          false,
        ),
      );
    },
    SLOW,
  );

  test.each([
    ["412", problem(412, "PRECONDITION_FAILED")],
    ["409 VERSION_CONFLICT", problem(409, "VERSION_CONFLICT")],
  ])(
    "e3_10_t07 %s opens the reload dialog and keeps the unsaved values",
    async (_, reply) => {
      const fresh = entry({
        payload: { ...E.payload, pcb_panelization: "2x2 array (someone else)" },
      });
      fakeApi({
        [LIST]: { body: { items: [E] } },
        [ONE]: [
          { body: E, etag: '"1"' },
          { body: fresh, etag: '"2"' },
        ],
        [VERSION]: reply,
      });
      renderWithRefine(<MasterTable info={RULES} />);
      const drawer = await openEditor();
      const field = within(drawer).getByLabelText("PCB Panelization");
      fireEvent.change(field, { target: { value: "my unsaved edit" } });
      fireEvent.click(within(drawer).getByRole("button", { name: "Save new version" }));
      expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
      expect(field).toHaveProperty("value", "my unsaved edit"); // kept for copy until Reload
      fireEvent.click(screen.getByRole("button", { name: "Reload" }));
      await waitFor(() => expect(field).toHaveProperty("value", "2x2 array (someone else)"));
    },
    SLOW,
  );

  test(
    "a 422 lands on the field it names",
    async () => {
      const bad = problem(422, "VALIDATION_ERROR", "Check the fields");
      (bad.body as Record<string, unknown>).errors = [
        { path: "payload", message: "multiple Status fragments in Misc" },
      ];
      fakeApi({
        [LIST]: { body: { items: [E] } },
        [ONE]: { body: E, etag: '"1"' },
        [VERSION]: bad,
      });
      renderWithRefine(<MasterTable info={RULES} />);
      const drawer = await openEditor();
      fireEvent.click(within(drawer).getByRole("button", { name: "Save new version" }));
      expect(await within(drawer).findByText("multiple Status fragments in Misc")).toBeDefined();
    },
    SLOW,
  );

  test("sample rows (#1) keep Illustrative: the status can't be changed", async () => {
    const sample = entry({
      row_label: "Rule #1",
      source_kind: "BAM_0_2_SAMPLE",
      status: "ILLUSTRATIVE",
    });
    fakeApi({
      [LIST]: { body: { items: [sample] } },
      [ONE]: { body: sample, etag: '"1"' },
    });
    renderWithRefine(<MasterTable info={RULES} />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Edit Rule #1" }))[0]!);
    const drawer = await screen.findByRole("dialog");
    await within(drawer).findByText("ILLUSTRATIVE");
    expect(within(drawer).getByRole("combobox", { name: "Status" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  test(
    "Add Row creates an entry and refreshes the list",
    async () => {
      const created = entry({
        id: "0190f3a2-7b1c-7d4e-8f00-00000000e009",
        row_label: "Rule #7",
        stable_id: "RULE-NEW",
      });
      const { calls } = fakeApi({
        [LIST]: [{ body: { items: [] } }, { body: { items: [created] } }],
        "POST /admin/masters/RULES": { status: 201, body: created },
      });
      renderWithRefine(<MasterTable info={RULES} />);
      fireEvent.click(await screen.findByRole("button", { name: /Add Row/ }));
      const drawer = await screen.findByRole("dialog");
      fireEvent.change(within(drawer).getByLabelText("Domain Rule"), {
        target: { value: "RULE-NEW: new rule. Policy: x" },
      });
      fireEvent.click(within(drawer).getByRole("button", { name: "Create" }));
      expect(await screen.findByText("Created Rule #7")).toBeDefined();
      const post = calls.find((c) => c.method === "POST")!;
      expect(new Headers(post.init.headers).get("if-match")).toBeNull();
      expect(body(post.init).payload.domain_rule).toBe("RULE-NEW: new rule. Policy: x");
      expect(await screen.findByText("RULE-NEW")).toBeDefined();
    },
    SLOW,
  );

  test("Filetype has no edit controls even through the table", async () => {
    const FILETYPE = LAYOUT.types.find((t) => t.master_type === "FILETYPE")!;
    fakeApi({
      "GET /admin/masters/FILETYPE": { body: { items: [entry({ master_type: "FILETYPE" })] } },
    });
    renderWithRefine(<MasterTable info={FILETYPE} />);
    await screen.findByText("1 of 1 rows");
    expect(screen.queryByRole("button", { name: /Edit|Add Row/ })).toBeNull();
  });
});
