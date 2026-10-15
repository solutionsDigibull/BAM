import { expect, test, type Page, type Route } from "@playwright/test";

// E2.6 Users & Roles. Like admin-projects.spec.ts: the default project runs `next dev` with the dev role
// cookie and no sbam-api, so the API is faked at the proxy (`/api/proxy/api/v1/**`).

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

const ADMIN_A = {
  id: "0190f3a2-7b1c-7d4e-8f00-0000000000a1",
  email: "admin-a@acme.com",
  display_name: "admin-A",
  portal_roles: ["sbam-admin"],
  status: "ACTIVE",
  last_login_at: "2026-10-15T08:00:00Z",
  created_at: "2026-10-14T09:00:00Z",
  row_version: 1,
};

test("e2_6_t06 FDE on /admin/users gets Forbidden and no data request", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/users");
  await expect(page.getByText("Forbidden")).toBeVisible();
  await expect(page.getByRole("button", { name: /Invite user/ })).toHaveCount(0);
  expect(apiCalls).toEqual([]);
});

test("e2_6_t03 admin invites fde3@acme.com as FDE and the row shows INVITED", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const users: Record<string, unknown>[] = [ADMIN_A];
  let invite: unknown;
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/proxy/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    if (req.method() === "GET" && path === "/admin/users") return json(route, { items: users });
    if (req.method() === "POST" && path === "/admin/users/invite") {
      invite = req.postDataJSON();
      const body = invite as { email: string; roles: string[] };
      const created = {
        ...ADMIN_A,
        id: "0190f3a2-7b1c-7d4e-8f00-0000000000f3",
        email: body.email,
        display_name: null,
        portal_roles: body.roles,
        status: "INVITED",
        last_login_at: null,
      };
      users.push(created);
      return json(route, created, 201);
    }
    return json(route, { status: 404, title: "Not found", code: "NOT_FOUND" }, 404);
  });

  await page.goto("/admin/users");
  await expect(page.getByText("Invite your first FDE")).toBeVisible();
  await page
    .getByRole("button", { name: /Invite user/ })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email").fill("fde3@acme.com");
  await dialog.getByRole("button", { name: "Send invite" }).click();

  await expect(page.getByText("Invite sent to fde3@acme.com")).toBeVisible();
  const row = page.getByRole("row", { name: /fde3@acme\.com/ });
  await expect(row.locator('[data-status="INVITED"]')).toBeVisible();
  expect(invite).toEqual({ email: "fde3@acme.com", roles: ["sbam-fde"] });
});
