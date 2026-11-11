import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { fakeApi, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import type { WorkflowSummary } from "@/contracts";

import { LIFECYCLE, isFailure, nextStep, stepIndex } from "./[workflowId]/workflow-steps";
import { WorkflowOverview } from "./[workflowId]/workflow-overview";
import { WorkflowsScreen } from "./workflows-screen";

const P = "0190f3a2-7b1c-7d4e-8f00-00000000a001";
const W = "0190f3a2-7b1c-7d4e-8f00-00000000b001";
const GET = `GET /projects/${P}/workflows`;

const wf = (over: Partial<WorkflowSummary> = {}): WorkflowSummary => ({
  id: W,
  project_id: P,
  sow_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000c001",
  name: "project test v1",
  version: "1.0.0",
  state: "ISATVON_REVIEW",
  created_by: "SYSTEM",
  created_at: "2026-11-06T20:12:13Z",
  row_version: 3,
  ...over,
});

describe("Workflows list", () => {
  test("lists each workflow with its status and the next thing to do", async () => {
    fakeApi({ [GET]: { body: { items: [wf()] } } });
    renderWithRefine(<WorkflowsScreen projectId={P} />);
    expect(await screen.findByText("project test v1")).toBeDefined();
    const next = screen.getByText("Review the breakdown");
    expect(next.getAttribute("href")).toBe(`/projects/${P}/workflows/${W}/isatvon`);
  });

  test("explains the empty state", async () => {
    fakeApi({ [GET]: { body: { items: [] } } });
    renderWithRefine(<WorkflowsScreen projectId={P} />);
    expect(await screen.findByText(/after the SOW is approved/)).toBeDefined();
  });
});

describe("Workflow overview", () => {
  test("shows the state, the call to action and the stages", async () => {
    fakeApi({ [GET]: { body: { items: [wf({ state: "BUILDING" })] } } });
    renderWithRefine(<WorkflowOverview projectId={P} workflowId={W} />);
    expect(await screen.findByText("project test v1")).toBeDefined();
    expect(screen.getByText(/generated and run in the sandbox/)).toBeDefined();
    expect(screen.getByText("Open build & test").closest("a")?.getAttribute("href")).toBe(
      `/projects/${P}/workflows/${W}/build`,
    );
    expect(screen.getByText("Certification")).toBeDefined();
  });

  test("a workflow that is not in the project is said so, not a blank page", async () => {
    fakeApi({ [GET]: { body: { items: [wf({ id: "0190f3a2-7b1c-7d4e-8f00-00000000b999" })] } } });
    renderWithRefine(<WorkflowOverview projectId={P} workflowId={W} />);
    expect(await screen.findByText(/was not found in the project/)).toBeDefined();
  });
});

describe("Workflow steps", () => {
  test("every state has a step index and a next action or none on purpose", () => {
    expect(stepIndex("ISATVON_REVIEW")).toBe(0);
    expect(stepIndex("BUILDING")).toBe(1);
    expect(stepIndex("CERTIFIED")).toBe(2);
    expect(stepIndex("FROZEN")).toBe(LIFECYCLE.length - 1);
    expect(stepIndex("CREATED")).toBe(-1);
    expect(nextStep("CANCELLED")).toBeUndefined();
    expect(nextStep("FROZEN")?.path).toBe("versions");
  });

  test("failures are marked", () => {
    expect(isFailure("EXTRACTION_FAILED")).toBe(true);
    expect(isFailure("WORKFLOW_VALIDATION_FAILED")).toBe(true);
    expect(isFailure("BUILDING")).toBe(false);
  });
});
