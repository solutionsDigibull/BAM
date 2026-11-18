import { expect, test, type Page, type Route } from "@playwright/test";

// #849: the new workbench's Translate step shows the workflow's registry entry, read only: the name it is registered
// under in Bifrost, the version, the certified image digest and manifest hash, the certification and whether a later
// version superseded it (#760). The API is faked at the proxy (no sbam-api here). Set UI_SHOTS_DIR to save a screenshot
// of each state for comparison with the BAM_UI reference.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
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
const TREE = { workflow_id: WORKFLOW, groups: [] };
const tool = (name: string, role: "action" | "check", skill_id: string) => ({
  name,
  skill_id,
  role,
  input_schema: { type: "object" },
  output_schema: { type: "object" },
});
const ENTRY = {
  workflow_id: WORKFLOW,
  version: "1.0.2",
  registry_name: "wf_onboarding_v1_2",
  registry_id: "reg-0190f3a2",
  image_digest: `sha256:${"ab".repeat(32)}`,
  manifest_hash: `sha256:${"cd".repeat(32)}`,
  certification_id: "0190f3a2-7b1c-7d4e-8f00-0000000000c9",
  owner: "fde-a1",
  tools: [
    tool("MS01_1__action", "action", "MS01.1_Intake"),
    tool("MS01_1__check", "check", "MS01.1_Intake"),
    tool("MS01_2__action", "action", "MS01.2_Costing"),
    tool("MS01_2__check", "check", "MS01.2_Costing"),
  ],
  status: "REGISTERED",
  registered_at: "2026-11-17T10:00:00Z",
  superseded_at: null,
  superseded_by: null,
};
const OLD = {
  ...ENTRY,
  version: "1.0.1",
  registry_name: "wf_onboarding_v1_1",
  status: "SUPERSEDED",
  superseded_at: "2026-11-17T12:00:00Z",
  superseded_by: "1.0.2",
};

async function open(page: Page, baseURL: string, registration: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) => json(r, TREE));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/registration**`, (r) =>
    registration(r),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Registry" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

const answer = (route: Route) =>
  route.request().url().includes("version=1.0.1") ? json(route, OLD) : json(route, ENTRY);

test("e849_loaded the registered entry shows its name, digests, certification and tools, nothing can be changed", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, answer);
  const card = page.getByRole("region", { name: "Registry entry wf_onboarding_v1_2" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Current");
  await expect(card).toContainText("sha256:abababababab"); // the short image digest
  await expect(card).toContainText("sha256:cdcdcdcdcdcd"); // the short manifest hash
  await expect(card).toContainText("0190f3a2-7b1c-7d4e-8f00-0000000000c9");
  await expect(card).toContainText("2 action and 2 check tools");
  await expect(card.getByRole("list", { name: "Tools" })).toContainText("MS01_2__check");
  await expect(page.locator(".reg textarea, .reg select")).toHaveCount(0);
  await expect(page.locator(".reg input")).toHaveCount(1); // only the version look-up
  await shot(page, "1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "1c-loaded-wide");
});

test("e849_superseded an older version says it was superseded and links to the one that replaced it", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, answer);
  await expect(
    page.getByRole("region", { name: "Registry entry wf_onboarding_v1_2" }),
  ).toBeVisible();
  await page.getByLabel("Version", { exact: true }).fill("1.0.1");
  await page.getByRole("button", { name: "Look up" }).click();
  const card = page.getByRole("region", { name: "Registry entry wf_onboarding_v1_1" });
  await expect(card).toContainText("Superseded");
  await expect(card).toContainText("by version 1.0.2");
  await expect(card).toContainText("disabled in Bifrost but stays listed");
  await shot(page, "1b-superseded");
  await page.getByRole("button", { name: "Show v1.0.2" }).click();
  await expect(
    page.getByRole("region", { name: "Registry entry wf_onboarding_v1_2" }),
  ).toBeVisible();
});

test("e849_empty a workflow that is not registered yet says so, it is not an error", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 404, "Not Found"));
  await expect(page.getByText("Not registered yet")).toBeVisible();
  await expect(
    page.getByText("registered in Bifrost after it is certified and committed"),
  ).toBeVisible();
  await expect(page.getByText("Could not load the registry entry")).toHaveCount(0);
  await shot(page, "2-empty");
});

test("e849_error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true; // stays down (also for the browser's own refetches) until Retry is clicked
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "Bifrost is unreachable") : json(r, ENTRY),
  );
  await expect(page.getByText("Bifrost is unreachable")).toBeVisible();
  await shot(page, "3-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(
    page.getByRole("region", { name: "Registry entry wf_onboarding_v1_2" }),
  ).toBeVisible();
});

test("e849_forbidden an account that may not see the project gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this workflow's registry entry")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "4-forbidden");
});
