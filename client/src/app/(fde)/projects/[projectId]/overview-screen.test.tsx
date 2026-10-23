import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";
import type { PipelineTimeline, ProjectPipelineState, WorkflowSummary } from "@/contracts";

import { EMPTY_OVERVIEW, EMPTY_WORKFLOWS } from "../workspace-api";
import { OverviewScreen } from "./overview-screen";

const P = project();
const ID = P.id;
const TIMELINE = `GET /projects/${ID}/pipeline`;
const WORKFLOWS = `GET /projects/${ID}/workflows`;
const START = `POST /projects/${ID}/pipeline/start`;
const PROJECTS = "GET /projects";

afterEach(() => vi.unstubAllGlobals());

const timeline = (
  state: ProjectPipelineState,
  counts: Partial<PipelineTimeline["counts"]> = {},
): PipelineTimeline => ({
  state,
  steps: [],
  counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0, ...counts },
});

const base = (t: PipelineTimeline, workflows: WorkflowSummary[] = []) => ({
  [PROJECTS]: { body: { items: [P] } },
  [TIMELINE]: { body: t },
  [WORKFLOWS]: { body: { items: workflows } },
});

const workflow = (over: Partial<WorkflowSummary> = {}): WorkflowSummary => ({
  id: "0190f3a2-7b1c-7d4e-8f00-0000000000w1".replace("w", "a"),
  project_id: ID,
  sow_version_id: "0190f3a2-7b1c-7d4e-8f00-0000000000b1",
  name: "EMS RFQ costing v1",
  version: "1.0.0",
  state: "CREATED",
  created_by: "SYSTEM",
  created_at: "2026-10-12T10:00:00Z",
  row_version: 1,
  ...over,
});

describe("Project overview (E4.10)", () => {
  test("e4_10_t07 a project with no documents says 'Upload documents to start'", async () => {
    fakeApi(base(timeline("IDLE")));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    expect(await screen.findByText(EMPTY_OVERVIEW)).toBeDefined();
    const link = screen.getByRole("link", { name: "Upload documents" });
    expect(link.getAttribute("href")).toBe(`/projects/${ID}/documents`);
    expect(screen.queryByRole("button", { name: "Start processing" })).toBeNull();
  });

  test("e4_10_t06 no create or new Workflow action anywhere", async () => {
    fakeApi(base(timeline("WORKFLOW_CREATED", { parsed: 3 }), [workflow()]));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    expect(await screen.findByRole("link", { name: "Workflow v1.0.0" })).toBeDefined();
    for (const control of screen.getAllByRole("button").concat(screen.getAllByRole("link"))) {
      const name = control.textContent ?? "";
      expect(name).not.toMatch(/(create|new|add|generate)\b.*workflow|workflow.*\b(create|new)\b/i);
    }
    expect(screen.queryByRole("button", { name: /workflow/i })).toBeNull();
  });

  test("the Workflow list is read-only and empty until the SOW is approved", async () => {
    fakeApi(base(timeline("IDLE", { uploaded: 1 })));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    expect(await screen.findByText(EMPTY_WORKFLOWS)).toBeDefined();
  });

  test("shows the project name, its state and the document counts", async () => {
    fakeApi(base(timeline("IDLE", { uploaded: 2, parsed: 1, rejected: 1, failed: 0 })));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    expect(await screen.findByText(P.name)).toBeDefined();
    expect(screen.getByText("Idle")).toBeDefined();
    expect(screen.getByText("Uploaded").nextElementSibling?.textContent).toBe("2");
    expect(screen.getByText("Rejected").nextElementSibling?.textContent).toBe("1");
  });

  test("e4_10_t05 Start processing posts with an Idempotency-Key and then shows Ingesting", async () => {
    const api = fakeApi({
      ...base(timeline("IDLE", { uploaded: 2 })),
      [TIMELINE]: [
        { body: timeline("IDLE", { uploaded: 2 }) },
        { body: timeline("INGESTING", { uploaded: 2 }) },
      ],
      [START]: { status: 202, body: { run: {} } },
    });
    renderWithRefine(<OverviewScreen projectId={ID} />);
    const button = await screen.findByRole("button", { name: "Start processing" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(await screen.findByText("Processing is running.")).toBeDefined();
    const call = api.calls.find(
      (c) => c.method === "POST" && c.path === `/projects/${ID}/pipeline/start`,
    )!;
    expect((call.init.headers as Headers).get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Start processing" }) as HTMLButtonElement).disabled,
      ).toBe(true),
    );
  });

  test("a double click starts once", async () => {
    const api = fakeApi({
      ...base(timeline("IDLE", { uploaded: 1 })),
      [START]: { status: 202, body: { run: {} } },
    });
    renderWithRefine(<OverviewScreen projectId={ID} />);
    const button = await screen.findByRole("button", { name: "Start processing" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(1));
  });

  test("409 PIPELINE_RUNNING is shown as 'already running'", async () => {
    fakeApi({
      ...base(timeline("IDLE", { uploaded: 1 })),
      [START]: problem(409, "PIPELINE_RUNNING", "The pipeline is running"),
    });
    renderWithRefine(<OverviewScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start processing" }));
    expect(await screen.findByText("Processing is already running.")).toBeDefined();
  });

  test("409 NO_DOCUMENTS asks for an upload", async () => {
    fakeApi({
      ...base(timeline("IDLE", { uploaded: 1 })),
      [START]: problem(409, "NO_DOCUMENTS"),
    });
    renderWithRefine(<OverviewScreen projectId={ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start processing" }));
    expect(await screen.findByText(/Upload at least one document/)).toBeDefined();
  });

  test("a failed ingestion with documents can be started again", async () => {
    fakeApi(base(timeline("INGESTION_FAILED", { failed: 1 })));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    const button = await screen.findByRole("button", { name: "Start processing" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  test("while processing runs the button is disabled", async () => {
    fakeApi(base(timeline("KNOWLEDGE_BUILDING", { parsed: 2 })));
    renderWithRefine(<OverviewScreen projectId={ID} />);
    const button = await screen.findByRole("button", { name: "Start processing" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  test("a project the FDE is not assigned to is Not found", async () => {
    fakeApi({
      [PROJECTS]: { body: { items: [] } },
      [TIMELINE]: problem(404, "NOT_FOUND", "No such project"),
      [WORKFLOWS]: problem(404, "NOT_FOUND", "No such project"),
    });
    renderWithRefine(<OverviewScreen projectId={ID} />);
    expect(await screen.findByText("Not found")).toBeDefined();
    expect(screen.getByText("No such project")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Start processing" })).toBeNull();
  });
});
