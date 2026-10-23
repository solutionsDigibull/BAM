import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildRow, BuildsResponse } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import {
  buildRows,
  canRetry,
  codeHref,
  GATES,
  gateStatus,
  pollMs,
  POLL_MS,
  summary,
} from "./build-api";
import { BuildScreen, EMPTY_MESSAGE } from "./build-screen";

// jsdom can't lay out AG Grid's virtual rows; the rendered matrix is covered by e2e/build-dashboard.spec.ts.
vi.mock("next/dynamic", () => ({
  default: () =>
    function GridStub({ rows }: { rows: BuildRow[] }) {
      return <div data-testid="grid-stub">{rows.map((r) => r.skill_id).join(",")}</div>;
    },
}));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const BUILDS = `GET /projects/${P}/workflows/${W}/builds`;

const row = (over: Partial<BuildRow> = {}): BuildRow => ({
  sub_skill_id: "s1",
  skill_id: "MS01.1_intake",
  skill_name: "Intake",
  state: "BUILDING",
  stage: "ast_gate",
  attempt: 0,
  gates: { ISATVON_CONTRACT: "PASSED", AST_SYNTAX: "RUNNING" },
  ...over,
});
const builds = (rows: unknown[], active = true): BuildsResponse =>
  ({ workflow_id: W, workflow_state: "BUILDING", active, sub_skills: rows }) as BuildsResponse;

afterEach(() => vi.unstubAllGlobals());

describe("build dashboard rules (E12.6)", () => {
  test("19 gate columns in contract order, AST ×8", () => {
    expect(GATES).toHaveLength(19);
    expect(GATES.filter((g) => g.family === "AST")).toHaveLength(8);
    expect(GATES[0]!.name).toBe("ISATVON_CONTRACT");
    expect(GATES.at(-1)!.name).toBe("LOOP_TESTS");
  });

  test("a gate the row doesn't mention is PENDING", () => {
    expect(gateStatus(row(), "AST_SYNTAX")).toBe("RUNNING");
    expect(gateStatus(row(), "RUFF")).toBe("PENDING");
    expect(gateStatus(row({ gates: undefined }), "RUFF")).toBe("PENDING");
  });

  test.each([[null], [{}], [{ sub_skills: null }], ["x"]])("%j gives no rows", (bad) => {
    expect(buildRows(bad)).toEqual([]);
  });

  test("a row without an id is dropped, good ones stay", () => {
    expect(buildRows(builds([row(), { skill_id: "x" }, null])).map((r) => r.skill_id)).toEqual([
      "MS01.1_intake",
    ]);
  });

  test("e12_6_t04 Retry only in NEEDS_FDE_REVIEW", () => {
    expect(canRetry("NEEDS_FDE_REVIEW")).toBe(true);
    for (const s of ["BUILDING", "CORRECTING", "CERTIFIED", undefined] as const)
      expect(canRetry(s)).toBe(false);
  });

  test("e12_6_t07 polling stops once nothing is active, and backs off after failures", () => {
    expect(pollMs(builds([row()]), 0)).toBe(POLL_MS);
    expect(pollMs(builds([row({ state: "CERTIFIED" })], false), 0)).toBe(false);
    expect(pollMs(undefined, 0)).toBe(false);
    expect([1, 2, 3, 4, 9].map((n) => pollMs(builds([]), n))).toEqual([
      6_000, 12_000, 24_000, 30_000, 30_000,
    ]);
  });

  test("a finding links to the Code Viewer at its file and line", () => {
    expect(codeHref(P, W, { subId: "s1", attempt: 1, file: "skill/handoff.py", line: 12 })).toBe(
      `/projects/${P}/workflows/${W}/code?sub=s1&attempt=1&file=skill%2Fhandoff.py&line=12`,
    );
    expect(codeHref(P, W, { subId: "s1", attempt: 0, file: "a.py" })).not.toContain("line=");
  });

  test("the summary counts failed gates and Sub-Skills waiting for review", () => {
    const rows = [
      row(),
      row({ sub_skill_id: "s2", gates: { RUFF: "FAILED" } }),
      row({ sub_skill_id: "s3", state: "NEEDS_FDE_REVIEW", gates: { AST_HANDOFF: "FAILED" } }),
    ];
    expect(summary(rows)).toEqual({ total: 3, failed: 2, review: 1 });
  });
});

describe("Build & Test screen states (E12.6)", () => {
  test("nothing built yet shows the empty state", async () => {
    fakeApi({ [BUILDS]: { body: builds([], false) } });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeDefined();
  });

  test("rows reach the grid with the summary and the live line", async () => {
    fakeApi({
      [BUILDS]: {
        body: builds([
          row(),
          row({ sub_skill_id: "s2", skill_id: "MS01.2_x", state: "NEEDS_FDE_REVIEW" }),
        ]),
      },
    });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    expect((await screen.findByTestId("grid-stub")).textContent).toBe("MS01.1_intake,MS01.2_x");
    expect(screen.getByText("2 Sub-Skills · 1 waiting for review")).toBeDefined();
    expect(screen.getByText("Live: updates every 3 s")).toBeDefined();
  });

  test("an API error shows its detail with a retry", async () => {
    fakeApi({ [BUILDS]: problem(503, "UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("another tenant's or an unassigned project is not found (404)", async () => {
    fakeApi({ [BUILDS]: problem(404, "NOT_FOUND", "No such workflow") });
    renderWithRefine(<BuildScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText("Not found")).toBeDefined();
  });
});
