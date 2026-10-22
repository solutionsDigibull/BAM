import { act, configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { FiletypeInfo, MasterDocumentOut } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "../projects/test-harness";
import { DocumentsPanel } from "./documents-panel";
import { DOCUMENT_POLL_MS, isActive, PENDING_WINDOW_MS } from "./masters-api";
import { entry } from "./test-fixtures";

const LIST = "GET /admin/master-documents";
const FILETYPES = "GET /admin/filetypes";
const SLOW = 15_000; // multi-step flows; see entry-drawer.test.tsx

const doc = (over: Partial<MasterDocumentOut> = {}): MasterDocumentOut => ({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000d001",
  project_id: null,
  title: "certification.png",
  filetype_row_label: "Filetype #31",
  intent_class: "Evidence / media",
  master_type: "ORGANIZATION",
  master_entry_id: null,
  route_source: "DEFAULT_BY_FILETYPE",
  sha256: `sha256:${"b".repeat(64)}`,
  size: 2048,
  scan_status: "CLEAN",
  parse_status: "PARSED",
  created_at: new Date().toISOString(),
  ...over,
});

const pdf = { extensions: [".pdf", ".png"], mvp_enabled: true } as unknown as FiletypeInfo;

function drop(name: string, size = 2048) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File([new Uint8Array(size)], name)] } });
}

