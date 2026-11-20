import { expect, test, type Page, type Route } from "@playwright/test";

// #776: the Admin sets the token caps of one project. The default project runs `next dev` with the dev role cookie and
// no sbam-api, so the API is faked at the proxy. Set UI_SHOTS_DIR to save a screenshot of each state.

const PROJECT = {
  id: "0190f3a2-7b1c-7d4e-8f00-000000000001",
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "IDLE",
  row_version: 1,
  created_at: "2026-10-14T10:00:00Z",
  updated_at: "2026-10-14T10:00:00Z",
};
const DEFAULT = {
  sub_skill_tokens: 200000,
  workflow_tokens: 2000000,
  knowledge_tokens: 30000000,
  first_alert_pct: 50,
  second_alert_pct: 80,
};
const CAPS_PATH = `/admin/projects/${PROJECT.id}/token-caps`;
const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body), headers });
const problem = (route: Route, status: number, title: string, detail?: string) =>
  json(route, { type: "about:blank", title, status, detail }, status);

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};
const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};
type Handler = (route: Route, method: string) => unknown;
async function fake(page: Page, handler: Handler) {
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/admin/projects") return handler(route, "LIST");
    if (path === CAPS_PATH) return handler(route, route.request().method());
    return problem(route, 404, "Not Found");
  });
}

test("wg23_caps an admin sets the caps of a project and saves with If-Match", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  let version = 0;
  let caps = { ...DEFAULT };
  let ifMatch: string | undefined;
  await fake(page, async (route, kind) => {
    if (kind === "LIST") return json(route, { items: [PROJECT] });
    if (kind === "PUT") {
      ifMatch = route.request().headers()["if-match"];
      caps = route.request().postDataJSON() as typeof caps;
      version += 1;
    }
    return json(route, { caps, row_version: version, is_default: version === 0 }, 200, {
      etag: `"${version}"`,
    });
  });
  await page.goto("/admin/token-caps");
  await expect(page.getByText("Using the platform defaults")).toBeVisible();
  await shot(page, "caps-1-defaults");
  await page.getByLabel(/Workflow build/).fill("3000000");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Using the platform defaults")).toHaveCount(0);
  expect(ifMatch).toBe('"0"');
  expect(caps.workflow_tokens).toBe(3000000);
  await shot(page, "caps-2-saved");
  await expect(page.getByRole("link", { name: "Token caps" }).first()).toBeVisible();
});

test("wg23_caps empty no projects", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fake(page, (route) => json(route, { items: [] }));
  await page.goto("/admin/token-caps");
  await expect(page.getByText(/No projects yet/)).toBeVisible();
  await shot(page, "caps-3-empty");
});

test("wg23_caps error shows the reason of the failure", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fake(page, (route, kind) => {
    if (kind === "LIST")
      return problem(route, 500, "Server error", "The projects could not be read");
    return problem(route, 404, "Not Found");
  });
  await page.goto("/admin/token-caps");
  // the app's query client retries a failed read a few times before it shows the error
  await expect(page.getByText(/The projects could not be read/)).toBeVisible({ timeout: 20_000 });
  await shot(page, "caps-4-error");
});

test("wg23_caps a stale write says so", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fake(page, (route, kind) => {
    if (kind === "LIST") return json(route, { items: [PROJECT] });
    if (kind === "PUT") return problem(route, 412, "Precondition failed", "Resource changed");
    return json(route, { caps: DEFAULT, row_version: 1, is_default: false }, 200, { etag: '"1"' });
  });
  await page.goto("/admin/token-caps");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("These limits were changed by someone else")).toBeVisible();
  await shot(page, "caps-5-stale");
});

test("wg23_caps forbidden an FDE gets Forbidden and no data request", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/token-caps");
  await expect(page.getByText("Forbidden")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
  expect(apiCalls).toEqual([]);
  await shot(page, "caps-6-forbidden");
});

test("wg23_caps loading shows a skeleton until the projects arrive", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await fake(page, async (route, kind) => {
    if (kind === "LIST") {
      await gate;
      return json(route, { items: [PROJECT] });
    }
    return json(route, { caps: DEFAULT, row_version: 0, is_default: true }, 200, { etag: '"0"' });
  });
  await page.goto("/admin/token-caps");
  await expect(page.locator(".ant-skeleton")).toBeVisible();
  await shot(page, "caps-7-loading");
  release();
  await expect(page.getByText("Using the platform defaults")).toBeVisible();
});
