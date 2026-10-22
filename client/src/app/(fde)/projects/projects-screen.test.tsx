import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";

import { MyProjectsScreen } from "./projects-screen";
import { EMPTY_PROJECTS } from "./workspace-api";

const LIST = "GET /projects";

afterEach(() => vi.unstubAllGlobals());

describe("My projects (E4.10)", () => {
  test("empty list says to ask the Admin", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    renderWithRefine(<MyProjectsScreen />);
    expect(await screen.findByText(EMPTY_PROJECTS)).toBeDefined();
  });

  test("each assigned project is a card that opens it, with its state", async () => {
    const a = project();
    const b = project({
      id: "0190f3a2-7b1c-7d4e-8f00-000000000002",
      name: "Cable assembly quote",
      client_name: null,
      pipeline_state: "INGESTING",
    });
    fakeApi({ [LIST]: { body: { items: [a, b] } } });
    renderWithRefine(<MyProjectsScreen />);
    const first = await screen.findByRole("link", { name: `Open ${a.name}` });
    expect(first.getAttribute("href")).toBe(`/projects/${a.id}`);
    expect(
      screen.getByRole("link", { name: "Open Cable assembly quote" }).getAttribute("href"),
    ).toBe(`/projects/${b.id}`);
    expect(screen.getByText(a.client_name!)).toBeDefined();
    expect(screen.getByText("Idle")).toBeDefined();
    expect(screen.getByText("Ingesting")).toBeDefined();
  });

  test("a failed request shows the problem detail with a retry", async () => {
    fakeApi({ [LIST]: problem(503, "ORCHESTRATOR_UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<MyProjectsScreen />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("project and client names are shown as text, never as markup", async () => {
    const evil = project({ name: "<img src=x onerror=alert(1)>", client_name: "<b>Acme</b>" });
    fakeApi({ [LIST]: { body: { items: [evil] } } });
    const { container } = renderWithRefine(<MyProjectsScreen />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeDefined();
    expect(container.querySelector("img[src='x']")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });
});
