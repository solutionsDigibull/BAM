import { expect, test, type Page, type Route } from "@playwright/test";

// #715: Translate shows the code attempts of one Sub-Skill, read only: the first generation and the corrections, a file
// with its generated-body lines marked and its findings on their lines, and a comparison of a correction with another
// attempt. The API is faked at the proxy. Set UI_SHOTS_DIR to save a screenshot of each state.
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
      attempt: 1,
      gates: {},
    },
    {
      sub_skill_id: "0190f3a2-7b1c-7d4e-8f00-0000000000d2",
      skill_id: "MS01.2_Costing",
      skill_name: "Cost the parts",
      state: "BUILDING",
      stage: "assemble",
      attempt: 0,
      gates: {},
    },
  ],
};
const sha = "a".repeat(64);
const ATTEMPTS = {
  items: [
    {
      build_no: 1,
      cycle: 0,
      attempt: 0,
      status: "FAILED",
      template_name: "mcp_action",
      template_version: "1.2.0",
      files: [{ path: "app/intake.py", sha256: sha }],
      findings: [
        {
          gate: "RUFF",
          code: "F401",
          message: "os imported but unused",
          file: "app/intake.py",
          line: 1,
        },
      ],
    },
    {
      build_no: 1,
      cycle: 0,
      attempt: 1,
      status: "PASSED",
      template_name: "mcp_action",
      template_version: "1.2.0",
      correction_reason: "Removed the unused import the Ruff gate found",
      files: [
        { path: "app/intake.py", sha256: sha },
        { path: "app/schema.py", sha256: sha },
      ],
      findings: [],
    },
  ],
};
const CONTENT = [
  "from typing import Any",
  "import json",
  "",
  "def action(payload: dict[str, Any]) -> dict[str, Any]:",
  "    # >>> LLM_BODY:action",
  "    rows = json.loads(payload['rfq'])",
  "    return {'rows': len(rows)}",
  "    # <<< LLM_BODY:action",
  "",
].join("\n");
const FILE = {
  path: "app/intake.py",
  sha256: sha,
  content: CONTENT,
  truncated: false,
  slots: [{ start: 6, end: 7 }],
  findings: [],
};
const DIFF = {
  from_attempt: 0,
  to_attempt: 1,
  path: "app/intake.py",
  changed: true,
  unified:
    "--- a/app/intake.py\n+++ b/app/intake.py\n@@ -1,3 +1,2 @@\n-import os\n from typing import Any\n import json",
  truncated: false,
};

async function open(page: Page, baseURL: string, attempts: (route: Route) => unknown) {
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
  const code = `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/code/attempts`;
  await page.route(code, (r) => attempts(r));
  await page.route(`${code}/*/files**`, (r) => json(r, FILE));
  await page.route(`${code}/*/diff/*`, (r) => json(r, DIFF));
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Code attempts" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e715_attempts loaded the attempts list newest first and the file marks the generated body", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, ATTEMPTS));
  const list = page.getByRole("list", { name: "Attempts" });
  await expect(list.locator("li").first()).toContainText("Correction 1");
  await expect(list.locator("li").first()).toContainText(
    "Removed the unused import the Ruff gate found",
  );
  await expect(list.locator("li").nth(1)).toContainText("First generation");
  const code = page.getByRole("list", { name: "Content of app/intake.py" });
  await expect(code.locator("li.att-slot-line")).toHaveCount(2);
  await expect(code.locator("li.att-protected-line").first()).toContainText(
    "from typing import Any",
  );
  await expect(page.getByText("Read only", { exact: true })).toBeVisible();
  await shot(page, "attempts-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "attempts-1c-loaded-wide");
});

test("e715_attempts the first generation shows its finding on the line", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, ATTEMPTS));
  await page.getByRole("button", { name: /First generation/ }).click();
  await expect(page.getByRole("list", { name: "Findings of first generation" })).toContainText(
    "F401",
  );
  await shot(page, "attempts-2-first-generation-findings");
});

test("e715_attempts compare shows what the correction changed", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, ATTEMPTS));
  await expect(page.getByRole("list", { name: "Content of app/intake.py" })).toBeVisible();
  await page.getByLabel("Compare with").selectOption("0");
  const diff = page.getByRole("figure", { name: "Changes from first generation to correction 1" });
  await expect(diff.locator(".att-d-del")).toContainText("-import os");
  await shot(page, "attempts-3-compare");
});

test("e715_attempts loading shows a skeleton until the attempts arrive", async ({
  page,
  baseURL,
}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, ATTEMPTS);
  });
  await expect(page.getByText("Loading the code attempts")).toBeAttached();
  await shot(page, "attempts-4-loading");
  release();
  await expect(page.getByRole("list", { name: "Attempts" })).toBeVisible();
});

test("e715_attempts empty a Sub-Skill with no attempts says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { items: [] }));
  await expect(page.getByText("No code attempts yet")).toBeVisible();
  await shot(page, "attempts-5-empty");
});

test("e715_attempts error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The code store is unreachable") : json(r, ATTEMPTS),
  );
  await expect(page.getByText("The code store is unreachable")).toBeVisible();
  await shot(page, "attempts-6-error");
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("list", { name: "Attempts" })).toBeVisible();
});

test("e715_attempts forbidden an account that may not see the project gets the forbidden state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this Sub-Skill's code")).toBeVisible();
  await shot(page, "attempts-7-forbidden");
});
