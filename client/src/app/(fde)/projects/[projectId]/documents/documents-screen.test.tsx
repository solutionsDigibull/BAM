import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";
import type { DocumentOut, PipelineTimeline, ProjectPipelineState } from "@/contracts";

import { EMPTY_DOCUMENTS } from "../../workspace-api";
import { POLL_MS } from "./document-state";
import { DocumentsScreen } from "./documents-screen";
import { EMPTY_BIN } from "./recycle-bin";

vi.mock("next/dynamic", () => ({ default: () => () => <div data-testid="upload-panel" /> }));

const ID = project().id;
const DOCS = `GET /projects/${ID}/documents`;
const TIMELINE = `GET /projects/${ID}/pipeline`;
const RETRY = (doc: string) => `POST /projects/${ID}/documents/${doc}/retry`;
const DELETE = (doc: string) => `DELETE /projects/${ID}/documents/${doc}`;
const RESTORE = (doc: string) => `POST /projects/${ID}/documents/${doc}/restore`;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const doc = (over: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "d1",
  title: "rfq.pdf",
  state: "PARSED",
  size: 2048,
  mime: "application/pdf",
  sha256: "x",
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

const timeline = (state: ProjectPipelineState = "IDLE"): PipelineTimeline => ({
  state,
  steps: [],
  counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0 },
});

const api = (items: DocumentOut[], state: ProjectPipelineState = "IDLE") => ({
  [DOCS]: { body: { items } },
  [TIMELINE]: { body: timeline(state) },
});

const deleted = (over: Partial<DocumentOut> = {}) =>
  doc({
    id: "d9",
    title: "old-quote.pdf",
    deleted_at: "2026-10-10T10:00:00Z",
    purge_after: "2027-01-08T10:00:00Z",
    ...over,
  });

