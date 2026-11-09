import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";

import { EMPTY_PROJECTS } from "./project-state";
import { ProjectsLanding } from "./projects-landing";

const LIST = "GET /projects";
const show = (signOut = vi.fn(async () => {})) =>
  renderWithRefine(<ProjectsLanding user="fde@example.com" signOut={signOut} />);

afterEach(() => vi.unstubAllGlobals());

describe("the FDE project landing (#719)", () => {
  test("test_719_no_assigned_project_says_to_ask_the_admin_and_offers_no_create", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    show();
    expect(await screen.findByText(EMPTY_PROJECTS)).toBeDefined();
    expect(screen.queryByRole("button", { name: /create|new project/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /create|new project/i })).toBeNull();
  });

  test("test_719_every_assigned_project_is_listed_even_when_there_is_only_one", async () => {
    const a = project();
    fakeApi({ [LIST]: { body: { items: [a] } } });
    show();
    expect(await screen.findByRole("link", { name: `Open ${a.name}` })).toBeDefined();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  test("test_719_a_card_opens_the_project_in_the_workbench_and_says_start_or_resume", async () => {
    const fresh = project({ name: "Brand new", pipeline_state: "IDLE" });
    const busy = project({
      id: "0190f3a2-7b1c-7d4e-8f00-000000000002",
      name: "Cable assembly quote",
      client_name: null,
      pipeline_state: "INGESTING",
    });
    fakeApi({ [LIST]: { body: { items: [fresh, busy] } } });
    show();
    const first = await screen.findByRole("link", { name: "Open Brand new" });
    expect(first.getAttribute("href")).toBe(`/projects/${fresh.id}/workbench`);
    expect(first.textContent).toBe("Start");
    const second = screen.getByRole("link", { name: "Open Cable assembly quote" });
    expect(second.getAttribute("href")).toBe(`/projects/${busy.id}/workbench`);
    expect(second.textContent).toBe("Resume");
    expect(screen.getByText("Not started")).toBeDefined();
    expect(screen.getByText("Processing documents")).toBeDefined();
  });

  test("test_719_a_failed_request_shows_the_problem_detail_with_a_retry", async () => {
    fakeApi({ [LIST]: problem(503, "ORCHESTRATOR_UNAVAILABLE", "Try again shortly") });
    show();
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  test("test_719_a_forbidden_answer_says_so_and_offers_no_retry", async () => {
    fakeApi({ [LIST]: problem(403, "FORBIDDEN", "nope") });
    show();
    expect(await screen.findByText("You do not have access to these projects.")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  test("test_719_project_and_client_names_are_shown_as_text_never_as_markup", async () => {
    const evil = project({ name: "<img src=x onerror=alert(1)>", client_name: "<b>Acme</b>" });
    fakeApi({ [LIST]: { body: { items: [evil] } } });
    const { container } = show();
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeDefined();
    expect(container.querySelector("img[src='x']")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  test("test_719_the_signed_in_user_and_a_sign_out_are_shown", async () => {
    fakeApi({ [LIST]: { body: { items: [] } } });
    show();
    expect(await screen.findByText("fde@example.com")).toBeDefined();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeDefined();
  });
});
