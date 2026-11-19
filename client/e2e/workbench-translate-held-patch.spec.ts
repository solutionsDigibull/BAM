import { expect, test, type Page, type Route } from "@playwright/test";

// #715: a Sub-Skill that waits for the FDE shows the ISATVON patch a correction proposed; the FDE accepts it (the
// Sub-Skill's hash goes as If-Match) or rejects it with a reason, and the build starts again. The API is faked at the proxy.
// Set UI_SHOTS_DIR to save a screenshot of each state.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const SHA = "a".repeat(64);
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
      state: "NEEDS_FDE_REVIEW",
      stage: "wait_retry",
      attempt: 2,
      gates: { ISATVON_CONTRACT: "PASSED", CODEGEN: "PASSED", AST_CONFORMANCE: "FAILED" },
    },
  ],
};
const HELD = {
  held: {
    sub_skill_id: SUB,
    content_sha256: SHA,
    attempt: 2,
    failed_gate: "AST_CONFORMANCE",
    rationale:
      "The input bound of 100 rows is smaller than the RFQs this Sub-Skill reads (up to 480 rows). Raising the bound lets the generated code accept them.",
    changed_paths: ["gate.max_rows"],
    ops: [
      { op: "replace", path: "/isatvon_definition/gate/max_rows", value: 500 },
      { op: "remove", path: "/isatvon_definition/notes/legacy" },
    ],
    isatvon_sha256_before: "b".repeat(64),
    isatvon_sha256_after: "c".repeat(64),
  },
};
const HOLD = (page: Page) => page.getByRole("region", { name: "Held ISATVON patch" });
const DIALOG = (page: Page, name: string) => page.getByRole("dialog", { name });

async function open(page: Page, baseURL: string, held: (route: Route) => unknown) {
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
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/held-patch`,
    (r) => held(r),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Builds" }).click();
  await expect(page.getByRole("list", { name: "Sub-Skill builds" })).toBeVisible();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e715_held_patch loaded the card shows the gate, the rationale and every operation", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, HELD));
  const card = HOLD(page);
  await expect(card).toContainText("Attempt 2 proposed it after the Conformance gate failed");
  await expect(card).toContainText("up to 480 rows");
  await expect(page.getByRole("list", { name: "Patch operations" })).toContainText(
    "gate / max_rows",
  );
  await expect(page.getByRole("list", { name: "Patch operations" })).toContainText("500");
  await shot(page, "held-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "held-1c-loaded-wide");
});

test("e715_held_patch accept sends the hash as If-Match with a key and says the build started", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, HELD));
  let headers: Record<string, string> = {};
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/held-patch/accept`,
    (r) => {
      headers = r.request().headers();
      return json(r, { sub_skill_id: SUB, build_no: 5, decision: "ACCEPTED" }, 202);
    },
  );
  await page.getByRole("button", { name: "Accept the patch" }).click();
  await expect(DIALOG(page, "Accept the ISATVON patch?")).toBeVisible();
  await shot(page, "held-2-accept-dialog");
  await DIALOG(page, "Accept the ISATVON patch?").getByRole("button", { name: "Accept" }).click();
  await expect(page.getByText("Patch accepted: build 5 started.")).toBeVisible();
  expect(headers["if-match"]).toBe(`"${SHA}"`);
  expect(headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
  await shot(page, "held-3-accepted");
});

test("e715_held_patch reject needs a reason and sends it", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, HELD));
  let body: unknown;
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/held-patch/reject`,
    (r) => {
      body = r.request().postDataJSON();
      return json(r, { sub_skill_id: SUB, build_no: 6, decision: "REJECTED" }, 202);
    },
  );
  await page.getByRole("button", { name: "Reject it" }).click();
  const dialog = DIALOG(page, "Reject the ISATVON patch?");
  await dialog.getByRole("button", { name: "Reject" }).click({ force: true }); // aria-disabled without a reason
  expect(body).toBeUndefined(); // no reason, nothing sent
  await shot(page, "held-4-reject-dialog");
  await dialog.getByLabel("Reason").fill("The 100 row bound is a contract with the client");
  await dialog.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Patch rejected: build 6 started.")).toBeVisible();
  expect(body).toEqual({ reason: "The 100 row bound is a contract with the client" });
  await shot(page, "held-5-rejected");
});

test("e715_held_patch a refused decision shows the servers reason in the dialog", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, HELD));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/held-patch/accept`,
    (r) =>
      problem(
        r,
        412,
        "Precondition Failed",
        "The Sub-Skill changed since you opened it. Reload and look again.",
      ),
  );
  await page.getByRole("button", { name: "Accept the patch" }).click();
  await DIALOG(page, "Accept the ISATVON patch?").getByRole("button", { name: "Accept" }).click();
  await expect(DIALOG(page, "Accept the ISATVON patch?")).toContainText(
    "changed since you opened it",
  );
  await shot(page, "held-6-refused");
});

test("e715_held_patch loading shows a skeleton until the patch arrives", async ({
  page,
  baseURL,
}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, HELD);
  });
  await expect(page.getByText("Loading the patch for Intake the RFQ")).toBeAttached();
  await shot(page, "held-7-loading");
  release();
  await expect(HOLD(page)).toBeVisible();
});

test("e715_held_patch empty no patch waiting is a quiet line", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { held: null }));
  await expect(page.getByText("No ISATVON patch is waiting for Intake the RFQ")).toBeVisible();
  await expect(HOLD(page)).toHaveCount(0);
  await shot(page, "held-8-empty");
});

test("e715_held_patch error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The store is unreachable") : json(r, HELD),
  );
  await expect(page.getByText("The store is unreachable")).toBeVisible();
  await shot(page, "held-9-error");
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(HOLD(page)).toBeVisible();
});

test("e715_held_patch forbidden an account that may not see the project gets the forbidden state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this patch")).toBeVisible();
  await shot(page, "held-10-forbidden");
});