describe("Documents screen (E4.11)", () => {
  test("an empty project says no documents, keeps the upload panel and an empty bin", async () => {
    fakeApi(api([]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText(EMPTY_DOCUMENTS)).toBeDefined();
    expect(screen.getByTestId("upload-panel")).toBeDefined();
    expect(screen.getByRole("tab", { name: "Recycle bin (0)" })).toBeDefined();
  });

  test("the list asks for deleted documents too, so the bin can show them", async () => {
    const fake = fakeApi(api([doc()]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await screen.findByText("rfq.pdf");
    expect(fake.calls.find((c) => c.path.startsWith(`/projects/${ID}/documents`))!.path).toContain(
      "include_deleted=true",
    );
  });

  test("each file shows scan, parse and OCR chips, its size and the reason a failure happened", async () => {
    fakeApi(
      api([
        doc(),
        doc({ id: "d2", title: "scan.pdf", state: "PARSE_FAILED" }),
        doc({ id: "d3", title: "big.pdf", state: "PARSING" }),
      ]),
    );
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await screen.findByText("rfq.pdf");
    expect(screen.getAllByText("Scan: Clean")).toHaveLength(3);
    expect(screen.getByText("Parse: Parsed")).toBeDefined();
    expect(screen.getByText("Parse: Failed")).toBeDefined();
    expect(screen.getByText("Parse: Parsing")).toBeDefined(); // still running: keeps its spinner
    expect(screen.getAllByText("OCR: n/a")).toHaveLength(3);
    expect(screen.getAllByText("2 KB")).toHaveLength(3);
    expect(screen.getByText(/could not be read/)).toBeDefined();
  });

  test("e4_11_t06 an infected file is Quarantined with no actions", async () => {
    fakeApi(api([doc({ state: "REJECTED", title: "evil.pdf" })]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText("Scan: Quarantined")).toBeDefined();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /download/i })).toBeNull();
    expect(screen.getByText(/quarantined: it cannot be downloaded/)).toBeDefined();
  });

  test("Retry is offered only for failed documents", async () => {
    fakeApi(
      api([
        doc({ id: "d1", title: "ok.pdf" }),
        doc({ id: "d2", title: "bad-scan.pdf", state: "SCAN_FAILED" }),
        doc({ id: "d3", title: "bad-parse.pdf", state: "PARSE_FAILED" }),
        doc({ id: "d4", title: "busy.pdf", state: "SCANNING" }),
      ]),
    );
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await screen.findByText("ok.pdf");
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(2);
  });

  test("Retry posts with an Idempotency-Key and the list is asked again", async () => {
    const fake = fakeApi({
      ...api([doc({ state: "PARSE_FAILED" })]),
      [DOCS]: [
        { body: { items: [doc({ state: "PARSE_FAILED" })] } },
        { body: { items: [doc({ state: "SCANNING" })] } },
      ],
      [RETRY("d1")]: { status: 202, body: { run: {} } },
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Scan: Scanning")).toBeDefined(); // chips reset to running
    const call = fake.calls.find((c) => c.method === "POST")!;
    expect((call.init.headers as Headers).get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  test("e4_11_t03 a 409 on retry says only failed documents can be retried", async () => {
    fakeApi({
      ...api([doc({ state: "PARSE_FAILED" })]),
      [RETRY("d1")]: problem(409, "CONFLICT", "not failed"),
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Only failed documents can be retried")).toBeDefined();
  });

  test("a document deleted by someone else while viewing is reported and the list reloads", async () => {
    const fake = fakeApi({
      ...api([doc({ state: "PARSE_FAILED" })]),
      [DOCS]: [{ body: { items: [doc({ state: "PARSE_FAILED" })] } }, { body: { items: [] } }],
      [RETRY("d1")]: problem(404, "NOT_FOUND"),
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/no longer exists/)).toBeDefined();
    expect(await screen.findByText(EMPTY_DOCUMENTS)).toBeDefined();
    expect(
      fake.calls.filter((c) => c.method === "GET" && c.path.includes("/documents")),
    ).toHaveLength(2);
  });

  test("Delete asks first and then moves the document to the bin", async () => {
    const fake = fakeApi({
      ...api([doc()]),
      [DOCS]: [
        { body: { items: [doc()] } },
        { body: { items: [deleted({ id: "d1", title: "rfq.pdf" })] } },
      ],
      [DELETE("d1")]: { status: 204 },
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete rfq.pdf" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/restored for 90 days/)).toBeDefined();
    expect(fake.calls.some((c) => c.method === "DELETE")).toBe(false); // nothing sent before OK
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(EMPTY_DOCUMENTS)).toBeDefined();
    expect(screen.getByRole("tab", { name: "Recycle bin (1)" })).toBeDefined();
    expect(fake.calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  test("cancelling the delete dialog sends nothing", async () => {
    const fake = fakeApi(api([doc()]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete rfq.pdf" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300)); // let the dialog close
    });
    expect(fake.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  test("a document used by a frozen version cannot be deleted (423)", async () => {
    fakeApi({ ...api([doc()]), [DELETE("d1")]: problem(423, "RESOURCE_LOCKED") });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete rfq.pdf" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
    expect(await screen.findByText(/used by a frozen version and cannot be deleted/)).toBeDefined();
  });

  test("a failed request shows the problem detail with a retry", async () => {
    fakeApi({
      [DOCS]: problem(503, "ORCHESTRATOR_UNAVAILABLE", "Try again shortly"),
      [TIMELINE]: { body: timeline() },
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined(); // the error panel's retry
  });
});

describe("Recycle bin (E4.11)", () => {
  const openBin = async () => {
    fireEvent.click(await screen.findByRole("tab", { name: /Recycle bin/ }));
  };

  test("lists only the deleted documents with the purge date and the days left", async () => {
    fakeApi(api([doc({ id: "d1", title: "live.pdf" }), deleted()]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await openBin();
    expect(await screen.findByText("old-quote.pdf")).toBeDefined();
    expect(screen.getByText(/8 Jan 2027 \(\d+ days left\)/)).toBeDefined();
    expect(screen.getByRole("tab", { name: "Documents (1)" })).toBeDefined();
    const panel = screen.getByRole("tabpanel", { name: /Recycle bin/ });
    expect(within(panel).queryByText("live.pdf")).toBeNull();
  });

  test("an empty bin says so", async () => {
    fakeApi(api([doc()]));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await openBin();
    expect(await screen.findByText(EMPTY_BIN)).toBeDefined();
  });

  test("Restore brings the document back to the list", async () => {
    const fake = fakeApi({
      ...api([deleted()]),
      [DOCS]: [
        { body: { items: [deleted()] } },
        { body: { items: [doc({ id: "d9", title: "old-quote.pdf", state: "UPLOADED" })] } },
      ],
      [RESTORE("d9")]: { body: doc({ id: "d9", title: "old-quote.pdf", state: "UPLOADED" }) },
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await openBin();
    fireEvent.click(await screen.findByRole("button", { name: "Restore old-quote.pdf" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Documents (1)" })).toBeDefined());
    expect(screen.getByRole("tab", { name: "Recycle bin (0)" })).toBeDefined();
    expect(fake.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  test("e4_11_t05 restoring after the purge says it was permanently deleted", async () => {
    fakeApi({ ...api([deleted()]), [RESTORE("d9")]: problem(410, "GONE") });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await openBin();
    fireEvent.click(await screen.findByRole("button", { name: "Restore old-quote.pdf" }));
    expect(await screen.findByText("This document was permanently deleted")).toBeDefined();
  });

  test("restoring into a full project says the 20-file limit is reached", async () => {
    fakeApi({ ...api([deleted()]), [RESTORE("d9")]: problem(409, "FILE_LIMIT_REACHED") });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await openBin();
    fireEvent.click(await screen.findByRole("button", { name: "Restore old-quote.pdf" }));
    expect(await screen.findByText(/already has 20 files/)).toBeDefined();
  });
});

describe("Live updates and polling (E4.11)", () => {
  const getsOfDocuments = (fake: ReturnType<typeof fakeApi>) =>
    fake.calls.filter((c) => c.method === "GET" && c.path.startsWith(`/projects/${ID}/documents`));

  test("e4_11_t01 chips progress without a reload while documents are in flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = fakeApi({
      ...api([doc({ state: "SCANNING" })], "INGESTING"),
      [DOCS]: [
        { body: { items: [doc({ state: "SCANNING" })] } },
        { body: { items: [doc({ state: "PARSING" })] } },
        { body: { items: [doc({ state: "PARSED" })] } },
      ],
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText("Scan: Scanning")).toBeDefined();
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 200));
    expect(await screen.findByText("Parse: Parsing")).toBeDefined();
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 200));
    expect(await screen.findByText("Parse: Parsed")).toBeDefined();
    expect(getsOfDocuments(fake)).toHaveLength(3);
  });

  test("e4_11_t07 stops asking once all documents settled and the pipeline moved on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = fakeApi(api([doc({ state: "PARSED" })], "KNOWLEDGE_BUILDING"));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await screen.findByText("Parse: Parsed");
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 4));
    expect(getsOfDocuments(fake)).toHaveLength(1);
  });

  test("it keeps asking while the pipeline is ingesting even if every document looks settled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = fakeApi(api([doc({ state: "PARSED" })], "INGESTING"));
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    await screen.findByText("Parse: Parsed");
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 200));
    expect(getsOfDocuments(fake).length).toBeGreaterThanOrEqual(2);
  });
});
