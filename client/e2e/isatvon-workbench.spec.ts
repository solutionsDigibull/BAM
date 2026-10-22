import { expect, test, type Page, type Route } from "@playwright/test";

// E8.7 ISATVON workbench. The breakdown endpoints are #94's and not built yet, so the API is faked at the
// proxy; the grid metadata follows GET /schemas/isatvon/1.4 (#98), trimmed to the columns used here.

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const PAGE = `/projects/${PROJECT}/workflows/${WORKFLOW}/isatvon`;

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const col = (field_path: string, header: string, editor = "text", platform = false) => ({
  field_path,
  header,
  type: "string",
  editor,
  required: true,
  editable: !platform && field_path !== "skill_metadata.skill_id",
  platform,
  width: 180,
});

const SCHEMA = {
  version: "1.4",
  json_schema: {},
  grid: {
    column_groups: [
      {
        id: "metadata",
        header: "Metadata",
        description: "Skill identity",
        children: [
          col("skill_metadata.skill_id", "Skill ID", "readonly"),
          col("skill_metadata.skill_name", "Skill Name"),
        ],
      },
      {
        id: "i",
        header: "I · Instructions",
        description: "What the skill does",
        children: [col("isatvon_definition.i_instructions.objective", "Objective", "largeText")],
      },
      {
        id: "v",
        header: "V · Variables",
        description: "Loop and gates",
        children: [
          {
            ...col("isatvon_definition.v_variables.max_iterations", "Max Iterations", "number"),
            type: "integer",
            min: 1,
            max: 10,
            width: 110,
          },
        ],
      },
      {
        id: "status",
        header: "Status",
        description: "Platform fields",
        children: [
          col("state", "State", "readonly", true),
          col("validation_errors", "Validation Errors", "readonly", true),
        ],
      },
    ],
  },
};

const sub = (macro: number, n: number, over: Record<string, unknown> = {}) => ({
  id: `0190f3a2-7b1c-7d4e-8f00-${String(macro * 1000 + n).padStart(12, "0")}`,
  macro_id: `0190f3a2-7b1c-7d4e-8f00-${String(macro).padStart(12, "9")}`,
  state: "VALID",
  content_sha256: "a".repeat(64),
  validation_errors: [],
  sub_skill: {
    skill_metadata: {
      macro_category: `MS${String(macro).padStart(2, "0")}`,
      skill_id: `MS${String(macro).padStart(2, "0")}.${n}_step_${n}`,
      skill_name: `Step ${n}`,
      rule_refs: [],
    },
    isatvon_definition: {
      i_instructions: { objective: `Do step ${n}` },
      v_variables: { max_iterations: 3 },
    },
  },
  ...over,
});

const macro = (m: number, subs: unknown[]) => ({
  id: `0190f3a2-7b1c-7d4e-8f00-${String(m).padStart(12, "9")}`,
  macro_category: `MS${String(m).padStart(2, "0")}`,
  name: `Macro ${m}`,
  description: "",
  order: m,
  sub_skills: subs,
});

const tree = (macros: unknown[], workflow_state = "ISATVON_REVIEW") => ({
  workflow_id: WORKFLOW,
  workflow_state,
  sow_approved: true,
  macros,
});

async function open(page: Page, baseURL: string, body: unknown) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/schemas/isatvon/1.4") return json(route, SCHEMA);
    if (path.endsWith("/macro-skills")) return json(route, body);
    return json(route, { type: "about:blank", title: "Not found", status: 404 }, 404);
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(PAGE);
  return errors;
}

const grid = (page: Page) => page.getByTestId("isatvon-grid");

test("e8_7_t01 empty data shows 'No Macro Skills yet' without crashing", async ({
  page,
  baseURL,
}) => {
  const errors = await open(page, baseURL!, tree([]));
  await expect(page.getByText("No Macro Skills yet")).toBeVisible();
  expect(errors).toEqual([]);
});

