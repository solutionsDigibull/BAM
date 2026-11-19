import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildsResponse, ManifestVersionList, ManifestView } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { ManifestsPanel } from "./ManifestsPanel";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const BUILDS: BuildsResponse = {
  workflow_id: "w1", workflow_state: "BUILDING", active: false,
  sub_skills: [{ sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "CERTIFIED", stage: "done", attempt: 0, gates: {} }],
};
const V = (version: string, build_no: number, frozen = false) => ({ version, sha256: `${build_no}`.repeat(64).slice(0, 64), build_no, cycle: 0, attempt: 0, is_frozen: frozen, created_at: "2026-11-18T10:00:00Z" });
const LIST: ManifestVersionList = { items: [V("1.0.2", 2, true), V("1.0.1", 1)] };
const VIEW = (version: string): ManifestView => ({ ...V(version, version === "1.0.2" ? 2 : 1), schema_version: "1", manifest: { zeta: 1, alpha: { b: "<script>x</script>", a: 2 }, governance: { deterministic_hash: "sha256:abcdef0123456789" } } });

const install = (over: Partial<BamApi> = {}) =>
  setBamApi({
    getWorkflowBuilds: async () => BUILDS,
    getManifestVersions: async () => LIST,
    getManifest: async (_w: string, _s: string | undefined, _b?: number, version?: string) => VIEW(version ?? "1.0.2"),
    ...over,
  } as unknown as BamApi);
const renderPanel = (id = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <ManifestsPanel workflowId={id} />
      </Refine>
    </div>,
  );
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the manifests in Translate (#715)", () => {
  test("test_715_the_workflow_manifest_lists_versions_newest_first_and_shows_the_newest_as_text", async () => {
    const getManifest = vi.fn(async (_w: string, _s: string | undefined, _b?: number, version?: string) => VIEW(version ?? "1.0.2"));
    install({ getManifest });
    renderPanel();
    const list = await screen.findByRole("list", { name: "Versions" });
    const rows = list.querySelectorAll("li");
    expect(rows[0]!.textContent).toContain("v1.0.2");
    expect(rows[0]!.textContent).toContain("Frozen");
    expect(rows[0]!.textContent).toContain("build 2 · cycle 0 · attempt 0");
    const json = await screen.findByLabelText("Manifest v1.0.2");
    expect(json.textContent!.indexOf('"alpha"')).toBeLessThan(json.textContent!.indexOf('"zeta"'));
    expect(json.textContent).toContain("<script>x</script>");
    expect(json.querySelector("script")).toBeNull();
    expect(getManifest.mock.calls[0]![1]).toBeUndefined();
  });

  test("test_715_another_version_can_be_opened", async () => {
    install();
    renderPanel();
    await screen.findByLabelText("Manifest v1.0.2");
    fireEvent.click(screen.getByRole("button", { name: /v1\.0\.1/ }));
    expect(await screen.findByLabelText("Manifest v1.0.1")).toBeTruthy();
  });

  test("test_715_a_sub_skill_can_be_picked_and_its_manifests_are_read", async () => {
    const getManifestVersions = vi.fn(async () => LIST);
    install({ getManifestVersions });
    renderPanel();
    await screen.findByRole("list", { name: "Versions" });
    fireEvent.change(screen.getByLabelText("Manifest of"), { target: { value: SUB } });
    await waitFor(() => expect(getManifestVersions).toHaveBeenCalledWith("w1", SUB));
  });

  test("test_715_the_panel_has_no_edit_save_or_approve_action", async () => {
    install();
    renderPanel();
    await screen.findByLabelText("Manifest v1.0.2");
    expect(screen.queryByRole("button", { name: /save|approve|edit/i })).toBeNull();
    expect(document.querySelector("textarea")).toBeNull();
    expect(screen.getAllByText("Read only").length).toBeGreaterThan(0);
  });

  test("test_715_no_manifest_yet_is_the_empty_state", async () => {
    install({ getManifestVersions: async () => ({ items: [] }) });
    renderPanel();
    expect(await screen.findByText("Not generated yet")).toBeTruthy();
  });

  test("test_715_a_failed_load_shows_the_detail_and_a_retry", async () => {
    install({ getManifestVersions: async () => { throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "the store is down" } }); } });
    renderPanel();
    expect((await screen.findByText("the store is down")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("test_715_another_tenants_workflow_is_the_forbidden_state", async () => {
    install({ getManifestVersions: async () => { throw Object.assign(new Error("x"), { statusCode: 404 }); } });
    renderPanel();
    expect(await screen.findByText("You can't see these manifests")).toBeTruthy();
  });

  test("test_715_no_workflow_is_the_empty_state", async () => {
    install();
    renderPanel("");
    expect(await screen.findByText("No manifests yet")).toBeTruthy();
  });
});
