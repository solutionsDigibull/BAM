import { expect, test, type Page, type Route } from "@playwright/test";

// E3.9 Admin Projects. The default project runs `next dev` with the dev role cookie and no sbam-api, so the
// API is faked at the proxy (`/api/proxy/api/v1/**`); a stack run (E2E_BASE_URL) exercises the real one.

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

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
const FDE_A1 = {
  id: "0190f3a2-7b1c-7d4e-8f00-0000000000f1",
  email: "fde-a1@acme.example",
  display_name: "fde-A1",
  portal_roles: ["sbam-fde"],
  status: "ACTIVE",
  last_login_at: null,
  created_at: "2026-10-14T09:00:00Z",
  row_version: 1,
};

test("e3_9_t07 FDE on /admin/projects gets Forbidden and no data request", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/projects");
  await expect(page.getByText("Forbidden")).toBeVisible();
  await expect(page.getByRole("button", { name: /New project/ })).toHaveCount(0);
  expect(apiCalls).toEqual([]);
});

test("e3_9_t02 admin creates a project and assigns fde-A1", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const projects: Record<string, unknown>[] = [];
  let members: string[] = [];
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/proxy/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    const method = req.method();
    if (method === "GET" && path === "/admin/projects") return json(route, { items: projects });
    if (method === "POST" && path === "/admin/projects") {
      const body = req.postDataJSON() as { name: string; client_name: string | null };
      const created = { ...PROJECT, name: body.name, client_name: body.client_name };
      projects.push(created);
      return json(route, created, 201);
    }
    if (method === "GET" && path === "/admin/users") return json(route, { items: [FDE_A1] });
    if (path === `/admin/projects/${PROJECT.id}/members`) {
      if (method === "PUT") members = (req.postDataJSON() as { user_ids: string[] }).user_ids;
      return json(route, {
        project_id: PROJECT.id,
        members: members.map((id) => ({ ...FDE_A1, id })),
      });
    }
    return json(route, { status: 404, title: "Not found", code: "NOT_FOUND" }, 404);
  });

  await page.goto("/admin/projects");
  await expect(page.getByText("Create your first project")).toBeVisible();
  await page.getByRole("button", { name: /New project/ }).click();
  await page.getByLabel("Project name").fill(PROJECT.name);
  await page.getByLabel("Client").fill(PROJECT.client_name);
  await page.getByRole("button", { name: "Create project" }).click();

  // After create, the FDE picker opens as the next step.
  const picker = page.getByRole("dialog").filter({ hasText: "FDEs ·" });
  await expect(picker).toBeVisible();
  await picker.getByRole("combobox").click();
  // antd's role=option nodes are hidden a11y stand-ins; click the visible dropdown item.
  await page.locator(".ant-select-item-option", { hasText: "fde-A1" }).click();
  await picker.getByRole("button", { name: "Save" }).click();

  await expect(page.getByText("FDE assignment saved")).toBeVisible();
  expect(members).toEqual([FDE_A1.id]);
  await expect(page.getByRole("table").getByText(PROJECT.name, { exact: true })).toBeVisible();
  // The "fde-A1 sees it in /projects" half needs the FDE My Projects screen (its own issue) and the API.
});
