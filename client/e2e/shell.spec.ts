import { expect, test, type Page } from "@playwright/test";

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

test("e1_13_t01 admin shell shows projects, masters, users, registry, tenants", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await page.goto("/");
  await expect(page).toHaveURL(/\/admin\/projects$/);
  const nav = page.locator(".ant-layout-sider");
  for (const item of ["Projects", "Masters", "Users & Roles", "Registry", "Tenants"]) {
    await expect(nav.getByRole("link", { name: item })).toBeVisible();
  }
});

test("e1_13_t02 FDE shell lists projects and has no Workflow create action", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByText("No projects assigned — ask your Admin.")).toBeVisible();
  await page.goto("/projects/0190f3a2-7b1c-7d4e-8f00-123456789abc/workflows");
  await expect(page.getByText(/SBAM creates the Workflow/)).toBeVisible();
  await expect(page.getByRole("button", { name: /create|new workflow/i })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /create|new workflow/i })).toHaveCount(0);
});

test("e1_13_t03 FDE on an admin route gets Forbidden and no admin content", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  await page.goto("/admin/masters");
  await expect(page.getByText("Forbidden")).toBeVisible();
  await expect(page.getByText(/Import the BAM 0.2 workbook/)).toHaveCount(0);
});

test("e1_13_t08 invalid project id shows not-found", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/") && apiCalls.push(r.url()));
  await page.goto("/projects/not-a-uuid");
  await expect(page.getByText("Not found")).toBeVisible();
  expect(apiCalls).toEqual([]);
});

// Deep-test regressions: guards run in src/proxy.ts before rendering, so no page payload ships.
test("guard_t01 anonymous admin route is a real 307 and ships no page content", async ({
  request,
}) => {
  const r = await request.get("/admin/masters", { maxRedirects: 0 });
  expect(r.status()).toBe(307);
  expect(r.headers()["location"]).toMatch(/\/login-admin\?callbackUrl=%2Fadmin%2Fmasters$/);
  expect(await r.text()).not.toContain("Import the BAM");
});

test("guard_t02 FDE on admin route gets 403 and the admin page is never rendered", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const r = await page.request.get("/admin/masters", { maxRedirects: 0 });
  expect(r.status()).toBe(403);
  expect(await r.text()).not.toMatch(/Import the BAM|No masters loaded/);
});

test("guard_t03 invalid project or workflow id is a 404 status", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  for (const path of [
    "/projects/not-a-uuid",
    "/projects/0190f3a2-7b1c-7d4e-8f00-123456789abc/workflows/nope/code",
  ]) {
    const r = await page.goto(path);
    expect(r?.status()).toBe(404);
    await expect(page.getByText("Not found")).toBeVisible();
  }
});

test("guard_t04 security headers are set and X-Powered-By is hidden", async ({ request }) => {
  const h = (await request.get("/login-fde")).headers();
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(h["x-powered-by"]).toBeUndefined();
});
