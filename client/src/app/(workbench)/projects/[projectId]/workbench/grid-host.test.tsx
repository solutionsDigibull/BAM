import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// The grid screen is large and needs the app's data provider; this test is about which workflow it is given and where it renders.
vi.mock("@/app/(fde)/projects/[projectId]/workflows/[workflowId]/isatvon/workbench-screen", () => ({
  WorkbenchScreen: ({ projectId, workflowId }: { projectId: string; workflowId: string }) => (
    <p>
      grid for {projectId} / {workflowId}
    </p>
  ),
}));

const list = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("@refinedev/core", () => ({ useCustom: () => list.value }));

import { HostSlot } from "@/workbench/host/slots";

import { GridHost, NO_WORKFLOW, currentWorkflow } from "./grid-host";

const wf = (id: string, created_at: string) => ({ id, created_at }) as never;
const answer = (over: Record<string, unknown>) => {
  list.value = {
    query: { isLoading: false, error: null, refetch: vi.fn() },
    result: { data: { items: [] } },
    ...over,
  };
};

beforeEach(() => answer({}));
afterEach(cleanup);

const show = () =>
  render(
    <>
      <HostSlot id="grid" />
      <GridHost projectId="p1" />
    </>,
  );

describe("the Grid host in the workbench (#714)", () => {
  test("test_714_the_newest_workflow_is_the_one_shown", () => {
    expect(
      currentWorkflow([wf("old", "2026-11-01T00:00:00Z"), wf("new", "2026-11-09T00:00:00Z")])?.id,
    ).toBe("new");
    expect(currentWorkflow([])).toBeUndefined();
  });

  test("test_714_the_grid_renders_inside_the_workbench_slot_for_the_projects_workflow", async () => {
    answer({ result: { data: { items: [wf("w1", "2026-11-09T00:00:00Z")] } } });
    const { container } = show();
    expect((await screen.findByText("grid for p1 / w1")).closest(".host-slot")).toBe(
      container.querySelector(".host-slot"),
    );
  });

  test("test_714_before_the_sow_is_approved_there_is_no_workflow_and_no_create_action", async () => {
    show();
    expect(await screen.findByText(NO_WORKFLOW)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /create|new workflow/i })).toBeNull();
  });

  test("test_714_loading_shows_a_skeleton_not_the_empty_message", async () => {
    answer({ query: { isLoading: true, error: null, refetch: vi.fn() } });
    const { container } = show();
    await vi.waitFor(() => expect(container.querySelector(".ant-skeleton")).not.toBeNull());
    expect(screen.queryByText(NO_WORKFLOW)).toBeNull();
  });

  test("test_714_a_failed_load_shows_the_error_not_the_empty_message", async () => {
    answer({
      query: { isLoading: false, error: { statusCode: 500, message: "boom" }, refetch: vi.fn() },
    });
    show();
    expect(await screen.findByText("boom")).toBeTruthy();
    expect(screen.queryByText(NO_WORKFLOW)).toBeNull();
  });

  test("test_714_a_forbidden_project_shows_the_forbidden_state", async () => {
    answer({
      query: { isLoading: false, error: { statusCode: 403, message: "no" }, refetch: vi.fn() },
    });
    show();
    expect(await screen.findByText("Forbidden")).toBeTruthy();
  });

  test("test_714_nothing_renders_when_the_grid_is_not_on_show", () => {
    const { container } = render(<GridHost projectId="p1" />);
    expect(container.innerHTML).toBe("");
  });
});
