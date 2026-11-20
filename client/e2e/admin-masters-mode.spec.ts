import { expect, test, type Page, type Route } from "@playwright/test";

// #869 Admin | Admin Masters mode. Like admin-masters.spec.ts the API is faked at the proxy on the default
// `next dev` project (dev role cookie). Screenshots go to SHOTS_DIR when it is set.

// next dev compiles each route on first visit, which can take longer than the default 5 s.
const slow = expect.configure({ timeout: 45_000 });

const SHOTS = process.env.SHOTS_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });

const project = (n: number, over: Record<string, unknown> = {}) => ({
  id: `0190f3a2-7b1c-7d4e-8f00-00000000000${n}`,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: `Project ${n}`,
  client_name: `Client ${n}`,
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "IDLE",
  row_version: 1,
  created_at: "2026-10-14T10:00:00Z",
  updated_at: `2026-10-1${n}T10:00:00Z`,
  ...over,
});
const PROJECTS = [project(1), project(2), project(3, { status: "ARCHIVED" })];

const LAYOUT = {
  types: ["ORGANIZATION", "RULES", "AGENTS", "SKILLS", "FILETYPE"].map((t) => ({
    master_type: t,
    tab: t[0] + t.slice(1).toLowerCase(),
    editable: t !== "FILETYPE",
    row_label_prefix: t,
    columns: [{ header: "Name", field: "name", description: "Name" }],
    payload_schema: {},
  })),
  readme: { title: "BuLLM - Admin Masters (BAM)", rows: [["1. STRUCTURE", null, null, null]] },
};

async function fakeApi(page: Page, projects: unknown[] = PROJECTS) {
  const seen: string[] = [];
  await page.route("**/api/proxy/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/proxy/api/v1", "");
    seen.push(`${path}${url.search}`);
    if (path === "/admin/projects") return json(route, { items: projects, next_cursor: null });
    const one = /^\/admin\/projects\/([\w-]+)$/.exec(path);
    if (one) {
      const p = (projects as { id: string }[]).find((x) => x.id === one[1]);
      return p ? json(route, p) : json(route, { title: "Not found", status: 404 }, 404);
    }
    if (path === "/admin/master-types") return json(route, LAYOUT);
    if (path === "/admin/filetypes") return json(route, []);
    if (path.startsWith("/admin/master-documents")) return json(route, { items: [] });
    if (/^\/admin\/masters\/\w+$/.test(path)) return json(route, { items: [], next_cursor: null });
    return json(route, { title: "Not found", status: 404 }, 404);
  });
  return seen;
}

