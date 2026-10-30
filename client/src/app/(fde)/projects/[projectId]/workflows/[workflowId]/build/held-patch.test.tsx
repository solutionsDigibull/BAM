import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildRow, BuildsResponse, HeldPatch } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import { decideUrl, heldOf, heldPatchUrl, ifMatchOf, showValue } from "./build-api";
import { BuildScreen, NO_PATCH_MESSAGE } from "./build-screen";

// jsdom can't lay out AG Grid; the stub shows one "Review patch" button per Sub-Skill waiting for review.
vi.mock("next/dynamic", () => ({
  default: () =>
    function GridStub({
      rows,
      actions,
    }: {
      rows: BuildRow[];
      actions: { onPatch: (r: BuildRow) => void };
    }) {
      return (
        <div>
          {rows
            .filter((r) => r.state === "NEEDS_FDE_REVIEW")
            .map((r) => (
              <button key={r.sub_skill_id} onClick={() => actions.onPatch(r)}>
                {`Review patch ${r.skill_id}`}
              </button>
            ))}
        </div>
      );
    },
}));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "s2";
const BUILDS = `GET /projects/${P}/workflows/${W}/builds`;
const HELD_URL = `/projects/${P}/workflows/${W}/sub-skills/${SUB}/held-patch`;
const SHA = "c".repeat(64);

const builds = (): BuildsResponse =>
  ({
    workflow_id: W,
    workflow_state: "BUILDING",
    active: false,
    sub_skills: [
      {
        sub_skill_id: SUB,
        skill_id: "MS01.2_x",
        skill_name: "X",
        state: "NEEDS_FDE_REVIEW",
        stage: "wait_retry",
        attempt: 1,
        gates: {},
      },
    ],
  }) as BuildsResponse;

const held = (over: Partial<HeldPatch> = {}): HeldPatch => ({
  sub_skill_id: SUB,
  content_sha256: SHA,
  attempt: 1,
  failed_gate: "run_sandbox_tests",
  rationale: "the budget was too tight <b>for the sandbox</b>",
  changed_paths: ["/isatvon_definition/v_variables/budget_limit"],
  ops: [
    {
      op: "replace",
      path: "/isatvon_definition/v_variables/budget_limit",
      value: "9000ms timeout per validation cycle",
    },
  ],
  isatvon_sha256_before: "sha256:a",
  isatvon_sha256_after: "sha256:b",
  ...over,
});

const openPatch = async () => fireEvent.click(await screen.findByText("Review patch MS01.2_x"));

afterEach(() => vi.unstubAllGlobals());

describe("the held ISATVON patch rules (#499)", () => {
  test("the urls follow the API", () => {
    expect(heldPatchUrl(P, W, SUB)).toBe(
      `projects/${P}/workflows/${W}/sub-skills/${SUB}/held-patch`,
    );
    expect(decideUrl(P, W, SUB, "accept")).toBe(`${heldPatchUrl(P, W, SUB)}/accept`);
    expect(decideUrl(P, W, SUB, "reject")).toBe(`${heldPatchUrl(P, W, SUB)}/reject`);
  });

  test.each([[null], [undefined], [{}], [{ held: null }], ["x"]])("%j holds no patch", (bad) => {
    expect(heldOf(bad)).toBeNull();
  });

  test("a held patch is recognised and accepting sends its content hash quoted", () => {
    expect(heldOf({ held: held() })?.attempt).toBe(1);
    expect(ifMatchOf(held())).toBe(`"${SHA}"`);
  });

  test("a value is shown as text and cut when it is huge", () => {
    expect(showValue("a<b>c")).toBe("a<b>c");
    expect(showValue({ n: 1 })).toBe('{"n":1}');
    expect(showValue(undefined)).toBe("");
    expect(showValue("x".repeat(1000)).length).toBe(401);
  });
});

describe("the held patch drawer (#499)", () => {
  test("nothing waiting says so and offers no decision", async () => {
    fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: { body: { held: null } },
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    expect(await screen.findByText(NO_PATCH_MESSAGE)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  test("the patch is shown as text with its changes and why it was proposed", async () => {
    fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: { body: { held: held() } },
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    expect(await screen.findByText(/changes what the Sub-Skill means/)).toBeDefined();
    // markup in the model's rationale is shown as those characters, never rendered
    expect(screen.getByText("the budget was too tight <b>for the sandbox</b>")).toBeDefined();
    expect(screen.getByText("/isatvon_definition/v_variables/budget_limit")).toBeDefined();
    expect(screen.getByText("9000ms timeout per validation cycle")).toBeDefined();
    expect(screen.getByText("replace")).toBeDefined();
  });

  test("accepting sends If-Match and an Idempotency-Key, then refreshes the grid", async () => {
    const { calls } = fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: { body: { held: held() } },
      [`POST ${HELD_URL}/accept`]: {
        status: 202,
        body: { sub_skill_id: SUB, build_no: 1, decision: "ACCEPTED" },
      },
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "fine" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/accept"))).toBe(true),
    );
    const post = calls.find((c) => c.method === "POST")!;
    const headers = new Headers(post.init.headers);
    expect(headers.get("if-match")).toBe(`"${SHA}"`);
    expect(headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(String(post.init.body))).toEqual({ reason: "fine" });
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/builds")).length).toBe(2));
  });

  test("rejecting sends no If-Match and leaves an empty reason out", async () => {
    const { calls } = fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: { body: { held: held() } },
      [`POST ${HELD_URL}/reject`]: {
        status: 202,
        body: { sub_skill_id: SUB, build_no: 1, decision: "REJECTED" },
      },
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/reject"))).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(new Headers(post.init.headers).get("if-match")).toBeNull();
    expect(JSON.parse(String(post.init.body))).toEqual({});
  });

  test("a refused decision keeps the drawer open", async () => {
    const { calls } = fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: { body: { held: held() } },
      [`POST ${HELD_URL}/accept`]: problem(412, "PRECONDITION_FAILED", "Changed since you loaded"),
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(await screen.findByRole("button", { name: "Accept" })).toBeDefined();
    expect(calls.filter((c) => c.path.endsWith("/builds")).length).toBe(1); // no refresh
  });

  test("not allowed shows the forbidden state, not the patch", async () => {
    fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: problem(403, "FORBIDDEN", "Not allowed"),
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    expect(await screen.findByText(/forbidden|not allowed/i)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  });

  test("an unknown Sub-Skill is not found (404)", async () => {
    fakeApi({
      [BUILDS]: { body: builds() },
      [`GET ${HELD_URL}`]: problem(404, "NOT_FOUND", "No such Sub-Skill"),
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    await openPatch();
    expect(await screen.findByText("Not found")).toBeDefined();
  });
});
