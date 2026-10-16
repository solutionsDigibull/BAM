import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";
import type { CoverageRow, KnowledgeVersionSummary } from "@/contracts";

import { COVERAGE_UNAVAILABLE, EMPTY_KNOWLEDGE, POLL_MS, okfFileName } from "./knowledge-api";
import { KnowledgePanel } from "./knowledge-panel";

const P = project();
const ID = P.id;
const LIST = `GET /projects/${ID}/knowledge`;
const REBUILD = `POST /projects/${ID}/knowledge/rebuild`;
const KV = "0190f3a2-7b1c-7d4e-8f00-00000000c0a1";
const SHA = "ab".repeat(32);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const row = (over: Partial<CoverageRow> = {}): CoverageRow => ({
  document_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000d0c1",
  title: "rfq.pdf",
  status: "COVERED",
  chunks: 4,
  entities: 7,
  reason: null,
  ...over,
});

const version = (over: Partial<KnowledgeVersionSummary> = {}): KnowledgeVersionSummary => ({
  id: KV,
  version: 2,
  status: "READY",
  coverage: { documents: [], masters_included: 0, entities: 7 },
  error_reason: null,
  created_at: "2026-10-16T10:00:00Z",
  finished_at: "2026-10-16T10:03:00Z",
  okf_sha256: SHA,
  counts: { documents: 1, entities: 7, relations: 3, chunks: 4 },
  documents: [row()],
  ...over,
});

const list = (...versions: KnowledgeVersionSummary[]) => ({ body: { versions } });

/** The value shown under a Statistic title (a table header may carry the same word). */
const stat = (title: string) =>
  Array.from(document.querySelectorAll(".ant-statistic")).find(
    (el) => el.querySelector(".ant-statistic-title")?.textContent === title,
  )?.textContent ?? "";

const render = () =>
  renderWithRefine(<KnowledgePanel projectId={ID} projectName="Acme onboarding" />);

describe("Knowledge status and coverage (E5.6)", () => {
  test("e5_6_t01 building shows progress, then READY with counts, and polling stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const building = version({ status: "BUILDING", finished_at: null, okf_sha256: null });
    const { calls } = fakeApi({ [LIST]: [list(building), list(building), list(version())] });
    render();
    expect(await screen.findByLabelText("Building progress")).toBeDefined();
    expect(screen.getByRole("button", { name: "Rebuild" }).hasAttribute("disabled")).toBe(true);
    await act(async () => void vi.advanceTimersByTime(POLL_MS + 10));
    await act(async () => void vi.advanceTimersByTime(POLL_MS + 10));
    await waitFor(() => expect(screen.getByText("Ready")).toBeDefined());
    expect(screen.queryByLabelText("Building progress")).toBeNull();
    const expected: [string, string][] = [
      ["Entities", "7"],
      ["Relations", "3"],
      ["Chunks", "4"],
    ];
    for (const [title, value] of expected) {
      expect(stat(title)).toContain(value);
    }
    const polls = calls.filter((c) => c.method === "GET").length;
    await act(async () => void vi.advanceTimersByTime(POLL_MS * 3));
    expect(calls.filter((c) => c.method === "GET").length).toBe(polls); // terminal: no more polls
  });

  test("e5_6_t02 download is a link to the API for READY only, named after the project", async () => {
    fakeApi({ [LIST]: list(version()) });
    render();
    const link = (await screen.findByRole("button", { name: "Download OKF bundle" })).closest("a");
    expect(link?.getAttribute("href")).toBe(
      `/api/proxy/api/v1/projects/${ID}/knowledge/${KV}/okf-bundle`,
    );
    expect(link?.getAttribute("download")).toBe("okf-Acme-onboarding-v2.zip");
    expect(screen.getByText(SHA.slice(0, 8), { exact: false })).toBeDefined();
  });

  test("e5_6_download_disabled_unless_ready", async () => {
    fakeApi({ [LIST]: list(version({ status: "FAILED", okf_sha256: null })) });
    render();
    const button = await screen.findByRole("button", { name: "Download OKF bundle" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.closest("a")).toBeNull();
  });

  test("e5_6_t03 failed documents come first with their reason and a Rebuild action", async () => {
    const failed = row({
      document_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000d0c2",
      title: "scan.pdf",
      status: "FAILED",
      reason: "NOT_PARSED",
      chunks: 0,
      entities: 0,
    });
    const { calls } = fakeApi({
      [LIST]: list(version({ documents: [row(), failed] })),
      [REBUILD]: { status: 202, body: { run: {} } },
    });
    render();
    const rows = await screen.findAllByRole("row");
    expect(rows[1]?.textContent).toContain("scan.pdf");
    expect(rows[1]?.textContent).toContain("NOT_PARSED");
    fireEvent.click(rows[1]!.querySelector("button")!);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(await screen.findByText("Rebuild started.")).toBeDefined();
  });

  test("e5_6_t04 malformed coverage renders zeros and 'Coverage unavailable'", async () => {
    const broken = { ...version(), coverage: null, counts: null, documents: null } as unknown;
    fakeApi({ [LIST]: list(broken as KnowledgeVersionSummary) });
    render();
    expect(await screen.findByText(COVERAGE_UNAVAILABLE)).toBeDefined();
    expect(stat("Entities")).toContain("0");
  });

  test("e5_6_t05 rebuild conflict shows 'Knowledge is already building'", async () => {
    fakeApi({
      [LIST]: list(version()),
      [REBUILD]: problem(409, "KNOWLEDGE_BUILDING", "A knowledge version is already being built."),
    });
    render();
    fireEvent.click(await screen.findByRole("button", { name: "Rebuild" }));
    expect(await screen.findByText("Knowledge is already building.")).toBeDefined();
  });

  test("e5_6_t07 a reason with markup is shown as text", async () => {
    const evil = row({ status: "FAILED", reason: "<script>x</script>", title: "<b>t</b>" });
    fakeApi({ [LIST]: list(version({ documents: [evil] })) });
    const { container } = render();
    expect(await screen.findByText("<script>x</script>")).toBeDefined();
    expect(screen.getByText("<b>t</b>")).toBeDefined();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  test("e5_6_t08 a double click sends exactly one rebuild with an Idempotency-Key", async () => {
    const { calls } = fakeApi({
      [LIST]: list(version()),
      [REBUILD]: { status: 202, body: { run: {} } },
    });
    render();
    const button = await screen.findByRole("button", { name: "Rebuild" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(1));
    const headers = new Headers(calls.find((c) => c.method === "POST")!.init.headers);
    expect(headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
  });

  test("e5_6_empty_state_without_any_version", async () => {
    fakeApi({ [LIST]: list() });
    render();
    expect(await screen.findByText(EMPTY_KNOWLEDGE)).toBeDefined();
  });

  test("e5_6_t06 another project's panel shows not found and no data", async () => {
    fakeApi({ [LIST]: problem(404, "NOT_FOUND", "") });
    render();
    expect(await screen.findByText(/not found/i)).toBeDefined();
    expect(screen.queryByText("Knowledge v2")).toBeNull();
  });

  test("e5_6_file_name_is_safe", () => {
    expect(okfFileName("Acme / Q4 <bid>", 3)).toBe("okf-Acme-Q4-bid-v3.zip");
    expect(okfFileName("///", 1)).toBe("okf-project-v1.zip");
  });
});