test("mui_8_t06 toggle round-trip: Admin -> Admin Masters -> Admin, same session", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fakeApi(page);
  await page.goto("/admin/projects");
  const toggle = page.getByRole("navigation", { name: "Admin mode" });
  await slow(toggle.getByRole("link", { name: "Admin", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await slow(page.locator("#portal-nav").getByRole("link", { name: "Masters" })).toHaveCount(0);
  await shot(page, "01-admin-mode");

  await toggle.getByRole("link", { name: "Admin Masters" }).click();
  await slow(page).toHaveURL(/\/admin\/masters$/);
  await slow(toggle.getByRole("link", { name: "Admin Masters" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  const side = page.locator("#portal-nav");
  await slow(side.getByRole("link", { name: "Projects" })).toBeVisible();
  await slow(side.getByRole("link", { name: "Tenant masters" })).toBeVisible();
  await slow(side.getByRole("link", { name: "Users & Roles" })).toHaveCount(0);
  await slow(page.getByRole("link", { name: "Project 2" })).toBeVisible();
  await shot(page, "02-masters-home");

  await page.getByRole("link", { name: "Project 2" }).click();
  await slow(page).toHaveURL(/\/admin\/masters\/projects\/0190f3a2/);
  await slow(page.getByText("Project 2: Masters")).toBeVisible();
  await shot(page, "03-project-masters");

  await side.getByRole("link", { name: "Tenant masters" }).click();
  await slow(page).toHaveURL(/\/admin\/masters\/tenant$/);
  await slow(page.getByRole("tab", { name: "Rules" })).toBeVisible();
  await shot(page, "04-tenant-masters");

  await toggle.getByRole("link", { name: "Admin", exact: true }).click();
  await slow(page).toHaveURL(/\/admin\/projects$/); // never sent back to a login page
  await slow(side.getByRole("link", { name: "Users & Roles" })).toBeVisible();
});

test("mui_8_t06 the list: archived filter, search, empty and error states", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fakeApi(page);
  await page.goto("/admin/masters");
  await slow(page.getByRole("listitem")).toHaveCount(2);
  await page.getByLabel("Show archived").check();
  await slow(page.getByRole("listitem")).toHaveCount(3);
  await page.getByLabel("Search projects").fill("project 3");
  await slow(page.getByRole("listitem")).toHaveCount(1);
  await page.getByLabel("Search projects").fill("zzz");
  await slow(page.getByText("No projects match your search.")).toBeVisible();
  await shot(page, "05-list-no-match");

  const empty = await page.context().newPage();
  await fakeApi(empty, []);
  await empty.goto("/admin/masters");
  await slow(empty.getByText(/No projects yet/)).toBeVisible();
  await slow(empty.getByRole("link", { name: "Go to Admin: Projects" })).toBeVisible();
  await shot(empty, "06-list-empty");

  const bad = await page.context().newPage();
  await bad.route("**/api/proxy/api/v1/**", (r) =>
    json(r, { title: "Down", status: 500, detail: "The database is down" }, 500),
  );
  await bad.goto("/admin/masters");
  await slow(bad.getByText(/The database is down/)).toBeVisible();
  await shot(bad, "07-list-error");
});

test("mui_8_t06 a project's masters: non-UUID and cross-tenant ids are not-found", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const seen = await fakeApi(page);
  await page.goto("/admin/masters/projects/not-a-uuid");
  await slow(page.getByText(/404|not found|could not be found/i).first()).toBeVisible();
  slow(seen.filter((p) => p.startsWith("/admin/masters"))).toEqual([]);

  await page.goto("/admin/masters/projects/0190f3a2-7b1c-7d4e-8f00-0000000000ff");
  await slow(page.getByText("Not found", { exact: true }).first()).toBeVisible();
  await shot(page, "08-project-not-found");

  await page.goto(`/admin/masters/projects/${PROJECTS[0]!.id}`);
  await page.getByRole("tab", { name: "Rules" }).click();
  await slow
    .poll(() =>
      seen.some((p) => p.startsWith("/admin/masters/RULES?") && p.includes("project_id=")),
    )
    .toBe(true);
});

test("mui_8_t07 FDE on /admin/masters gets Forbidden and makes zero API calls", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  for (const path of [
    "/admin/masters",
    "/admin/masters/tenant",
    `/admin/masters/projects/${PROJECTS[0]!.id}`,
  ]) {
    await page.goto(path);
    await slow(page.getByText("Forbidden")).toBeVisible();
  }
  await shot(page, "09-fde-forbidden");
  slow(apiCalls).toEqual([]);
});

for (const width of [320, 390]) {
  test(`mui_8_t08 no horizontal overflow at ${width}px`, async ({ page, baseURL }) => {
    await signInAs(page, "sbam-admin", baseURL!);
    await fakeApi(page);
    await page.setViewportSize({ width, height: 800 });
    for (const [path, name] of [
      ["/admin/projects", "admin"],
      ["/admin/masters", "masters-home"],
      [`/admin/masters/projects/${PROJECTS[0]!.id}`, "project-masters"],
      ["/admin/masters/tenant", "tenant-masters"],
    ] as const) {
      await page.goto(path);
      await slow(page.getByRole("navigation", { name: "Admin mode" })).toBeVisible();
      await page.waitForTimeout(500);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      slow(overflow, `${path} at ${width}px`).toBeLessThanOrEqual(0);
      await shot(page, `10-${name}-${width}`);
    }
  });
}
