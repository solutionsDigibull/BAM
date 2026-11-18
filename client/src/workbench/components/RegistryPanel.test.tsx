import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { RegisteredWorkflow } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { RegistryPanel } from "./RegistryPanel";

const DIGEST = "sha256:" + "ab".repeat(32);
const MANIFEST = "cd".repeat(32);
const tool = (name: string, role: "action" | "check") => ({ name, skill_id: "MS01.1", role, input_schema: {}, output_schema: {} });
const ENTRY = {
  workflow_id: "w1", version: "1.0.2", registry_name: "wf_onboarding_v1_2", registry_id: "reg-123", image_digest: DIGEST,
  manifest_hash: MANIFEST, certification_id: "0190f3a2-7b1c-7d4e-8f00-0000000000c9", owner: "fde-a1",
  tools: [tool("MS01_1__action", "action"), tool("MS01_1__check", "check")], status: "REGISTERED", registered_at: "2026-11-17T10:00:00Z",
} as unknown as RegisteredWorkflow;
const OLD = { ...ENTRY, version: "1.0.1", registry_name: "wf_onboarding_v1_1", status: "SUPERSEDED", superseded_at: "2026-11-17T12:00:00Z", superseded_by: "1.0.2" } as unknown as RegisteredWorkflow;
const missing = () => Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 }));

const install = (get: BamApi["getWorkflowRegistration"]) => setBamApi({ getWorkflowRegistration: get } as unknown as BamApi);
const renderPanel = (id: string | undefined = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <RegistryPanel workflowId={id} />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("Translate shows the workflow's registry entry, read only (#849)", () => {
  test("test_849_the_current_entry_shows_name_version_digest_hash_certification_owner_and_tools", async () => {
    install(async () => ENTRY);
    renderPanel();
    const card = await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_2" });
    expect(card.textContent).toContain("Current");
    expect(card.textContent).toContain("1.0.2");
    expect(card.textContent).toContain("sha256:abababababab"); // the short image digest
    expect(card.textContent).toContain("sha256:cdcdcdcdcdcd"); // the short manifest hash
    expect(card.textContent).toContain("0190f3a2-7b1c-7d4e-8f00-0000000000c9");
    expect(card.textContent).toContain("fde-a1");
    expect(card.textContent).toContain("reg-123");
    const tools = within(card).getByRole("list", { name: "Tools" });
    expect(tools.textContent).toContain("MS01_1__action");
    expect(tools.textContent).toContain("MS01_1__check");
    expect(card.textContent).toContain("1 action and 1 check tools");
    expect(screen.queryByText(/Superseded/)).toBeNull();
  });

  test("test_849_a_superseded_entry_says_so_and_offers_the_version_that_replaced_it", async () => {
    const get = vi.fn(async (_id: string, version?: string) => (version === "1.0.2" ? ENTRY : OLD));
    install(get);
    renderPanel();
    // No version asked for: the lookup answers with the old one here only because the double does; the banner is what matters.
    const card = await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_1" });
    expect(card.textContent).toContain("Superseded");
    expect(card.textContent).toMatch(/by version 1\.0\.2/);
    expect(card.textContent).toMatch(/disabled in Bifrost but stays listed/);
    fireEvent.click(screen.getByRole("button", { name: "Show v1.0.2" }));
    expect(await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_2" })).toBeTruthy();
    expect(get).toHaveBeenLastCalledWith("w1", "1.0.2");
  });

  test("test_849_a_workflow_with_no_entry_is_not_registered_yet_not_an_error", async () => {
    install(missing);
    renderPanel();
    expect(await screen.findByText("Not registered yet")).toBeTruthy();
    expect(screen.getByText(/registered in Bifrost after it is certified and committed/)).toBeTruthy();
    expect(screen.queryByText("Could not load the registry entry")).toBeNull();
    expect(screen.queryByText("You can't see this workflow's registry entry")).toBeNull();
  });

  test("test_849_a_version_can_be_looked_up_and_an_unknown_one_says_so_with_a_way_back", async () => {
    const get = vi.fn((_id: string, version?: string) => (version === "9.0.9" ? missing() : Promise.resolve(version === "1.0.1" ? OLD : ENTRY)));
    install(get);
    renderPanel();
    await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_2" });
    const input = screen.getByLabelText("Version");
    const lookup = screen.getByRole("button", { name: "Look up" }) as HTMLButtonElement;
    fireEvent.change(input, { target: { value: "1.2.0" } });
    expect(lookup.disabled).toBe(true); // not MAJOR.0.PATCH
    expect(screen.getByRole("status").textContent).toMatch(/MAJOR\.0\.PATCH/);
    fireEvent.change(input, { target: { value: "9.0.9" } });
    expect(lookup.disabled).toBe(false);
    fireEvent.click(lookup);
    expect(await screen.findByText("Version 9.0.9 is not registered")).toBeTruthy();
    expect(get).toHaveBeenLastCalledWith("w1", "9.0.9");
    fireEvent.click(screen.getByRole("button", { name: "Show the current version" }));
    expect(await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_2" })).toBeTruthy();
    expect(get).toHaveBeenLastCalledWith("w1", undefined);
  });

  test("test_849_loading_then_an_error_with_the_servers_detail_and_a_retry_that_reloads", async () => {
    const get = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("Boom"), { statusCode: 500, problem: { detail: "Bifrost is unreachable" } }))
      .mockResolvedValue(ENTRY);
    install(get);
    renderPanel();
    expect(screen.getByLabelText("Loading the registry entry")).toBeTruthy();
    expect(await screen.findByText("Could not load the registry entry")).toBeTruthy();
    expect(screen.getByText("Bifrost is unreachable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "Registry entry wf_onboarding_v1_2" })).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(2);
  });

  test("test_849_an_account_that_may_not_see_the_project_gets_the_forbidden_state_without_retry", async () => {
    install(() => Promise.reject(Object.assign(new Error("Forbidden"), { statusCode: 403 })));
    renderPanel();
    expect(await screen.findByText("You can't see this workflow's registry entry")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  test("test_849_before_the_build_has_started_there_is_no_workflow_to_ask_about", () => {
    const get = vi.fn();
    install(get);
    renderPanel(""); // no workflow id yet (the default parameter would replace undefined)
    expect(screen.getByText("No registry entry yet")).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
  });

  test("test_849_the_panel_is_read_only_its_only_control_is_the_version_look_up", async () => {
    install(async () => ENTRY);
    renderPanel();
    await screen.findByRole("region", { name: "Registry entry wf_onboarding_v1_2" });
    expect(document.querySelectorAll("textarea, select")).toHaveLength(0);
    expect(document.querySelectorAll("input")).toHaveLength(1); // the version look-up
    const names = Array.from(document.querySelectorAll("button")).map(b => b.textContent);
    expect(names.join(" ")).not.toMatch(/register|enable|disable|supersede|delete|save/i);
  });
});
