import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #842: the certification evidence (import gate, repository licence, dependency vulnerabilities, MCP compliance) in the
// workbench's Check and Certify screens. The default project runs `next dev` with the dev role cookie and no sbam-api,
// so the API is faked at the proxy. Each state is saved as a screenshot for comparison with BAM_UI (CERT_SHOTS or the
// owner's Downloads folder on this machine).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SHOTS = process.env.CERT_SHOTS ?? path.join("test-results", "cert-evidence-shots");

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: detail, status, detail }, status);

const subId = (n: number) => `0190f3a2-7b1c-7d4e-8f00-${String(n).padStart(12, "0")}`;
const PASS = {
  IMPORT_RESOLUTION: "PASSED",
  REPO_LICENCE: "PASSED",
  DEPENDENCY_VULN: "PASSED",
  MCP_COMPLIANCE: "PASSED",
};
const row = (n: number, gates: Record<string, string>) => ({
  sub_skill_id: subId(n),
  skill_id: `MS01.${n}_step_${n}`,
  skill_name: `Step ${n}`,
  state: "BUILDING",
  stage: "ast_gate",
  attempt: 0,
  gates,
});

type Builds = (route: Route) => Promise<void>;

async function open(
  page: Page,
  baseURL: string,
  builds: Builds,
  screen: "results" | "certification" = "certification",
) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/pipeline`)
      return json(route, {
        state: "WORKFLOW_CREATED",
        steps: [],
        counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0 },
      });
    if (p === `/projects/${PROJECT}/documents`)
      return json(route, { items: [], next_cursor: null });
    if (p === `/projects/${PROJECT}/knowledge`) return json(route, { versions: [] });
    if (p === `/projects/${PROJECT}/sow`)
      return json(route, { sow_id: "s1", current: null, approved: null, etag: "e" });
    if (p === `/projects/${PROJECT}/workflows`)
      return json(route, {
        items: [
          {
            id: WORKFLOW,
            name: "Onboarding",
            state: "BUILDING",
            created_at: "2026-11-10T10:00:00Z",
          },
        ],
      });
    if (p === `/projects/${PROJECT}/workflows/${WORKFLOW}/builds`) return builds(route);
    if (p === `/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${subId(2)}/gates`)
      return json(route, {
        sub_skill_id: subId(2),
        attempt: 0,
        gates: [
          {
            gate: "REPO_LICENCE",
            status: "FAILED",
            findings: [
              {
                gate: "REPO_LICENCE",
                code: "LICENCE_REFUSED",
                message: "acme/pcb-tools is under GPL-3.0, which is not on the licence allowlist",
              },
              {
                gate: "REPO_LICENCE",
                code: "LICENCE_UNKNOWN",
                message: "Dependency left-pad has no declared licence",
              },
            ],
          },
        ],
      });
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench#/sandbox/${screen}`);
}

const panel = (page: Page) => page.locator(".cert-evidence");
const shot = (page: Page, name: string) =>
  panel(page).screenshot({ path: path.join(SHOTS, `${name}.png`) });
const full = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `${name}-page.png`) });

test("e842_t01 loaded: pass, fail with the reason, and a missing result that blocks certification", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (route) =>
    json(route, {
      workflow_id: WORKFLOW,
      workflow_state: "BUILDING",
      active: false,
      sub_skills: [
        row(1, PASS),
        row(2, { ...PASS, REPO_LICENCE: "FAILED" }),
        row(3, { IMPORT_RESOLUTION: "PASSED", REPO_LICENCE: "PASSED", DEPENDENCY_VULN: "PASSED" }),
      ],
    }),
  );
  await expect(panel(page).getByRole("heading", { name: "Certification evidence" })).toBeVisible();
  const licence = panel(page).locator('[data-gate="REPO_LICENCE"]');
  await expect(licence.getByText("Fail")).toBeVisible();
  await expect(licence.getByText("Step 2")).toBeVisible();
  await expect(licence.getByText(/GPL-3.0, which is not on the licence allowlist/)).toBeVisible();
  const mcp = panel(page).locator('[data-gate="MCP_COMPLIANCE"]');
  await expect(mcp.getByText("Missing")).toBeVisible();
  await expect(mcp.getByText(/Certification is blocked until it passes/)).toBeVisible();
  await expect(panel(page).getByText("Blocks certification")).toBeVisible();
  expect(await panel(page).getByRole("button").count()).toBe(0);
  await shot(page, "loaded");
  await full(page, "loaded");
});

test("e842_t02 ready: every result passes for every Sub-Skill", async ({ page, baseURL }) => {
  await open(
    page,
    baseURL!,
    (route) =>
      json(route, {
        workflow_id: WORKFLOW,
        workflow_state: "CERTIFYING",
        active: false,
        sub_skills: [row(1, PASS), row(2, PASS)],
      }),
    "results",
  );
  await expect(panel(page).getByText("Ready")).toBeVisible();
  await expect(panel(page).getByText("Pass", { exact: true })).toHaveCount(4);
  await shot(page, "ready");
});

test("e842_t03 empty: no Sub-Skill has been built yet", async ({ page, baseURL }) => {
  await open(page, baseURL!, (route) =>
    json(route, {
      workflow_id: WORKFLOW,
      workflow_state: "BUILDING",
      active: false,
      sub_skills: [],
    }),
  );
  await expect(panel(page).getByText("No evidence yet")).toBeVisible();
  await shot(page, "empty");
});

test("e842_t04 error: the server's detail and a retry", async ({ page, baseURL }) => {
  await open(page, baseURL!, (route) => problem(route, 503, "Storage is unavailable; retry later"));
  await expect(panel(page).getByText(/The evidence could not be loaded/)).toBeVisible();
  await expect(panel(page).getByText(/Storage is unavailable; retry later/)).toBeVisible();
  await expect(panel(page).getByRole("button", { name: "Retry" })).toBeVisible();
  await shot(page, "error");
});

test("e842_t05 forbidden: another tenant or an unassigned FDE gets the not-available state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (route) => problem(route, 404, "Not found"));
  await expect(panel(page).getByText("Not available")).toBeVisible();
  await expect(panel(page).getByText("Import gate")).toHaveCount(0);
  await shot(page, "forbidden");
});
