import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

// The SOW screen is large and needs the app's data provider; this test is about where it renders.
vi.mock("@/app/(fde)/projects/[projectId]/sow/sow-screen", () => ({
  SowScreen: ({ projectId }: { projectId: string }) => <p>SOW screen for {projectId}</p>,
}));

import { HostSlot } from "@/workbench/host/slots";

import { ScopeHost } from "./scope-host";

afterEach(cleanup);

describe("the Scope screen renders the SOW screen into the workbench slot (#713)", () => {
  test("test_713_nothing_renders_until_the_workbench_shows_the_scope_slot", () => {
    render(<ScopeHost projectId="p1" />);
    expect(screen.queryByText(/SOW screen/)).toBeNull();
  });

  test("test_713_the_sow_screen_lands_inside_the_slot_for_the_open_project", () => {
    const { container } = render(
      <>
        <HostSlot id="scope" />
        <ScopeHost projectId="p1" />
      </>,
    );
    const slot = container.querySelector('[data-slot="scope"]');
    expect(slot?.textContent).toContain("SOW screen for p1");
  });
});
