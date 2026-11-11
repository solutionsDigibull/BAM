import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { Snapshot } from "../api/types";

// The Orb draws with WebGL, which jsdom does not have.
vi.mock("./reactbits/Orb", () => ({ default: () => null }));
// assistant-ui watches the thread viewport's size, and jsdom has no ResizeObserver.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

import { getGoal, resetGoal, skipGoal } from "../lib/goal";
import { setUi } from "../lib/store";
import { AssistantPane } from "./AssistantPane";

// The pane reads the upload rows, whose Retry runs through useAction (needs the app's query client).
const render = (ui: React.ReactElement) => rtlRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

const snap = { uploads: [], sowGeneration: [], persist: {}, steps: [], testHistory: [], versions: [], scanEvents: [] } as unknown as Snapshot;

let n = 0;
beforeEach(() => {
  // The thread and the goal are kept per project (from the URL), so each test gets its own project.
  window.history.pushState(null, "", `/projects/t${++n}/workbench`);
  localStorage.clear();
  sessionStorage.clear();
  resetGoal();
  skipGoal(); // a project that already has work resumes: no goal box
});

afterEach(() => {
  cleanup();
  setUi((s) => ({ shell: { ...s.shell, assistant: false } }));
});

describe("the Enterprise Assistant pane (#711)", () => {
  test("test_711_the_empty_pane_greets_notes_the_screen_and_offers_chips", () => {
    render(<AssistantPane snap={snap} screenId="1.1" />);
    expect(screen.getByText("How can I help you today?")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Add your first files");
    expect(screen.getByRole("button", { name: "What is next?" })).toBeTruthy();
    expect(screen.getByLabelText("Message the Enterprise Assistant")).toBeTruthy();
  });

  test("test_711_a_chip_posts_the_question_and_the_reply_comes_from_the_project_state", async () => {
    render(<AssistantPane snap={snap} screenId="1.1" />);
    fireEvent.click(screen.getByRole("button", { name: "Upload status" }));
    await waitFor(() => expect(screen.getByText("No files uploaded yet.")).toBeTruthy());
    expect(screen.getByText("Upload status")).toBeTruthy();
  });

  test("test_711_hide_assistant_leaves_a_rail_button_that_brings_it_back", () => {
    render(<AssistantPane snap={snap} screenId="1.1" />);
    fireEvent.click(screen.getByRole("button", { name: "Hide assistant" }));
    expect(screen.queryByLabelText("Message the Enterprise Assistant")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show assistant" }));
    expect(screen.getByLabelText("Message the Enterprise Assistant")).toBeTruthy();
  });

  test("test_711_the_conversation_is_kept_when_the_pane_remounts", async () => {
    const first = render(<AssistantPane snap={snap} screenId="1.1" />);
    fireEvent.change(screen.getByLabelText("Message the Enterprise Assistant"), { target: { value: "Remount marker question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(screen.getAllByText("Remount marker question").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull(), { timeout: 5000 });
    first.unmount();
    render(<AssistantPane snap={snap} screenId="3.1" />);
    expect(screen.getAllByText("Remount marker question").length).toBeGreaterThan(0);
  });

  test("test_719_a_new_project_asks_for_the_goal_on_upload_and_saving_it_opens_the_chat", async () => {
    resetGoal();
    render(<AssistantPane snap={snap} screenId="1.1" />);
    expect(screen.getByText(/Name the agent and let the/)).toBeTruthy();
    expect(screen.queryByLabelText("Message the Enterprise Assistant")).toBeNull();
    const box = screen.getByLabelText("Agent goal") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: `${box.value}quoting for sales managers in India` } });
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} })); // reduced motion: no fly animation
    fireEvent.click(screen.getByRole("button", { name: "Send goal" }));
    await waitFor(() => expect(getGoal().saved).toBe(true));
    expect(getGoal().text).toBe("Build an AI agent that automates quoting for sales managers in India");
    expect(screen.getByLabelText("Message the Enterprise Assistant")).toBeTruthy();
    expect(screen.getByText("Build an AI agent that automates quoting for sales managers in India")).toBeTruthy();
    expect(screen.getByText(/Saved on this device for this project/)).toBeTruthy();
  });

  test("test_719_the_goal_box_only_shows_on_upload", () => {
    resetGoal();
    render(<AssistantPane snap={snap} screenId="3.1" />);
    expect(screen.queryByText(/Name the agent and let the/)).toBeNull();
    expect(screen.getByText("How can I help you today?")).toBeTruthy();
  });
});
