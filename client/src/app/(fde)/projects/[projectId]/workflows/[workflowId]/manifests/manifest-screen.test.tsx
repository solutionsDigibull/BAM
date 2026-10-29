import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";

import { ManifestScreen } from "./manifest-screen";
import { hashOf, NOT_GENERATED, pretty } from "./manifests-api";

// Monaco needs a real browser (workers, canvas): a plain read-only <pre> stands in for it, with the
// same props. What Monaco renders is checked end to end in e2e/manifests.spec.ts.
vi.mock("./monaco-viewer", () => ({
  default: ({ path, value, original }: { path: string; value: string; original?: string }) => (
    <div
      role="region"
      aria-label={original === undefined ? `${path} (read only)` : `Diff of ${path}`}
    >
      {original !== undefined ? <pre data-testid="original">{original}</pre> : null}
      <pre data-testid="viewer">{value}</pre>
    </div>
  ),
}));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const S = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const BASE = `/projects/${P}/workflows/${W}`;
const HASH = `sha256:${"ab".repeat(32)}`;
const XSS = "<img src=x onerror=alert(1)>";

const manifest = (patch = 0) => ({
  skill_metadata: { skill_id: "MS01.1_Login", skill_name: XSS },
  isatvon_definition: { i_instructions: `Log in (rev ${patch}).` },
  srim_runtime_definition: {},
  contracts: {},
  governance: { deterministic_hash: HASH, versioning: { major: 1, minor: 0, patch } },
  sbam_build_record: {},
});
const version = (build_no: number, over: object = {}) => ({
  version: "1.0.0",
  sha256: HASH,
  build_no,
  cycle: 1,
  attempt: 0,
  is_frozen: false,
  created_at: "2026-10-29T10:00:00Z",
  ...over,
});
const view = (build_no: number, over: object = {}) => ({
  ...version(build_no),
  schema_version: "manifest/skill_manifest@1.0",
  reused: false,
  manifest: manifest(build_no - 1),
  ...over,
});
const tree = {
  workflow_id: W,
  workflow_state: "ISATVON_REVIEW",
  sow_approved: true,
  macros: [
    {
      id: "m1",
      macro_category: "MS01_Access",
      name: "Access",
      description: "",
      order: 1,
      sub_skills: [
        {
          id: S,
          macro_id: "m1",
          state: "VALID",
          content_sha256: "c".repeat(64),
          sub_skill: { skill_metadata: { skill_id: "MS01.1_Login", skill_name: "Login" } },
        },
      ],
    },
  ],
};
const routes = (over: Record<string, unknown> = {}) => ({
  [`GET ${BASE}/macro-skills`]: { body: tree },
  [`GET ${BASE}/sub-skills/${S}/manifests`]: { body: { items: [version(2), version(1)] } },
  [`GET ${BASE}/sub-skills/${S}/manifest`]: { body: view(2) },
  [`GET /schemas/manifest/skill_manifest`]: { body: { $id: "urn:x", properties: {} } },
  ...over,
});
const show = (sub: string | null = S) =>
  renderWithRefine(<ManifestScreen projectId={P} workflowId={W} subId={sub} />);

describe("manifest viewer rules (E9.6)", () => {
  test("e9_6_t02 Copy hash copies the manifest's own deterministic_hash, prefix included", () => {
    expect(hashOf(view(1) as never)).toBe(HASH);
    const wf = {
      ...view(1),
      manifest: { integrity: { content_sha256: "sha256:" + "9".repeat(64) } },
    };
    expect(hashOf(wf as never)).toBe("sha256:" + "9".repeat(64));
  });

  test("e9_6_t03 key-sorted JSON: two versions differ only where a value changed", () => {
    const a = pretty(manifest(0)).split("\n");
    const b = pretty({ ...manifest(1), governance: manifest(0).governance }).split("\n");
    const changed = a.filter((line, i) => line !== b[i]);
    expect(changed).toEqual(['    "i_instructions": "Log in (rev 0)."']);
  });
});

describe("manifest viewer screen (E9.6)", () => {
  test("e9_6_t01 the newest manifest with its six blocks, version, hash and draft marker", async () => {
    fakeApi(routes());
    show();
    const viewer = await screen.findByTestId("viewer");
    const shown = JSON.parse(viewer.textContent ?? "{}") as Record<string, unknown>;
    expect(Object.keys(shown)).toHaveLength(6);
    expect(screen.getByTestId("manifest-header").textContent).toContain("v1.0.0");
    expect(screen.getByText("Draft")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy hash" })).toBeTruthy();
  });

  test("e9_6_t04 read only: no editable field, and only GET requests are sent", async () => {
    const { calls } = fakeApi(routes());
    const { container } = show();
    await screen.findByTestId("viewer");
    expect(container.querySelector("textarea, [contenteditable='true']")).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  test("e9_6_t05 manifest text is shown as text, never as HTML", async () => {
    fakeApi(routes());
    const { container } = show();
    const viewer = await screen.findByTestId("viewer");
    expect(viewer.textContent).toContain(XSS);
    expect(container.querySelector("img")).toBeNull();
  });

  test("e9_6_t07 no manifest yet: 'Not generated yet' with a link to the Workbench", async () => {
    fakeApi(routes({ [`GET ${BASE}/sub-skills/${S}/manifests`]: { body: { items: [] } } }));
    show();
    expect(await screen.findByText(NOT_GENERATED)).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open the Workbench" });
    expect(link.getAttribute("href")).toBe(`/projects/${P}/workflows/${W}/isatvon`);
  });

  test("e9_6_t06 a 404 (other tenant, unassigned) shows not found and no manifest", async () => {
    fakeApi(routes({ [`GET ${BASE}/sub-skills/${S}/manifests`]: problem(404, "NOT_FOUND") }));
    show();
    await waitFor(() => expect(screen.queryByText(/not found/i)).toBeTruthy());
    expect(screen.queryByTestId("viewer")).toBeNull();
  });

  test("403 shows the forbidden state", async () => {
    fakeApi(routes({ [`GET ${BASE}/sub-skills/${S}/manifests`]: problem(403, "FORBIDDEN") }));
    show();
    await waitFor(() =>
      expect(screen.queryByText(/not allowed|forbidden|permission/i)).toBeTruthy(),
    );
    expect(screen.queryByTestId("viewer")).toBeNull();
  });

  test("a reused Sub-Skill is marked", async () => {
    fakeApi(
      routes({ [`GET ${BASE}/sub-skills/${S}/manifest`]: { body: view(2, { reused: true }) } }),
    );
    show();
    expect(await screen.findByText("Reused")).toBeTruthy();
  });

  test("without a Sub-Skill it asks for one; the Workflow switch shows the Workflow manifest", async () => {
    fakeApi(
      routes({
        [`GET ${BASE}/manifests`]: {
          body: { items: [version(1, { cycle: null, attempt: null })] },
        },
        [`GET ${BASE}/manifest`]: {
          body: view(1, {
            schema_version: "sbam-workflow-manifest/1.0",
            manifest: { version: "1.0.0", integrity: { content_sha256: HASH } },
          }),
        },
        [`GET /schemas/manifest/workflow_manifest`]: { body: { $id: "urn:w" } },
      }),
    );
    show(null);
    // the Workflow manifest by default
    expect(
      await screen.findByRole("region", { name: "workflow_manifest.json (read only)" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("Sub-Skill"));
    expect(await screen.findByText("Choose a Sub-Skill to see its manifest")).toBeTruthy();
  });
});