test("e8_7_t02 malformed data: null tree, a row without isatvon -> flagged rows, no errors", async ({
  page,
  baseURL,
}) => {
  const broken = { ...sub(1, 2), sub_skill: { skill_metadata: { skill_id: "MS01.2_x" } } };
  const errors = await open(
    page,
    baseURL!,
    tree([macro(1, [sub(1, 1), broken, null]), { name: "no id" }]),
  );
  await expect(grid(page).getByText("Step 1", { exact: true })).toBeVisible();
  await expect(grid(page).getByText("Invalid data")).toHaveCount(3);
  await expect(grid(page).getByText(/no isatvon_definition/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("e8_7_t02 a null body is the empty state, not a crash", async ({ page, baseURL }) => {
  const errors = await open(page, baseURL!, null);
  await expect(page.getByText("No Macro Skills yet")).toBeVisible();
  expect(errors).toEqual([]);
});

test("e8_7_t06 columns come from metadata only: a row key 'hacked_col' makes no column", async ({
  page,
  baseURL,
}) => {
  const hacked = sub(1, 1, { hacked_col: "pwned" });
  (hacked.sub_skill as Record<string, unknown>).hacked_col = "pwned";
  await open(page, baseURL!, tree([macro(1, [hacked])]));
  await expect(grid(page).getByText("Step 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /hacked/i })).toHaveCount(0);
  await expect(page.getByText("pwned")).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Max Iterations" })).toBeVisible();
});

test("e8_7_t07 XSS in a cell is rendered literally", async ({ page, baseURL }) => {
  const payload = "<img src=x onerror=alert(1)>";
  const evil = sub(1, 1);
  (evil.sub_skill.skill_metadata as Record<string, unknown>).skill_name = payload;
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  await open(page, baseURL!, tree([macro(1, [evil])]));
  await expect(grid(page).getByText(payload)).toBeVisible();
  await expect(grid(page).locator("img")).toHaveCount(0);
  expect(dialogs).toBe(0);
});

test("invalid cells are marked with the server's message; read-only outside review", async ({
  page,
  baseURL,
}) => {
  const bad = sub(1, 1, {
    state: "INVALID",
    validation_errors: [
      {
        rule: 5,
        path: "isatvon_definition.v_variables.max_iterations",
        message: "max_iterations must be 1-10",
      },
    ],
  });
  (bad.sub_skill.isatvon_definition.v_variables as Record<string, unknown>).max_iterations = 11;
  await open(page, baseURL!, tree([macro(1, [bad])], "BUILDING"));
  await expect(page.getByText("Read-only: the Workflow is BUILDING.")).toBeVisible();
  await expect(page.getByText("1 Macro Skill · 1 Sub-Skill · 1 invalid")).toBeVisible();
  const cell = grid(page)
    .locator('[col-id="isatvon_definition.v_variables.max_iterations"]')
    .filter({ hasText: "11" });
  await expect(cell).toHaveCSS("background-color", "rgb(255, 241, 240)");
  await cell.hover();
  await expect(page.getByText("max_iterations must be 1-10")).toBeVisible();
});

test("macro rows collapse and expand their Sub-Skills", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1), sub(1, 2)])]));
  await expect(grid(page).getByText("Step 2", { exact: true })).toBeVisible();
  await grid(page).getByRole("button", { name: "Collapse MS01 Macro 1" }).click();
  await expect(grid(page).getByText("Step 2", { exact: true })).toHaveCount(0);
  await grid(page).getByRole("button", { name: "Expand MS01 Macro 1" }).click();
  await expect(grid(page).getByText("Step 2", { exact: true })).toBeVisible();
});

test("an extraction in progress shows its progress state", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([], "ISATVON_EXTRACTING"));
  await expect(page.getByText(/Extracting the ISATVON 1.4 breakdown/)).toBeVisible();
});

test("e8_7_t03 500 Sub-Skills across 40 macros render within 2 s and scroll", async ({
  page,
  baseURL,
}) => {
  const macros = Array.from({ length: 40 }, (_, m) =>
    macro(
      m + 1,
      Array.from({ length: m < 20 ? 13 : 12 }, (_, n) => sub(m + 1, n + 1)),
    ),
  );
  expect(macros.reduce((n, m) => n + m.sub_skills.length, 0)).toBe(500);
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL! }]);
  let served = 0;
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/schemas/isatvon/1.4") return json(route, SCHEMA);
    served = Date.now();
    return json(route, tree(macros));
  });
  await page.goto(PAGE);
  await expect(page.getByText("40 Macro Skills · 500 Sub-Skills")).toBeVisible();
  await expect(grid(page).getByText("Step 1", { exact: true }).first()).toBeVisible();
  const rendered = Date.now() - served;
  expect(rendered).toBeLessThan(2_000);
  const rows = await grid(page).locator('.ag-center-cols-container [role="row"]').count();
  expect(rows).toBeLessThan(200); // virtualised: only the visible window is in the DOM
  await grid(page).hover();
  const last = grid(page).getByRole("button", { name: "Collapse MS40 Macro 40" });
  for (let i = 0; i < 60 && !(await last.isVisible()); i++) await page.mouse.wheel(0, 2_000); // as a user scrolls
  await expect(last).toBeVisible();
});
