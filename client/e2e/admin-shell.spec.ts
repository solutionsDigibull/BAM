import { expect, test, type Page } from "@playwright/test";

// MUI-6 (#785): the admin shell and /login-admin in the approved BuLLM look. The default project runs
// `next dev` with the dev role cookie and no sbam-api, so the API is faked at the proxy.

const asAdmin = async (page: Page, baseURL: string) => {
  await page
    .context()
    .addCookies([{ name: "sbam_dev_roles", value: "sbam-admin,sbam-fde", url: baseURL }]);
  await page.route(/\/api\/proxy\/api\/v1\/.*/, (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ items: [] }) }),
  );
};

const noOverflow = async (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

for (const width of [320, 390]) {
  test(`mui_6_t08 no page overflow at ${width}px on /admin/projects and /login-admin`, async ({
    page,
    baseURL,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await asAdmin(page, baseURL!);
    await page.goto("/admin/projects");
    const burger = page.getByRole("button", { name: "Open navigation" });
    await expect(burger).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
    await burger.click(); // the open nav overlays the page, it doesn't widen it
    await expect(page.getByRole("button", { name: "Close navigation" })).toBeVisible();
    expect(await noOverflow(page)).toBe(true);

    await page.context().clearCookies();
    await page.goto("/login-admin");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    expect(await noOverflow(page)).toBe(true);
  });
}

test("mui_6_t09 theme toggle persists across reloads and the account menu signs out", async ({
  page,
  baseURL,
}) => {
  await asAdmin(page, baseURL!);
  await page.goto("/admin/projects");
  const shell = page.locator(".portal-shell");
  await expect(shell).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(shell).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator(".portal-shell")).toHaveAttribute("data-theme", "dark");

  const avatar = page.getByRole("button", { name: "Account menu" });
  await avatar.click();
  const menu = page.getByRole("menu", { name: "Account" });
  await expect(menu.getByRole("menuitem", { name: "FDE portal" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(avatar).toBeFocused();
  await avatar.click();
  await menu.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL(/\/login-(admin|fde)/);
});

test("mui_6_t10 /login-admin is the BuLLM login with the admin choices", async ({ page }) => {
  await page.goto("/login-admin");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in as Admin" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in as Super-admin" })).toBeVisible();
  await expect(page.getByRole("link", { name: "FDE? Use the FDE login" })).toBeVisible();
});
