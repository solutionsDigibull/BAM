import { expect, test } from "@playwright/test";

import { guard, keycloakLogin, USERS } from "./stack";

// E01 (E15.1-T01): Admin and FDE log in through Keycloak; each lands in their own portal, and the
// wrong portal is refused without a session.
test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t01 admin and FDE land in their own portals; the wrong portal is refused", async ({
  browser,
}) => {
  const admin = await (await browser.newContext()).newPage();
  await keycloakLogin(admin, "admin", USERS.adminA);
  await admin.waitForURL("**/admin/projects");

  const fde = await (await browser.newContext()).newPage();
  await keycloakLogin(fde, "fde", USERS.fdeA);
  await fde.waitForURL("**/projects");
  // an FDE session on an Admin route: the wrong-portal page, never admin data
  const res = await fde.goto("/admin/projects");
  expect(res?.status()).toBe(403);

  // an admin at the FDE login: refused, and no FDE session exists afterwards
  const wrong = await (await browser.newContext()).newPage();
  await keycloakLogin(wrong, "fde", USERS.adminA);
  await wrong.waitForURL(/\/login-fde\?error=WrongPortal/);
  await wrong.goto("/projects");
  expect(new URL(wrong.url()).pathname).toBe("/login-fde");
});
