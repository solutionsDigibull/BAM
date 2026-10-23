import { expect, test, type Page, type Request, type Route } from "@playwright/test";

// E12.6 Build & Test dashboard. GET W/builds and GET W/sub-skills/{id}/gates come with the gate issues
// (#117 / #119), so they're faked at the proxy with the shapes of sbam_contracts.build_dashboard (#312).
// Retry is #107's POST W/sub-skills/{id}/retry.

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const PAGE = `/projects/${PROJECT}/workflows/${WORKFLOW}/build`;

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const subId = (n: number) => `0190f3a2-7b1c-7d4e-8f00-${String(n).padStart(12, "0")}`;
const row = (n: number, over: Record<string, unknown> = {}) => ({
  sub_skill_id: subId(n),
  skill_id: `MS01.${n}_step_${n}`,
  skill_name: `Step ${n}`,
  state: "BUILDING",
  stage: "ast_gate",
  attempt: 0,
  gates: { ISATVON_CONTRACT: "PASSED", LIBRARIES: "PASSED", AST_SYNTAX: "RUNNING" },
  ...over,
});
const builds = (rows: unknown[], active = true) => ({
  workflow_id: WORKFLOW,
  workflow_state: "BUILDING",
  active,
  sub_skills: rows,
});

type Handler = (req: Request, path: string, route: Route) => Promise<void> | undefined;

async function open(page: Page, baseURL: string, body: unknown | (() => unknown), other?: Handler) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  let calls = 0;
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    if (path.endsWith("/builds")) {
      calls += 1;
      return json(route, typeof body === "function" ? (body as () => unknown)() : body);
    }
    const handled = other?.(req, path, route);
    if (handled) return handled;
    return json(route, { type: "about:blank", title: "Not found", status: 404 }, 404);
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(PAGE);
  return { errors, calls: () => calls };
}

const grid = (page: Page) => page.getByTestId("build-grid");
const cell = (page: Page, skill: string, gate: string, status: string) =>
  grid(page).getByRole("img", { name: `${skill} ${gate}: ${status}` });

test("e12_6_t01 a running gate turns PASSED without a reload, within 5 s", async ({
  page,
  baseURL,
}) => {
  let done = false;
  await open(page, baseURL!, () =>
    builds([row(1, done ? { gates: { AST_SYNTAX: "PASSED" } } : {})]),
  );
  await expect(cell(page, "MS01.1_step_1", "Syntax", "Running")).toBeVisible();
  await expect(page.getByText("Live: updates every 3 s")).toBeVisible();
  const flipped = Date.now();
  done = true;
  await expect(cell(page, "MS01.1_step_1", "Syntax", "Passed")).toBeVisible({ timeout: 5_000 });
  expect(Date.now() - flipped).toBeLessThan(5_000);
});

test("e12_6_t02 a failed gate opens its findings; the file:line link opens the Code Viewer", async ({
  page,
  baseURL,
}) => {
  let attempt: string | null = null;
  const failed = row(1, {
    state: "NEEDS_FDE_REVIEW",
    stage: "wait_retry",
    attempt: 2,
    gates: { AST_HANDOFF: "FAILED" },
  });
  await open(page, baseURL!, builds([failed], false), (req, path, route) => {
    if (!path.endsWith("/gates")) return undefined;
    attempt = new URL(req.url()).searchParams.get("attempt");
    return json(route, {
      sub_skill_id: subId(1),
      attempt: Number(attempt),
      gates: [
        {
          gate: "AST_HANDOFF",
          status: "FAILED",
          tool_version: "sbam-ast 1.0",
          findings: [
            {
              gate: "AST_HANDOFF",
              code: "AST_HANDOFF",
              file: "skill/handoff.py",
              line: 42,
              message: "handoff writes to a table not in the manifest",
            },
          ],
        },
      ],
    });
  });
  await grid(page)
    .getByRole("button", { name: "Findings for MS01.1_step_1 Handoff: Failed" })
    .click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("handoff writes to a table not in the manifest")).toBeVisible();
  await expect(drawer.getByText("AST_HANDOFF", { exact: true }).first()).toBeVisible();
  expect(attempt).toBe("2"); // the latest attempt first
  const link = drawer.getByRole("link", { name: "skill/handoff.py:42" });
  await expect(link).toHaveAttribute(
    "href",
    `/projects/${PROJECT}/workflows/${WORKFLOW}/code?sub=${subId(1)}&attempt=2&file=skill%2Fhandoff.py&line=42`,
  );
  await link.click();
  // `next dev` compiles the Code page on its first visit
  await expect(page).toHaveURL(/\/code\?sub=.*&file=skill%2Fhandoff\.py&line=42/, {
    timeout: 30_000,
  });
});

