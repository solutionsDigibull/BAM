import { expect, test, type Page, type Route } from "@playwright/test";

// #715: Translate shows the manifests of the workflow and of each Sub-Skill, read only: the versions stored and one
// manifest as text. There is no edit, save or approve here. The API is faked at the proxy. Set UI_SHOTS_DIR to save a
// screenshot of each state.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
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
const BUILDS = {
  workflow_id: WORKFLOW,
  workflow_state: "BUILDING",
  active: false,
  sub_skills: [
    {
      sub_skill_id: SUB,
      skill_id: "MS01.1_Intake",
      skill_name: "Intake the RFQ",
      state: "CERTIFIED",
      stage: "done",
      attempt: 0,
      gates: {},
    },
  ],
};
const entry = (version: string, build_no: number, is_frozen: boolean, sha: string) => ({
  version,
  sha256: sha.repeat(64),
  build_no,
  cycle: null,
  attempt: null,
  is_frozen,
  created_at: "2026-11-18T10:00:00Z",
});
const LIST = { items: [entry("1.0.2", 2, true, "b"), entry("1.0.1", 1, false, "a")] };
const VIEW = (version: string, build_no: number, is_frozen: boolean) => ({
  ...entry(version, build_no, is_frozen, version === "1.0.2" ? "b" : "a"),
  schema_version: "1",
  reused: false,
  manifest: {
    workflow: { name: "Onboarding", version },
    skills: [{ id: "MS01.1_Intake", tools: ["intake__action", "intake__check"] }],
    governance: { deterministic_hash: `sha256:${"b".repeat(64)}` },
  },
});

async function open(page: Page, baseURL: string, versions: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/builds`, (r) =>
    json(r, BUILDS),
  );
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/manifests`, (r) =>
    versions(r),
  );
  // /manifest (one) and /manifests (the list) share a prefix, so the one-manifest routes match the exact path.
  await page.route(
    (u) => u.pathname.endsWith(`/workflows/${WORKFLOW}/manifest`),
    (r) =>
      json(
        r,
        r.request().url().includes("build_no=1") ? VIEW("1.0.1", 1, false) : VIEW("1.0.2", 2, true),
      ),
  );
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/manifests`,
    (r) => json(r, { items: [entry("1.0.1", 1, false, "c")] }),
  );
  await page.route(
    (u) => u.pathname.endsWith(`/sub-skills/${SUB}/manifest`),
    (r) =>
      json(r, {
        ...VIEW("1.0.1", 1, false),
        manifest: { skill: { id: "MS01.1_Intake", kind: "action" } },
      }),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Manifests" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e715_manifests loaded versions newest first and the newest as read-only text, nothing can be changed", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, LIST));
  const versions = page.getByRole("list", { name: "Versions" });
  await expect(versions.locator("li").first()).toContainText("v1.0.2");
  await expect(versions.locator("li").first()).toContainText("Frozen");
  await expect(page.getByLabel("Manifest v1.0.2")).toContainText('"tools"');
  await expect(page.locator(".mf").getByRole("button", { name: /save|approve|edit/i })).toHaveCount(
    0,
  );
  await expect(page.locator(".mf textarea")).toHaveCount(0);
  await shot(page, "manifests-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "manifests-1c-loaded-wide");
});

test("e715_manifests an older version opens on its own", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, LIST));
  await expect(page.getByLabel("Manifest v1.0.2")).toBeVisible();
  await page.getByRole("button", { name: /v1\.0\.1/ }).click();
  await expect(page.getByLabel("Manifest v1.0.1")).toBeVisible();
  await shot(page, "manifests-2-older-version");
});

test("e715_manifests a Sub-Skill has its own manifests", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, LIST));
  await expect(page.getByLabel("Manifest v1.0.2")).toBeVisible();
  await page.getByLabel("Manifest of").selectOption(SUB);
  await expect(page.getByLabel("Manifest v1.0.1")).toContainText('"kind": "action"');
  await shot(page, "manifests-3-sub-skill");
});

test("e715_manifests loading shows a skeleton until the versions arrive", async ({
  page,
  baseURL,
}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, LIST);
  });
  await expect(page.getByText("Loading the manifests")).toBeAttached();
  await shot(page, "manifests-4-loading");
  release();
  await expect(page.getByRole("list", { name: "Versions" })).toBeVisible();
});

test("e715_manifests empty a workflow with no manifest yet says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { items: [] }));
  await expect(page.getByText("Not generated yet")).toBeVisible();
  await shot(page, "manifests-5-empty");
});

test("e715_manifests error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The manifest store is unreachable") : json(r, LIST),
  );
  await expect(page.getByText("The manifest store is unreachable")).toBeVisible();
  await shot(page, "manifests-6-error");
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("list", { name: "Versions" })).toBeVisible();
});

test("e715_manifests forbidden an account that may not see the project gets the forbidden state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see these manifests")).toBeVisible();
  await shot(page, "manifests-7-forbidden");
});
