import { expect, test, type Page } from "@playwright/test";

// E3.12 runtime registry view. The default project runs `next dev` without sbam-api, so the registry
// API is faked at the proxy; a stack run (E2E_BASE_URL) exercises the real one.

const DIGEST = "sha256:" + "ab12".repeat(16);
const REGISTRY = {
  image_digest: DIGEST,
  python_version: "3.12.15",
  generated_at: "2026-10-15T09:00:00Z",
  content_sha256: "sha256:" + "0".repeat(64),
  packages: [
    { name: "anyio", version: "4.6.0", top_level_modules: ["anyio"], allowed: false },
    { name: "pydantic", version: "2.13.0", top_level_modules: ["pydantic"], allowed: true },
  ],
};

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

test("e3_12_t04 FDE reads the registry with no edit actions", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  await page.route("**/api/proxy/api/v1/registry", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(REGISTRY) }),
  );
  await page.goto("/registry");
  await expect(page.getByTestId("image-digest")).toContainText(DIGEST);
  await expect(
    page.locator(".ant-layout-sider").getByRole("link", { name: "Runtime registry" }),
  ).toBeVisible();
  await expect(page.getByText("Not allowed", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /edit|delete|add|save|upload/i })).toHaveCount(0);
});

test("e3_12_t06 the page issues only GET requests", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const api: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && api.push(r.method()));
  await page.route("**/api/proxy/api/v1/admin/registry", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(REGISTRY) }),
  );
  await page.goto("/admin/registry");
  await expect(page.getByTestId("image-digest")).toBeVisible();
  await page.getByRole("button", { name: /refresh/i }).click();
  await page.getByLabel("Search packages").fill("pyd");
  await expect(page.getByText("1 of 2 packages")).toBeVisible();
  expect(api.length).toBeGreaterThan(0);
  expect(new Set(api)).toEqual(new Set(["GET"]));
});

test("anonymous /registry goes to the FDE login", async ({ request }) => {
  const r = await request.get("/registry", { maxRedirects: 0 });
  expect(r.status()).toBe(307);
  expect(r.headers()["location"]).toMatch(/\/login-fde/);
});