// A loaded machine can take over findBy's 1 s default to render the next step (seen in a full run).
configure({ asyncUtilTimeout: 4_000 });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Master documents panel (E3.11)", () => {
  test("e3_11_t03 an infected row is quarantined with no override or download", async () => {
    fakeApi({
      [LIST]: { body: { items: [doc({ scan_status: "INFECTED", parse_status: "PENDING" })] } },
      [FILETYPES]: { body: [] },
    });
    renderWithRefine(<DocumentsPanel />);
    expect(await screen.findByText("Quarantined")).toBeDefined();
    expect(screen.getByText("Quarantined · no actions")).toBeDefined();
    expect(screen.queryByRole("button", { name: /Change route/ })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  test("e3_11_t04 a 415 from the server is shown on that file", async () => {
    fakeApi({
      [LIST]: { body: { items: [] } },
      [FILETYPES]: { body: [] }, // no client allowlist yet: the server decides
      "POST /admin/master-documents": problem(415, "UNSUPPORTED_TYPE", ".step is a Future type"),
    });
    renderWithRefine(<DocumentsPanel />);
    await screen.findByText(/No master documents yet/);
    drop("part.step");
    expect(await screen.findByText("File type not supported yet")).toBeDefined();
    expect(screen.getByText("part.step")).toBeDefined();
  });

  test("a file the Filetype allowlist doesn't cover, or over 15 MB, never leaves the browser", async () => {
    const { calls } = fakeApi({ [LIST]: { body: { items: [] } }, [FILETYPES]: { body: [pdf] } });
    renderWithRefine(<DocumentsPanel />);
    await screen.findByText(/No master documents yet/);
    await waitFor(() => expect(calls.filter((c) => c.path === "/admin/filetypes")).toHaveLength(1));
    drop("drawing.dwg");
    expect(await screen.findByText("File type not supported yet")).toBeDefined();
    drop("scan.pdf", 15 * 1024 * 1024 + 1);
    expect(await screen.findByText("Larger than 15 MB")).toBeDefined();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  test("upload is scanned and routed by the server", async () => {
    const { calls } = fakeApi({
      [LIST]: [{ body: { items: [] } }, { body: { items: [doc()] } }],
      [FILETYPES]: { body: [pdf] },
      "POST /admin/master-documents": { status: 201, body: doc() },
    });
    renderWithRefine(<DocumentsPanel />);
    await screen.findByText(/No master documents yet/);
    await waitFor(() => expect(calls.some((c) => c.path === "/admin/filetypes")).toBe(true));
    drop("certification.png");
    expect(await screen.findByText("Routed to Organization (Evidence / media)")).toBeDefined();
    const post = calls.find((c) => c.method === "POST")!;
    expect((post.init.body as FormData).get("file")).toBeInstanceOf(File);
    expect(await screen.findByText("By filetype")).toBeDefined();
  });

  test("a 422 MALWARE upload is reported as quarantined", async () => {
    fakeApi({
      [LIST]: { body: { items: [] } },
      [FILETYPES]: { body: [] },
      "POST /admin/master-documents": problem(422, "MALWARE", "The file contains malware"),
    });
    renderWithRefine(<DocumentsPanel />);
    await screen.findByText(/No master documents yet/);
    drop("eicar.pdf");
    expect(await screen.findByText("Quarantined: the file contains malware")).toBeDefined();
  });

  test(
    "override: Skills with ADMIN_OVERRIDE, sent with If-Match",
    async () => {
      const moved = doc({ master_type: "SKILLS", route_source: "ADMIN_OVERRIDE" });
      const { calls } = fakeApi({
        [LIST]: [{ body: { items: [doc()] } }, { body: { items: [moved] } }],
        [FILETYPES]: { body: [] },
        [`GET /admin/master-documents/${doc().id}`]: { body: doc(), etag: '"4"' },
        [`PATCH /admin/master-documents/${doc().id}`]: { body: moved, etag: '"5"' },
        "GET /admin/masters/ORGANIZATION": { body: { items: [] } },
        "GET /admin/masters/SKILLS": {
          body: { items: [entry({ master_type: "SKILLS", stable_id: "SK-INTAKE" })] },
        },
      });
      renderWithRefine(<DocumentsPanel />);
      fireEvent.click(
        await screen.findByRole("button", { name: "Change route of certification.png" }),
      );
      fireEvent.mouseDown(screen.getByRole("combobox", { name: "Master for certification.png" }));
      fireEvent.click((await screen.findAllByText("Skills")).at(-1)!);
      fireEvent.click(screen.getByRole("button", { name: "Save route" }));
      expect(await screen.findByText("ADMIN_OVERRIDE")).toBeDefined();
      const patch = calls.find((c) => c.method === "PATCH")!;
      expect(new Headers(patch.init.headers).get("if-match")).toBe('"4"');
      expect(JSON.parse(String(patch.init.body))).toEqual({
        master_type: "SKILLS",
        master_entry_id: null,
      });
    },
    SLOW,
  );

  test("the override stays disabled until the scan is clean", async () => {
    fakeApi({
      [LIST]: { body: { items: [doc({ scan_status: null, parse_status: "PENDING" })] } },
      [FILETYPES]: { body: [] },
    });
    renderWithRefine(<DocumentsPanel />);
    expect(await screen.findByText("Scanning")).toBeDefined();
    expect(
      screen.getByRole("button", { name: "Change route of certification.png" }),
    ).toHaveProperty("disabled", true);
  });

  test("an empty master says so", async () => {
    const { calls } = fakeApi({ [LIST]: { body: { items: [] } }, [FILETYPES]: { body: [] } });
    renderWithRefine(<DocumentsPanel />);
    await screen.findByText(/No master documents yet/);
    fireEvent.click(screen.getByText("Skills"));
    expect(await screen.findByText("No documents routed to Skills yet.")).toBeDefined();
    expect(calls.some((c) => c.path === "/admin/master-documents?master_type=SKILLS")).toBe(true);
  });

  test("e3_11_t05 polling stops once every row has settled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = fakeApi({
      [LIST]: [
        { body: { items: [doc({ scan_status: null, parse_status: "PENDING" })] } },
        { body: { items: [doc({ parse_status: "PARSING" })] } },
        { body: { items: [doc({ parse_status: "PARSED" })] } },
      ],
      [FILETYPES]: { body: [] },
    });
    const lists = () => calls.filter((c) => c.path === "/admin/master-documents").length;
    renderWithRefine(<DocumentsPanel />);
    expect(await screen.findByText("Scanning")).toBeDefined();
    for (const chip of ["PARSING", "PARSED"]) {
      await act(() => vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS));
      expect(await screen.findByText(chip)).toBeDefined();
    }
    const settled = lists();
    await act(() => vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS * 4));
    expect(lists()).toBe(settled); // no further list requests after settle
    expect(within(document.body).queryByText(/updating every 5 s/)).toBeNull();
  });
});

describe("which rows still poll", () => {
  const now = Date.parse("2026-10-22T12:00:00Z");
  test.each([
    ["scan unfinished", doc({ scan_status: null }), true],
    ["parsing", doc({ parse_status: "PARSING" }), true],
    ["just queued", doc({ parse_status: "PENDING", created_at: "2026-10-22T11:58:00Z" }), true],
    [
      "queued for a loader",
      doc({
        parse_status: "PENDING",
        created_at: new Date(now - PENDING_WINDOW_MS - 1).toISOString(),
      }),
      false,
    ],
    ["parsed", doc({ parse_status: "PARSED" }), false],
    ["scan failed", doc({ scan_status: "SCAN_FAILED", parse_status: "PENDING" }), false],
  ])("%s", (_, d, active) => {
    expect(isActive(d, now)).toBe(active);
  });
});
