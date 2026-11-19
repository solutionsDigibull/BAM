import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import type { HeldPatch, HeldPatchResponse } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { HeldPatchCard } from "./HeldPatchCard";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const SHA = "a".repeat(64);
const HELD: HeldPatch = {
  sub_skill_id: SUB,
  content_sha256: SHA,
  attempt: 2,
  failed_gate: "AST_CONFORMANCE",
  rationale: "The bound is tighter than the data the Sub-Skill reads. <b>Loosen it.</b>",
  changed_paths: ["gate.max"],
  ops: [
    { op: "replace", path: "/isatvon_definition/gate/max", value: 500 },
    { op: "remove", path: "/isatvon_definition/notes" },
  ],
  isatvon_sha256_before: "b".repeat(64),
  isatvon_sha256_after: "c".repeat(64),
};
const RESP = (held: HeldPatch | null): HeldPatchResponse => ({ held });

const install = (over: Partial<BamApi> = {}) =>
  setBamApi({
    getHeldPatch: async () => RESP(HELD),
    decideHeldPatch: async (_w: string, _s: string, d: string) => ({ sub_skill_id: SUB, build_no: 5, decision: d === "accept" ? "ACCEPTED" : "REJECTED" }),
    ...over,
  } as unknown as BamApi);
const renderCard = () =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <HeldPatchCard workflowId="w1" subId={SUB} name="Verify payment" />
      </Refine>
    </div>,
  );
const press = (name: string) => fireEvent.click(screen.getAllByRole("button", { name }).at(-1)!);

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
});
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the held ISATVON patch the FDE decides (#715)", () => {
  test("test_715_the_patch_shows_the_gate_the_rationale_as_text_and_every_operation", async () => {
    install();
    renderCard();
    const card = await screen.findByRole("region", { name: "Held ISATVON patch" });
    expect(card.textContent).toContain("Attempt 2 proposed it after the Conformance gate failed");
    expect(screen.getByLabelText("Rationale").textContent).toContain("<b>Loosen it.</b>");
    expect(screen.getByLabelText("Rationale").querySelector("b")).toBeNull();
    const ops = screen.getByRole("list", { name: "Patch operations" });
    expect(ops.textContent).toContain("Replace");
    expect(ops.textContent).toContain("gate / max");
    expect(ops.textContent).toContain("500");
    expect(ops.textContent).toContain("Remove");
  });

  test("test_715_accepting_sends_the_hash_as_if_match_with_one_key_and_says_the_build_started", async () => {
    const decideHeldPatch = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 5, decision: "ACCEPTED" as const }));
    install({ decideHeldPatch });
    renderCard();
    await screen.findByRole("region", { name: "Held ISATVON patch" });
    press("Accept the patch");
    press("Accept");
    await waitFor(() => expect(decideHeldPatch).toHaveBeenCalledTimes(1));
    const [wf, sub, decision, reason, key, ifMatch] = decideHeldPatch.mock.calls[0] as unknown as (string | undefined)[];
    expect([wf, sub, decision, reason, ifMatch]).toEqual(["w1", SUB, "accept", undefined, `"${SHA}"`]);
    expect(key).toMatch(/[0-9a-f-]{36}/);
    expect((await screen.findByText(/Patch accepted: build 5 started/)).textContent).toBeTruthy();
  });

  test("test_715_rejecting_needs_a_reason_and_sends_it_without_an_if_match", async () => {
    const decideHeldPatch = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 6, decision: "REJECTED" as const }));
    install({ decideHeldPatch });
    renderCard();
    await screen.findByRole("region", { name: "Held ISATVON patch" });
    press("Reject it");
    press("Reject");
    expect(decideHeldPatch).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "The bound is right as it is" } });
    press("Reject");
    await waitFor(() => expect(decideHeldPatch).toHaveBeenCalledTimes(1));
    const call = decideHeldPatch.mock.calls[0] as unknown as (string | undefined)[];
    expect([call[2], call[3], call[5]]).toEqual(["reject", "The bound is right as it is", undefined]);
    expect((await screen.findByText(/Patch rejected: build 6 started/)).textContent).toBeTruthy();
  });

  test("test_715_a_refused_decision_shows_the_servers_reason_and_keeps_the_card", async () => {
    install({ decideHeldPatch: async () => { throw Object.assign(new Error("x"), { statusCode: 412, problem: { detail: "The Sub-Skill changed since you opened it." } }); } });
    renderCard();
    await screen.findByRole("region", { name: "Held ISATVON patch" });
    press("Accept the patch");
    press("Accept");
    expect((await screen.findByText("The Sub-Skill changed since you opened it.")).textContent).toBeTruthy();
    expect(screen.getByRole("region", { name: "Held ISATVON patch" })).toBeTruthy();
  });

  test("test_715_no_patch_waiting_is_a_quiet_line_not_an_error", async () => {
    install({ getHeldPatch: async () => RESP(null) });
    renderCard();
    expect((await screen.findByText(/No ISATVON patch is waiting for Verify payment/)).textContent).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Held ISATVON patch" })).toBeNull();
  });

  test("test_715_a_failed_load_shows_the_detail_and_a_retry", async () => {
    install({ getHeldPatch: async () => { throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "the store is down" } }); } });
    renderCard();
    expect((await screen.findByText("the store is down")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("test_715_another_tenants_workflow_is_the_forbidden_state", async () => {
    install({ getHeldPatch: async () => { throw Object.assign(new Error("x"), { statusCode: 404 }); } });
    renderCard();
    expect(await screen.findByText("You can't see this patch")).toBeTruthy();
  });
});
