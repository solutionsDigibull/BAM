import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { emptyMessage } from "./masters-api";
import { MastersScreen } from "./masters-screen";
import { entry, filetype, LAYOUT } from "./test-fixtures";

const routes = (over: Record<string, unknown> = {}) => ({
  "GET /admin/master-types": { body: LAYOUT },
  "GET /admin/masters/ORGANIZATION": { body: { items: [] } },
  "GET /admin/masters/RULES": { body: { items: [entry()] } },
  "GET /admin/filetypes": {
    body: [filetype(1, 1, false), filetype(2, 1, true), filetype(3, 2, false)],
  },
  ...over,
});

const openTab = async (name: string) => fireEvent.click(await screen.findByRole("tab", { name }));

afterEach(() => vi.unstubAllGlobals());

describe("Admin Masters screen (E3.10)", () => {
  test("tabs: README, Organization, Rules, Skills, Tool Factory, Documents and Filetype", async () => {
    fakeApi(routes());
    renderWithRefine(<MastersScreen />);
    await screen.findByText(LAYOUT.readme.title);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual([
      "README",
      "Organization",
      "Rules",
      "Skills",
      "Tool Factory",
      "Documents",
      "Filetype",
    ]);
    expect(screen.getByText("1. STRUCTURE")).toBeDefined();
  });

  test("e3_10_t03 the Rules tab is the Rules Engine: workflow map, filters and the row count", async () => {
    fakeApi(routes({ "GET /admin/masters/SKILLS": { body: { items: [] } } }));
    renderWithRefine(<MastersScreen />);
    await openTab("Rules");
    expect(await screen.findByRole("group", { name: "Workflow map" })).toBeDefined();
    expect(screen.getAllByLabelText("Filter by Category").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("Filter by Status").length).toBeGreaterThan(0);
    expect(await screen.findByText("1 of 1 rows")).toBeDefined();
  });

  test("a tab with no entries shows its empty state", async () => {
    fakeApi(routes());
    renderWithRefine(<MastersScreen />);
    await openTab("Organization");
    expect(await screen.findByText(emptyMessage("Organization"))).toBeDefined();
  });

  test("search filters the rows", async () => {
    const other = entry({
      id: "0190f3a2-7b1c-7d4e-8f00-00000000e002",
      stable_id: "RULE-OFFER",
      row_label: "Rule #3",
    });
    fakeApi(routes({ "GET /admin/masters/RULES": { body: { items: [entry(), other] } } }));
    renderWithRefine(<MastersScreen />);
    await openTab("Rules");
    fireEvent.change(
      (await screen.findAllByLabelText("Search Rules")).find((e) => e.tagName === "INPUT")!,
      {
        target: { value: "offer" },
      },
    );
    expect(await screen.findByText("1 of 2 rows")).toBeDefined();
  }, 30_000);

  test("e3_10_t06 Filetype is read-only with MVP loader and default route", async () => {
    fakeApi(routes());
    renderWithRefine(<MastersScreen />);
    await openTab("Filetype");
    const panel = await screen.findByRole("tabpanel");
    expect(await within(panel).findByText("Filetype #1")).toBeDefined();
    expect(within(panel).getByText("pdf-text")).toBeDefined(); // MVP loader
    expect(within(panel).getAllByText("knowledge").length).toBe(3); // default route
    expect(within(panel).getByText(/Read-only/)).toBeDefined();
    expect(within(panel).queryByRole("button", { name: /add|edit|new|save|delete/i })).toBeNull();
    fireEvent.click(within(panel).getByText("Tier 2 (1)"));
    await waitFor(() => expect(within(panel).queryByText("Filetype #1")).toBeNull());
    expect(within(panel).getByText("Filetype #3")).toBeDefined();
  });

  test("layout error shows the problem detail with a retry", async () => {
    fakeApi(
      routes({ "GET /admin/master-types": problem(503, "UNAVAILABLE", "Try again shortly") }),
    );
    renderWithRefine(<MastersScreen />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });
});

describe("Export and template downloads (E3.4 hand-off)", () => {
  let clicked: string[];
  beforeEach(() => {
    clicked = [];
    URL.createObjectURL = vi.fn(() => "blob:sbam");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download);
    });
  });

  test("Export downloads the workbook through the proxy", async () => {
    const { calls } = fakeApi(routes({ "GET /admin/masters/export": { body: {} } }));
    renderWithRefine(<MastersScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /Export/ }));
    expect(await screen.findByText("Downloaded BAM_masters.xlsx")).toBeDefined();
    expect(calls.some((c) => c.path === "/admin/masters/export")).toBe(true);
    expect(clicked).toEqual(["BAM_masters.xlsx"]);
  });

  test("Download template fetches the empty template", async () => {
    fakeApi(routes({ "GET /admin/masters/template": { body: {} } }));
    renderWithRefine(<MastersScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /Download template/ }));
    expect(await screen.findByText("Downloaded BAM_masters_template.xlsx")).toBeDefined();
  });

  test("an export that fails names the cell that is too long", async () => {
    const message = "Rule #2 has a value longer than 32767 characters";
    const tooLong = problem(422, "VALIDATION_ERROR", "Shorten Rule #2, then export again.");
    (tooLong.body as Record<string, unknown>).errors = [{ path: "Rules!row4:col12", message }];
    fakeApi(routes({ "GET /admin/masters/export": tooLong }));
    renderWithRefine(<MastersScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /Export/ }));
    expect(await screen.findByText(`Rules!row4:col12: ${message}`)).toBeDefined();
    expect(clicked).toEqual([]);
  });
});