test("e12_6_t03 Retry with a reason: 202, then the row is BUILDING again", async ({
  page,
  baseURL,
}) => {
  let retried: { reason?: string; key?: string } | null = null;
  await open(
    page,
    baseURL!,
    () =>
      builds(
        [row(1, retried ? {} : { state: "NEEDS_FDE_REVIEW", stage: "wait_retry", attempt: 2 })],
        !!retried,
      ),
    (req, path, route) => {
      if (!path.endsWith("/retry")) return undefined;
      retried = { ...req.postDataJSON(), key: req.headers()["idempotency-key"] };
      return json(route, { sub_skill_id: subId(1), build_no: 1 }, 202);
    },
  );
  await expect(page.getByText("1 Sub-Skill · 1 waiting for review")).toBeVisible();
  await grid(page).getByRole("button", { name: "Retry MS01.1_step_1" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Retry" })).toBeDisabled(); // a reason is required
  await dialog.getByRole("textbox", { name: "Reason" }).fill("raised the V bound to 5");
  await dialog.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Retry started for MS01.1_step_1")).toBeVisible();
  expect(retried).toMatchObject({ reason: "raised the V bound to 5" });
  expect(retried!.key).toMatch(/^[0-9a-f-]{36}$/);
  await expect(grid(page).getByText("BUILDING", { exact: true })).toBeVisible();
  await expect(grid(page).getByRole("button", { name: "Retry MS01.1_step_1" })).toBeDisabled();
});

test("a refused retry (409) shows the server's reason", async ({ page, baseURL }) => {
  await open(
    page,
    baseURL!,
    builds([row(1, { state: "NEEDS_FDE_REVIEW", stage: "wait_retry" })], false),
    (_req, path, route) =>
      path.endsWith("/retry")
        ? json(
            route,
            {
              type: "about:blank",
              title: "Conflict",
              status: 409,
              detail: "Not in review any more",
            },
            409,
          )
        : undefined,
  );
  await grid(page).getByRole("button", { name: "Retry MS01.1_step_1" }).click();
  await page.getByRole("textbox", { name: "Reason" }).fill("x");
  await page.getByRole("dialog").getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Not in review any more")).toBeVisible();
});

test("e12_6_t05 markup in a finding is shown as text", async ({ page, baseURL }) => {
  const payload = "<script>alert(1)</script><img src=x onerror=alert(1)>";
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  await open(
    page,
    baseURL!,
    builds([row(1, { gates: { RUFF: "FAILED" } })], false),
    (_req, path, route) =>
      path.endsWith("/gates")
        ? json(route, {
            sub_skill_id: subId(1),
            attempt: 0,
            gates: [
              {
                gate: "RUFF",
                status: "FAILED",
                findings: [{ gate: "RUFF", code: "E999", message: payload }],
              },
            ],
          })
        : undefined,
  );
  await grid(page).getByRole("button", { name: "Findings for MS01.1_step_1 Ruff: Failed" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText(payload)).toBeVisible();
  await expect(drawer.locator("img, script")).toHaveCount(0);
  expect(dialogs).toBe(0);
});

test("e12_6_t06 50 Sub-Skills × 19 gates render within 1 s", async ({ page, baseURL }) => {
  const rows = Array.from({ length: 50 }, (_, i) =>
    row(i + 1, {
      gates: { ISATVON_CONTRACT: "PASSED", RUFF: i % 7 === 0 ? "FAILED" : "PASSED" },
    }),
  );
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL! }]);
  await page.route("**/api/proxy/api/v1/**", (route) => json(route, builds(rows, false)));
  await page.goto(PAGE);
  // `next dev` compiles and downloads the grid's chunk on a cold visit, which is no part of rendering
  await expect(grid(page).getByText("MS01.1_step_1", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText("50 Sub-Skills · 8 with a failed gate")).toBeVisible();
  // The grid's own marks: container mounted -> first rows drawn (all 24 columns, rows virtualised)
  const ms = await page.evaluate(
    () => performance.measure("build-grid", "build-grid:mounted", "build-grid:rendered").duration,
  );
  console.log(`50 x 19 matrix rendered in ${Math.round(ms)} ms`);
  expect(ms).toBeLessThan(1_000);
  await grid(page).hover();
  const last = grid(page).getByText("MS01.50_step_50", { exact: true });
  for (let i = 0; i < 30 && !(await last.isVisible()); i++) await page.mouse.wheel(0, 1_500);
  await expect(last).toBeVisible();
});

test("e12_6_t07 nothing active: no more polling after the first answer", async ({
  page,
  baseURL,
}) => {
  const api = await open(
    page,
    baseURL!,
    builds([row(1, { state: "CERTIFIED", stage: "done" })], false),
  );
  await expect(page.getByText("Not building")).toBeVisible();
  // `next dev` mounts twice (React strict mode), so the first answer may be asked for twice
  const settled = api.calls();
  await page.waitForTimeout(4_000); // longer than one poll interval
  expect(api.calls()).toBe(settled);
});

test("a dropped connection keeps the last update and says so", async ({ page, baseURL }) => {
  let online = true;
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL! }]);
  await page.route("**/api/proxy/api/v1/**", (route) =>
    online ? json(route, builds([row(1)])) : route.abort("internetdisconnected"),
  );
  await page.goto(PAGE);
  await expect(cell(page, "MS01.1_step_1", "Syntax", "Running")).toBeVisible();
  online = false; // the next poll fails
  await expect(page.getByText(/Connection lost/)).toBeVisible({ timeout: 8_000 });
  await expect(cell(page, "MS01.1_step_1", "Syntax", "Running")).toBeVisible();
});

test("empty and not-found states", async ({ page, baseURL }) => {
  const api = await open(page, baseURL!, builds([], false));
  await expect(page.getByText("Nothing is building yet.")).toBeVisible();
  expect(api.errors).toEqual([]);
});
