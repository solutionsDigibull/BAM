import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";

import fixture from "../../../../../../e2e/fixtures/sow-ms01.json";
import { EMPTY_SOW, FAILED_NO_VERSION, GENERATING } from "./sow-api";
import { readDraft, writeDraft } from "./sow-draft";
import { SowScreen } from "./sow-screen";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const P = project({ pipeline_state: "SOW_REVIEW" });
const ID = P.id;
const PROJECTS = "GET /projects";
const SOW = `GET /projects/${ID}/sow`;
const VERSIONS = `GET /projects/${ID}/sow/versions`;
const V1 = `GET /projects/${ID}/sow/versions/1`;

const summary = (status = "IN_REVIEW") => ({
  id: "0190f3a2-7b1c-7d4e-8f00-000000000101",
  version: 1,
  status,
  source: "GENERATED",
  sha256: status === "GENERATION_FAILED" ? null : "ab".repeat(32),
  parent_version: null,
  knowledge_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000c0a1",
  error_reason: status === "GENERATION_FAILED" ? "LLM_UNAVAILABLE" : null,
  author: null,
  created_at: "2026-10-22T10:00:00Z",
});
const overview = (status = "IN_REVIEW") => ({
  body: {
    sow_id: "0190f3a2-7b1c-7d4e-8f00-00000000a0a1",
    current: summary(status),
    approved: null,
    etag: '"1"',
  },
});
const detail = (status = "IN_REVIEW") => ({
  body: {
    ...summary(status),
    content_tiptap: status === "GENERATION_FAILED" ? null : fixture.content_tiptap,
    structured: null,
    citations: [],
  },
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

const render = () => renderWithRefine(<SowScreen projectId={ID} />);

describe("SOW screen states (E6.5)", () => {
  test("loading shows the skeleton while the SOW is fetched", () => {
    fakeApi({ [PROJECTS]: { body: { items: [P] } }, [SOW]: () => new Promise(() => {}) as never });
    render();
    expect(document.querySelector(".ant-skeleton")).not.toBeNull();
  });

  test("empty: no SOW yet", async () => {
    fakeApi({ [PROJECTS]: { body: { items: [P] } }, [SOW]: problem(404, "NOT_FOUND") });
    render();
    expect(await screen.findByText(EMPTY_SOW)).toBeDefined();
  });

  test("generating: no SOW yet while the pipeline writes it", async () => {
    const generating = project({ pipeline_state: "SOW_GENERATING" });
    fakeApi({ [PROJECTS]: { body: { items: [generating] } }, [SOW]: problem(404, "NOT_FOUND") });
    render();
    expect(await screen.findByText(GENERATING)).toBeDefined();
  });

  test("error: the problem detail is shown", async () => {
    fakeApi({
      [PROJECTS]: { body: { items: [P] } },
      [SOW]: problem(500, "INTERNAL", "Something broke on our side"),
    });
    render();
    expect(await screen.findByText(/Something broke on our side/)).toBeDefined();
  });

  test("forbidden: a 403 says so", async () => {
    fakeApi({
      [PROJECTS]: { body: { items: [P] } },
      [SOW]: problem(403, "WRONG_PORTAL", "FDEs only"),
    });
    render();
    await waitFor(() => expect(document.body.textContent).toMatch(/403|forbidden|FDEs only/i));
  });

  test("generation failed: the reason and Regenerate", async () => {
    fakeApi({
      [PROJECTS]: { body: { items: [P] } },
      [SOW]: overview("GENERATION_FAILED"),
      [VERSIONS]: { body: { items: [summary("GENERATION_FAILED")] } },
      [V1]: detail("GENERATION_FAILED"),
    });
    render();
    const failed = await screen.findByTestId("sow-failed", {}, { timeout: 5000 }); // 3 requests in turn
    expect(failed.textContent).toContain("LLM_UNAVAILABLE");
    expect(screen.getAllByRole("button", { name: "Regenerate" }).length).toBeGreaterThan(0);
  });

  test("#376 failed before any version: the failure state and a working Regenerate", async () => {
    const failed = project({ pipeline_state: "SOW_GENERATION_FAILED" });
    const generating = { ...failed, pipeline_state: "SOW_GENERATING" };
    const api = fakeApi({
      [PROJECTS]: [{ body: { items: [failed] } }, { body: { items: [generating] } }],
      [SOW]: problem(404, "NOT_FOUND"),
      [`POST /projects/${ID}/sow/regenerate`]: {
        status: 202,
        body: { run_id: "r1", workflow_id: "w1", status: "RUNNING" },
      },
    });
    render();
    const panel = await screen.findByTestId("sow-failed");
    expect(panel.textContent).toContain(FAILED_NO_VERSION);
    fireEvent.click(within(panel).getByRole("button", { name: "Regenerate" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Regenerate" }));
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/sow/regenerate"))).toBe(
        true,
      ),
    );
    const post = api.calls.find((c) => c.method === "POST")!;
    expect(new Headers(post.init.headers).get("idempotency-key")).toBeTruthy();
    expect(await screen.findByText(GENERATING)).toBeDefined(); // the project now says SOW_GENERATING
  });

  test("#376 failed with a SOW row but no current version shows the same failure state", async () => {
    const failed = project({ pipeline_state: "SOW_GENERATION_FAILED" });
    fakeApi({
      [PROJECTS]: { body: { items: [failed] } },
      [SOW]: { body: { sow_id: "s1", current: null, approved: null, etag: '"0"' } },
      [VERSIONS]: { body: { items: [] } },
    });
    render();
    const panel = await screen.findByTestId("sow-failed");
    expect(within(panel).getByRole("button", { name: "Regenerate" })).toBeDefined();
  });

  test("#376 generating with a SOW row but no current version shows the generating state", async () => {
    const generating = project({ pipeline_state: "SOW_GENERATING" });
    fakeApi({
      [PROJECTS]: { body: { items: [generating] } },
      [SOW]: { body: { sow_id: "s1", current: null, approved: null, etag: '"0"' } },
      [VERSIONS]: { body: { items: [] } },
    });
    render();
    expect(await screen.findByText(GENERATING)).toBeDefined();
    expect(screen.queryByTestId("sow-failed")).toBeNull();
  });

  test("#376 an earlier pipeline state with no SOW still shows the empty state", async () => {
    const earlier = project({ pipeline_state: "KNOWLEDGE_BUILDING" });
    fakeApi({ [PROJECTS]: { body: { items: [earlier] } }, [SOW]: problem(404, "NOT_FOUND") });
    render();
    expect(await screen.findByText(EMPTY_SOW)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
  });

  test("session expiry: edits kept from before a sign-in are offered back", async () => {
    writeDraft(ID, 1, fixture.content_tiptap);
    fakeApi({
      [PROJECTS]: { body: { items: [P] } },
      [SOW]: overview(),
      [VERSIONS]: { body: { items: [summary()] } },
      [V1]: detail(),
    });
    render();
    const banner = await screen.findByTestId("sow-restore", {}, { timeout: 5000 });
    expect(banner.textContent).toContain("unsaved edits to v1");
    expect(screen.getByRole("button", { name: "Restore my edits" })).toBeDefined();
    screen.getByRole("button", { name: "Discard" }).click();
    await waitFor(() => expect(screen.queryByTestId("sow-restore")).toBeNull());
    expect(readDraft(ID)).toBeNull();
  });
});
