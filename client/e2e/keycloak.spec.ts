import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

// E2.5 against the running Compose stack with real Keycloak logins (seed users from
// compose/keycloak/seed.sh). Runs only in the "keycloak" project: E2E_BASE_URL + E2E_KEYCLOAK=1.
// E2E_SEED_PASSWORD = SEED_USER_PASSWORD; ENV_FILE = the stack's env file (for stopping Keycloak).

const PASSWORD = process.env.E2E_SEED_PASSWORD ?? "";
const KEYCLOAK = process.env.E2E_KEYCLOAK_URL ?? "http://localhost:8080";
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test.beforeAll(() => {
  if (!PASSWORD) throw new Error("set E2E_SEED_PASSWORD (SEED_USER_PASSWORD from compose/.env)");
});

async function keycloakLogin(page: Page, portal: "admin" | "fde", username: string, query = "") {
  await page.goto(`/login-${portal}${query}`);
  await page.getByRole("button", { name: "Sign in with Keycloak" }).click();
  await page.waitForURL(/\/realms\/bullm\//);
  // Organizations make the login identity-first: username page, then password page.
  await page.locator("#username").fill(username);
  await page.locator("#kc-login").click();
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
}

const pathOf = (page: Page) => new URL(page.url()).pathname;

test("e2_5_t01 admin login lands on the admin portal; super-admin on tenants", async ({
  browser,
}) => {
  const admin = await (await browser.newContext()).newPage();
  await keycloakLogin(admin, "admin", "admin-a");
  await admin.waitForURL("**/admin/projects");
  expect(pathOf(admin)).toBe("/admin/projects");

  const root = await (await browser.newContext()).newPage();
  await keycloakLogin(root, "admin", "super-admin");
  await root.waitForURL("**/admin/tenants");
  expect(pathOf(root)).toBe("/admin/tenants");
});

test("e2_5_t02 FDE login lands on projects", async ({ page }) => {
  await keycloakLogin(page, "fde", "fde-a1");
  await page.waitForURL("**/projects");
  expect(pathOf(page)).toBe("/projects");
});

test("e2_5_t03 FDE session on an admin route gets the wrong-portal page and no admin data", async ({
  page,
}) => {
  await keycloakLogin(page, "fde", "fde-a1");
  await page.waitForURL("**/projects");
  const proxied: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/proxy/")) proxied.push(r.url());
  });
  const res = await page.goto("/admin/masters");
  expect(res?.status()).toBe(403);
  await expect(page.getByText("The Admin portal needs the sbam-admin role.")).toBeVisible();
  expect(proxied.filter((u) => u.includes("/admin"))).toEqual([]);
});

test("e2_5_t04 admin at the FDE login is told to use the Admin login; no session", async ({
  page,
}) => {
  await keycloakLogin(page, "fde", "admin-a");
  await page.waitForURL(/\/login-fde\?error=WrongPortal/);
  await expect(page.getByText("This account cannot use this portal")).toBeVisible();
  await expect(page.getByRole("link", { name: "Admin? Use the Admin login" })).toBeVisible();
  await page.goto("/projects");
  expect(pathOf(page)).toBe("/login-fde"); // no FDE session was created
});

test("e2_5_t05 an external callbackUrl is ignored after login", async ({ page }) => {
  await keycloakLogin(page, "fde", "fde-a1", "?callbackUrl=https%3A%2F%2Fevil.example");
  await page.waitForURL("**/projects");
  expect(page.url()).not.toContain("evil.example");
  expect(pathOf(page)).toBe("/projects");
});

test("e2_5_t06 no token reaches browser JS", async ({ page, context }) => {
  await keycloakLogin(page, "fde", "fde-a1");
  await page.waitForURL("**/projects");
  const exposed = await page.evaluate(async () => {
    const session = await (await fetch("/api/auth/session")).text();
    return JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie, session]);
  });
  expect(exposed).not.toMatch(/eyJ[\w-]+\.[\w-]+|accessToken|idToken|refreshToken/);
  const sessionCookies = (await context.cookies()).filter((c) => c.name.includes("session-token"));
  expect(sessionCookies.length).toBeGreaterThan(0);
  expect(sessionCookies.every((c) => c.httpOnly && c.sameSite === "Lax")).toBe(true);
});

test("e2_5 logout ends the Keycloak session too", async ({ page }) => {
  await keycloakLogin(page, "fde", "fde-a2");
  await page.waitForURL("**/projects");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL("**/login-fde");
  await page.getByRole("button", { name: "Sign in with Keycloak" }).click();
  // Without RP-initiated logout Keycloak would sign straight back in; it must ask again.
  await expect(page.locator("#username")).toBeVisible();
});

test("e2_5_t07 the session refreshes silently after the 300 s access token expires", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.E2E_SLOW,
    "idles 5 min; CI runs it nightly and on manual runs (E2E_SLOW=1)",
  );
  test.setTimeout(9 * 60_000);
  await keycloakLogin(page, "fde", "fde-a1");
  await page.waitForURL("**/projects");
  const cookie = async () =>
    (await context.cookies()).filter((c) => c.name.includes("session-token")).map((c) => c.value);
  const before = await cookie();
  await page.waitForTimeout(305_000); // past accessTokenLifespan (300 s)
  const res = await page.request.get("/api/proxy/api/v1/e2e-refresh-probe");
  expect(res.status()).not.toBe(401); // the proxy had a valid (refreshed) token to forward
  expect(await cookie()).not.toEqual(before); // rotated tokens were written back
  await page.goto("/projects");
  expect(pathOf(page)).toBe("/projects"); // no login prompt
});

test("e2_5_t08 Keycloak down: sign-in unavailable with a reference id", async ({ page }) => {
  test.setTimeout(6 * 60_000);
  const compose = (...args: string[]) =>
    execFileSync(
      "docker",
      [
        "compose",
        "--env-file",
        path.resolve(REPO, process.env.ENV_FILE ?? "compose/.env"),
        "-f",
        path.resolve(REPO, "compose/compose.yaml"),
        ...args,
      ],
      { stdio: "inherit" },
    );
  compose("stop", "keycloak");
  try {
    await page.goto("/login-fde");
    await page.getByRole("button", { name: "Sign in with Keycloak" }).click();
    await page.waitForURL(/\/login-fde\?error=/);
    await expect(page.getByText("Sign-in is unavailable, try again later.")).toBeVisible();
    await expect(page.getByText(/^Reference: [0-9a-f-]{36}$/)).toBeVisible();
  } finally {
    compose("start", "keycloak");
    await expect
      .poll(
        async () => {
          try {
            return (await fetch(`${KEYCLOAK}/realms/bullm`)).status;
          } catch {
            return 0;
          }
        },
        { timeout: 240_000, intervals: [3_000] },
      )
      .toBe(200);
  }
});
