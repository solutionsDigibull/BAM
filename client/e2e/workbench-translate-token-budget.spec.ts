import { expect, test, type Page, type Route } from "@playwright/test";

// #776: the new workbench's Translate step has a Token budget view: tokens used against the cap per scope with the alert
// level, and the one raise a stopped build is allowed. The API is faked at the proxy (no sbam-api here). Set UI_SHOTS_DIR to
// save a screenshot of each state for comparison with the BAM_UI reference.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const BUILD = "build-0190f3a2-7b1c-7d4e-8f00-0000000000d1-1";
const API = "**/api/proxy/api/v1";
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const problem = (route: Route, status: number, title: string, detail?: string) =>
  json(route, { type: "about:blank", title, status, detail }, status);

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "WORKFLOW_CREATED",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};
const workflow = {
  id: WORKFLOW,
  project_id: PROJECT,
  sow_version_id: "v1",
  name: "Onboarding",
  version: "1.0.0",
  state: "BUILDING",
  created_at: "2026-11-10T10:00:00Z",
};
const scope = (name: string, used: number, cap: number, level: string, raised = false) => ({
  scope: name,
  used,
  cap,
  level,
  raised_once: raised,
  build_id: BUILD,
});
const meter = (scopes: unknown[]) => ({ scopes, first_alert_pct: 50, second_alert_pct: 80 });
const LEVELS = meter([
  scope("SUB_SKILL", 90_000, 200_000, "FIRST_ALERT"),
  scope("WORKFLOW", 1_700_000, 2_000_000, "SECOND_ALERT"),
  scope("KNOWLEDGE", 1_200_000, 30_000_000, "OK"),
]);
const STOPPED = meter([
  scope("SUB_SKILL", 90_000, 200_000, "FIRST_ALERT"),
  scope("WORKFLOW", 2_050_000, 2_000_000, "STOPPED"),
  scope("KNOWLEDGE", 1_200_000, 30_000_000, "OK"),
]);

async function open(page: Page, baseURL: string, budget: (route: Route) => unknown) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  // Calls no test fakes answer 404 (with no sbam-api the proxy would answer 401 and sign the user out, #742).
  await page.route(`${API}/**`, (r) => problem(r, 404, "Not Found"));
  await page.route(
    (u) => u.pathname.endsWith("/api/proxy/api/v1/projects"),
    (r) => json(r, { items: [project] }),
  );
  await page.route(`${API}/projects/${PROJECT}/documents**`, (r) =>
    json(r, { items: [], next_cursor: null }),
  );
  await page.route(`${API}/projects/${PROJECT}/pipeline`, (r) =>
    json(r, { state: "WORKFLOW_CREATED", steps: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) => json(r, { versions: [] }));
  await page.route(`${API}/projects/${PROJECT}/workflows`, (r) => json(r, { items: [workflow] }));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) =>
    json(r, { workflow_id: WORKFLOW, groups: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/token-budget`, (r) => budget(r));
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Token budget" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("wg23_t05 the meter shows used against the cap per scope with the alert level", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, LEVELS));
  const list = page.getByRole("list", { name: "Token usage by scope" });
  await expect(list).toContainText("1,700,000 of 2,000,000 tokens");
  await expect(list.locator("li[data-scope=WORKFLOW]")).toHaveAttribute(
    "data-level",
    "SECOND_ALERT",
  );
  await expect(list.locator("li[data-scope=SUB_SKILL]")).toContainText("Half used");
  await expect(page.getByRole("form", { name: /Raise the/ })).toHaveCount(0);
  await shot(page, "budget-1-levels");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "budget-1b-levels-wide");
});

test("wg23_t02 a stopped scope offers the one raise, and the raise lifts the stop", async ({
  page,
  baseURL,
}) => {
  let raised = false;
  let sent: unknown;
  await open(page, baseURL!, (r) =>
    json(
      r,
      raised
        ? meter([
            scope("SUB_SKILL", 90_000, 200_000, "FIRST_ALERT"),
            scope("WORKFLOW", 2_050_000, 4_000_000, "OK", true),
            scope("KNOWLEDGE", 1_200_000, 30_000_000, "OK"),
          ])
        : STOPPED,
    ),
  );
  // registered after open(): the last route wins, so it must come after the 404 catch-all
  await page.route(`${API}/projects/${PROJECT}/token-budget/raise`, (r) => {
    sent = r.request().postDataJSON();
    raised = true;
    return json(r, scope("WORKFLOW", 2_050_000, 4_000_000, "OK", true));
  });
  const form = page.getByRole("form", { name: "Raise the Workflow build limit" });
  await expect(form).toBeVisible();
  await shot(page, "budget-2-stopped-raise");
  await form.getByRole("button", { name: "Raise the limit" }).click();
  await expect(page.getByText(/Press Retry on the stopped Sub-Skill/)).toBeVisible();
  expect(sent).toEqual({ scope: "WORKFLOW", build_id: BUILD, tokens: 4_000_000 });
  await expect(page.getByRole("form", { name: /Raise the/ })).toHaveCount(0);
  await shot(page, "budget-3-raised");
});

test("wg23_t03 a build that was raised once cannot be raised again", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) =>
    json(
      r,
      meter([
        scope("SUB_SKILL", 90_000, 200_000, "FIRST_ALERT"),
        scope("WORKFLOW", 4_100_000, 4_000_000, "STOPPED", true),
        scope("KNOWLEDGE", 0, 30_000_000, "OK"),
      ]),
    ),
  );
  await expect(page.getByText(/raised once already and cannot be raised again/)).toBeVisible();
  await expect(page.getByRole("form", { name: /Raise the/ })).toHaveCount(0);
  await shot(page, "budget-4-second-raise-refused");
});

test("wg23_loading shows a skeleton until the meter arrives", async ({ page, baseURL }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, LEVELS);
  });
  await expect(page.getByText("Loading the token budget")).toBeAttached();
  await shot(page, "budget-5-loading");
  release();
  await expect(page.getByRole("list", { name: "Token usage by scope" })).toBeVisible();
});

test("wg23_empty nothing counted yet says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) =>
    json(
      r,
      meter([
        scope("SUB_SKILL", 0, 200_000, "OK"),
        scope("WORKFLOW", 0, 2_000_000, "OK"),
        scope("KNOWLEDGE", 0, 30_000_000, "OK"),
      ]),
    ),
  );
  await expect(page.getByText(/No model calls are counted yet/)).toBeVisible();
  await shot(page, "budget-6-empty");
});

test("wg23_error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The database is unreachable") : json(r, LEVELS),
  );
  await expect(page.getByText("The database is unreachable")).toBeVisible();
  await shot(page, "budget-7-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("list", { name: "Token usage by scope" })).toBeVisible();
});

test("wg23_forbidden an account that may not see the project gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this project's token budget")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "budget-8-forbidden");
});
