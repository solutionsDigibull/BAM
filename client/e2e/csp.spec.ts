import { expect, test, type Page } from "@playwright/test";

// A-11 (#441): every page carries a nonce CSP, and the main screens run under it with no violation.
// (The dev server adds 'unsafe-eval'; a production build is checked by the same header test.)

const violations = (page: Page) => {
  const seen: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) seen.push(m.text());
  });
  return seen;
};

const asRole = (page: Page, roles: string, baseURL: string) =>
  page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);

test("a11_t01 public pages send a nonce CSP and run under it", async ({ page }) => {
  const seen = violations(page);
  for (const path of ["/login-fde", "/login-admin", "/does-not-exist"]) {
    const res = await page.goto(path);
    const csp = res?.headers()["content-security-policy"] ?? "";
    expect(csp, path).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);
    await page.waitForLoadState("networkidle");
  }
  expect(seen).toEqual([]);
});

test("a11_t02 every response has a different nonce", async ({ page }) => {
  const nonce = async () =>
    ((await page.goto("/login-fde"))?.headers()["content-security-policy"] ?? "").match(
      /'nonce-([^']+)'/,
    )?.[1];
  const a = await nonce();
  const b = await nonce();
  expect(a).toBeTruthy();
  expect(a).not.toBe(b);
});

test("a11_t03 the nonce is on the framework scripts, and an injected inline script does not run", async ({
  page,
}) => {
  await page.goto("/login-fde");
  const unstamped = await page.evaluate(
    () =>
      [...document.querySelectorAll("script:not([data-nextjs-dev-overlay])")].filter(
        (s) => !(s as HTMLScriptElement).src && !(s as HTMLScriptElement).nonce,
      ).length,
  );
  expect(unstamped).toBe(0);
});

test("a11_t03b an inline script in the served HTML (an XSS) does not run", async ({ page }) => {
  await page.route("**/login-fde", async (route) => {
    const res = await route.fetch();
    const body = (await res.text()).replace(
      "</body>",
      '<script>window.__injected = true</script><img src=x onerror="window.__injected2 = true"></body>',
    );
    await route.fulfill({ response: res, body });
  });
  const seen = violations(page);
  await page.goto("/login-fde");
  await page.waitForLoadState("networkidle");
  const ran = await page.evaluate(() => {
    const w = window as unknown as Record<string, boolean | undefined>;
    return Boolean(w.__injected || w.__injected2);
  });
  expect(ran).toBe(false);
  expect(seen.some((m) => /Content Security Policy|Refused to/i.test(m))).toBe(true);
});

test("a11_t04 portal screens run under the CSP without a violation", async ({ page, baseURL }) => {
  const seen = violations(page);
  await page.route("**/api/proxy/api/v1/**", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ items: [] }) }),
  );
  await asRole(page, "sbam-fde", baseURL!);
  for (const path of ["/projects", "/registry", "/forbidden/admin"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }
  await asRole(page, "sbam-admin", baseURL!);
  for (const path of ["/admin/projects", "/admin/masters", "/admin/users"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }
  expect(seen).toEqual([]);
});

test("a11_t05 the guard still applies: no session is sent to sign in", async ({ page }) => {
  await page.goto("/projects");
  await expect(page).toHaveURL(/\/login-fde\?callbackUrl=/);
});
