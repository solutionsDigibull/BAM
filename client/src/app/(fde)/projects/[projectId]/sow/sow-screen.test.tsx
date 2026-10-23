import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";

import fixture from "../../../../../../e2e/fixtures/sow-ms01.json";
import { EMPTY_SOW, GENERATING } from "./sow-api";
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
