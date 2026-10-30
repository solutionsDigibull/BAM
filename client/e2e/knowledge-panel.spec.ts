import { createHash } from "node:crypto";

import { expect, test, type Page, type Route } from "@playwright/test";

// Knowledge status and coverage panel (E5.6). The API is faked at the proxy, as in fde-workspace.spec.ts.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const KV = "0190f3a2-7b1c-7d4e-8f00-00000000c0a1";
const API = "**/api/proxy/api/v1";
// the projects list, with or without the `?limit=` a list that follows its cursor sends
const PROJECTS = /\/api\/proxy\/api\/v1\/projects(\?.*)?$/;
const ZIP = Buffer.from("PK\u0003\u0004 fake okf bundle zip");
const ZIP_SHA = createHash("sha256").update(ZIP).digest("hex");
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing",
  client_name: null,
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "SOW_REVIEW",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};

const row = (over: Record<string, unknown> = {}) => ({
  document_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000d0c1",
  title: "rfq.pdf",
  status: "COVERED",
  chunks: 4,
  entities: 7,
  reason: null,
  ...over,
});

const version = (over: Record<string, unknown> = {}) => ({
  id: KV,
  version: 2,
  status: "READY",
  coverage: { documents: [], entities: 7 },
  error_reason: null,
  created_at: "2026-10-16T10:00:00Z",
  finished_at: "2026-10-16T10:03:00Z",
  okf_sha256: ZIP_SHA,
  counts: { documents: 1, entities: 7, relations: 3, chunks: 4 },
  documents: [row()],
  ...over,
});

async function open(page: Page, baseURL: string, versions: () => unknown[]) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route(PROJECTS, (r) => json(r, { items: [project] }));
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) =>
    json(r, { versions: versions() }),
  );
  await page.goto(`/projects/${PROJECT}/knowledge`);
}

test("e5_6_t01 building shows progress, then READY with counts; polling stops", async ({
  page,
  baseURL,
}) => {
  let polls = 0;
  await open(page, baseURL!, () => {
    polls += 1;
    return [
      polls < 3 ? version({ status: "BUILDING", finished_at: null, okf_sha256: null }) : version(),
    ];
  });
  await expect(page.getByLabel("Building progress")).toBeVisible();
  await expect(page.getByText("Ready", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel("Building progress")).toHaveCount(0);
  const settled = polls;
  await page.waitForTimeout(9_000);
  expect(polls).toBe(settled);
});

test("e5_6_t02 download saves okf-<project>-v<n>.zip with the hash shown", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, () => [version()]);
  await page.route(`${API}/projects/${PROJECT}/knowledge/${KV}/okf-bundle`, (r) =>
    r.fulfill({
      status: 200,
      body: ZIP,
      headers: { "content-type": "application/zip", "content-disposition": "attachment" },
    }),
  );
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Download OKF bundle" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("okf-EMS-RFQ-costing-v2.zip");
  // Playwright cancels downloads served by route.fulfill, so the saved bytes cannot be read back
  // here; the panel shows the version's okf_sha256, which the API's zip carries (#82 test).
  expect(download.url()).toContain(`/knowledge/${KV}/okf-bundle`);
  await expect(page.getByText(ZIP_SHA.slice(0, 8), { exact: false })).toBeVisible();
});

test("e5_6_t03 a failed document is listed with its reason and Rebuild", async ({
  page,
  baseURL,
}) => {
  let posted = 0;
  await open(page, baseURL!, () => [
    version({
      documents: [
        row(),
        row({ document_version_id: KV, title: "scan.pdf", status: "FAILED", reason: "NOT_PARSED" }),
      ],
    }),
  ]);
  await page.route(`${API}/projects/${PROJECT}/knowledge/rebuild`, (r) => {
    posted += 1;
    return json(r, { run: {} }, 202);
  });
  const failed = page.getByRole("row", { name: /scan\.pdf/ });
  await expect(failed).toContainText("NOT_PARSED");
  await failed.getByRole("button", { name: "Rebuild" }).click();
  await expect(page.getByText("Rebuild started.")).toBeVisible();
  expect(posted).toBe(1);
});

test("e5_6_t05 rebuild conflict shows the error toast", async ({ page, baseURL }) => {
  await open(page, baseURL!, () => [version()]);
  await page.route(`${API}/projects/${PROJECT}/knowledge/rebuild`, (r) =>
    r.fulfill({
      status: 409,
      contentType: "application/problem+json",
      body: JSON.stringify({
        status: 409,
        code: "KNOWLEDGE_BUILDING",
        title: "Knowledge is being built",
      }),
    }),
  );
  await page.getByRole("button", { name: "Rebuild" }).first().click();
  await expect(page.getByText("Knowledge is already building.")).toBeVisible();
});

test("e5_6_t06 another project's panel is not found and shows no data", async ({
  page,
  baseURL,
}) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL! }]);
  await page.route(PROJECTS, (r) => json(r, { items: [] }));
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) =>
    r.fulfill({
      status: 404,
      contentType: "application/problem+json",
      body: JSON.stringify({ status: 404, code: "NOT_FOUND", title: "Not found" }),
    }),
  );
  await page.goto(`/projects/${PROJECT}/knowledge`);
  // the data layer retries a failed read a few times before it shows the error
  await expect(page.getByText(/not found/i).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Knowledge v2")).toHaveCount(0);
});

test("e5_6_t08 a double click on Rebuild sends one POST with an Idempotency-Key", async ({
  page,
  baseURL,
}) => {
  const keys: (string | undefined)[] = [];
  await open(page, baseURL!, () => [version()]);
  await page.route(`${API}/projects/${PROJECT}/knowledge/rebuild`, async (r) => {
    keys.push(r.request().headers()["idempotency-key"]);
    await new Promise((resolve) => setTimeout(resolve, 300));
    return json(r, { run: {} }, 202);
  });
  await page.getByRole("button", { name: "Rebuild" }).first().dblclick();
  await expect(page.getByText("Rebuild started.")).toBeVisible();
  expect(keys).toHaveLength(1);
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
});
