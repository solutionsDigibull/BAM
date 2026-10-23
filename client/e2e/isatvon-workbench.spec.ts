import { expect, test, type Page, type Request, type Route } from "@playwright/test";

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

type Patch = { ifMatch: string | null; changes: Record<string, unknown> };

async function open(
  page: Page,
  baseURL: string,
  body: unknown,
  patch?: (p: Patch, route: Route) => Promise<void>,
  // Any other call (validate, export, generate, regenerate, delete): answer it, or return undefined.
  other?: (req: Request, path: string, route: Route) => Promise<void> | undefined,
) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/schemas/isatvon/1.4") return json(route, SCHEMA);
    if (path.endsWith("/macro-skills"))
      return json(route, typeof body === "function" ? (body as () => unknown)() : body);
    if (req.method() === "PATCH" && path.includes("/sub-skills/") && patch)
      return patch({ ifMatch: req.headers()["if-match"] ?? null, ...req.postDataJSON() }, route);
    const handled = other?.(req, path, route);
    if (handled) return handled;
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
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/schemas/isatvon/1.4") return json(route, SCHEMA);
    return json(route, tree(macros));
  });
  await page.goto(PAGE);
  await expect(page.getByText("40 Macro Skills · 500 Sub-Skills")).toBeVisible();
  // `next dev` compiles and downloads the grid's chunk on a cold visit: no part of rendering
  await expect(grid(page).getByText("Step 1", { exact: true }).first()).toBeVisible({
    timeout: 30_000,
  });
  // the grid's own marks: container mounted -> first rows drawn, timed in the page
  const rendered = await page.evaluate(
    () =>
      performance.measure("isatvon-grid", "isatvon-grid:mounted", "isatvon-grid:rendered").duration,
  );
  console.log(`500 Sub-Skills rendered in ${Math.round(rendered)} ms`);
  expect(rendered).toBeLessThan(2_000);
  const rows = await grid(page).locator('.ag-center-cols-container [role="row"]').count();
  expect(rows).toBeLessThan(200); // virtualised: only the visible window is in the DOM
  await grid(page).hover();
  const last = grid(page).getByRole("button", { name: "Collapse MS40 Macro 40" });
  for (let i = 0; i < 60 && !(await last.isVisible()); i++) await page.mouse.wheel(0, 2_000); // as a user scrolls
  await expect(last).toBeVisible();
});

// ---- edits (part 3): PATCH with If-Match, the row replaced by the server's answer ----

const MAX = "isatvon_definition.v_variables.max_iterations";
const maxCell = (page: Page) => grid(page).locator(`[role="gridcell"][col-id="${MAX}"]`).first();

async function typeInto(page: Page, value: string) {
  await maxCell(page).dblclick();
  const input = grid(page).locator(".ag-cell-inline-editing input");
  await input.fill(value);
  await input.press("Enter");
}

const withMax = (n: number, sha: string, errors: unknown[] = []) => {
  const s = sub(1, 1, {
    content_sha256: sha,
    state: errors.length ? "INVALID" : "VALID",
    validation_errors: errors,
  });
  (s.sub_skill.isatvon_definition.v_variables as Record<string, unknown>).max_iterations = n;
  return s;
};
const RULE5 = { rule: 5, path: MAX, message: "max_iterations must be 1-10" };

test("e8_7_t04 / e8_7_t05 edit -> PATCH with If-Match -> invalid, then fixed -> valid", async ({
  page,
  baseURL,
}) => {
  const sent: Patch[] = [];
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), async (p, route) => {
    sent.push(p);
    const n = p.changes[MAX] as number;
    const sha = String(sent.length + 1).repeat(64);
    await json(route, withMax(n, sha, n > 10 ? [RULE5] : []));
  });
  await typeInto(page, "11");
  await expect(page.getByText("1 Macro Skill · 1 Sub-Skill · 1 invalid")).toBeVisible();
  await expect(maxCell(page)).toHaveText("11");
  await expect(maxCell(page)).toHaveCSS("background-color", "rgb(255, 241, 240)");
  await expect(grid(page).getByText("INVALID", { exact: true })).toBeVisible();
  expect(sent[0]).toEqual({ ifMatch: `"${"a".repeat(64)}"`, changes: { [MAX]: 11 } });

  await typeInto(page, "3");
  await expect(page.getByText("1 Macro Skill · 1 Sub-Skill", { exact: true })).toBeVisible();
  await expect(maxCell(page)).toHaveText("3");
  await expect(maxCell(page)).not.toHaveCSS("background-color", "rgb(255, 241, 240)");
  expect(sent[1]!.ifMatch).toBe(`"${"2".repeat(64)}"`); // the ETag the first answer carried
});

test("a client-side type error never reaches the API", async ({ page, baseURL }) => {
  let calls = 0;
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), async (_p, route) => {
    calls += 1;
    await json(route, {});
  });
  await typeInto(page, "2.5");
  await expect(maxCell(page)).toHaveCSS("background-color", "rgb(255, 241, 240)");
  await maxCell(page).hover();
  await expect(page.getByText("Max Iterations must be a whole number")).toBeVisible();
  expect(calls).toBe(0);
});

test("a 422 keeps the old value and marks the cell with the server's message", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), (_p, route) =>
    json(
      route,
      {
        type: "about:blank",
        title: "Unprocessable",
        status: 422,
        errors: [{ path: "changes", message: "Rejected by the ISATVON schema" }],
      },
      422,
    ),
  );
  await typeInto(page, "5");
  await expect(maxCell(page)).toHaveCSS("background-color", "rgb(255, 241, 240)");
  await expect(maxCell(page)).toHaveText("3");
  await maxCell(page).hover();
  await expect(page.getByText("Rejected by the ISATVON schema")).toBeVisible();
});

test("e8_7_t08 a 412 reloads the row and says 'Updated elsewhere'", async ({ page, baseURL }) => {
  let reloaded = false;
  await open(
    page,
    baseURL!,
    () => tree([macro(1, [reloaded ? withMax(7, "c".repeat(64)) : sub(1, 1)])]),
    async (_p, route) => {
      reloaded = true;
      await json(route, { type: "about:blank", title: "Stale", status: 412 }, 412);
    },
  );
  await typeInto(page, "5");
  await expect(page.getByText(/Updated elsewhere/)).toBeVisible();
  await expect(maxCell(page)).toHaveText("7");
});

test("a 423 makes the grid read-only", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), (_p, route) =>
    json(route, { type: "about:blank", title: "Locked", status: 423 }, 423),
  );
  await typeInto(page, "5");
  await expect(page.getByText(/This item is frozen \/ approved/)).toBeVisible();
  await maxCell(page).dblclick();
  await expect(grid(page).locator(".ag-cell-inline-editing")).toHaveCount(0);
});

test("outside review cells don't open an editor", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])], "BUILDING"));
  await maxCell(page).dblclick();
  await expect(grid(page).locator(".ag-cell-inline-editing")).toHaveCount(0);
});

test("e8_7_t03 an edit with 500 rows loaded answers within 500 ms", async ({ page, baseURL }) => {
  const macros = Array.from({ length: 40 }, (_, m) =>
    macro(
      m + 1,
      Array.from({ length: m < 20 ? 13 : 12 }, (_, n) => sub(m + 1, n + 1)),
    ),
  );
  await open(page, baseURL!, tree(macros), (p, route) =>
    json(route, withMax(p.changes[MAX] as number, "d".repeat(64))),
  );
  await expect(page.getByText("40 Macro Skills · 500 Sub-Skills")).toBeVisible();
  // Timed in the page from the Enter keydown to the replaced cell (each animation frame), so neither
  // Playwright's polling nor its round trips are counted. Median of 5: one sample on a busy CI box is noise.
  const sel = `[data-testid="isatvon-grid"] [role="gridcell"][col-id="${MAX}"]`;
  const samples: number[] = [];
  for (const v of ["4", "5", "6", "7", "8"]) {
    await maxCell(page).dblclick();
    const input = grid(page).locator(".ag-cell-inline-editing input");
    await input.fill(v);
    const waited = page.evaluate(
      ([s, want]) =>
        new Promise<number>((done) => {
          document.addEventListener(
            "keydown",
            () => {
              const t0 = performance.now();
              const tick = () =>
                document.querySelector(s)?.textContent === want
                  ? done(performance.now() - t0)
                  : requestAnimationFrame(tick);
              requestAnimationFrame(tick);
            },
            { capture: true, once: true },
          );
        }),
      [sel, v] as const,
    );
    await input.press("Enter");
    samples.push(await waited);
  }
  samples.sort((a, b) => a - b);
  console.log(`edit -> row replaced (ms): ${samples.map(Math.round).join(", ")}`);
  expect(samples[2]).toBeLessThan(500);
});

// ---- toolbar and row actions (part 4): Validate, Export, Generate, Regenerate, Delete ----

const generateButton = (page: Page) => page.getByRole("button", { name: "Generate", exact: true });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: "Conflict", status, detail }, status);

test("e8_7_t05 Generate turns on once the last invalid cell is fixed, and starts with an Idempotency-Key", async ({
  page,
  baseURL,
}) => {
  let key: string | undefined;
  await open(
    page,
    baseURL!,
    tree([macro(1, [withMax(11, "a".repeat(64), [RULE5]), sub(1, 2)])]),
    (p, route) => json(route, withMax(p.changes[MAX] as number, "b".repeat(64))),
    (req, path, route) => {
      if (req.method() !== "POST" || !path.endsWith("/generate")) return undefined;
      key = req.headers()["idempotency-key"];
      return json(route, { runs: [] }, 202);
    },
  );
  await expect(page.getByText("Generate is off: 1 Sub-Skill isn't VALID.")).toBeVisible();
  await expect(generateButton(page)).toBeDisabled();
  await typeInto(page, "3");
  await expect(generateButton(page)).toBeEnabled();
  await generateButton(page).click();
  await expect(page.getByText("Generation started")).toBeVisible();
  expect(key).toMatch(/^[0-9a-f-]{36}$/);
});

test("Generate refused by the server (409) shows its reason", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), undefined, (req, path, route) =>
    path.endsWith("/generate")
      ? problem(route, 409, "Sub-Skills changed: validate again")
      : undefined,
  );
  await generateButton(page).click();
  await expect(page.getByText("Sub-Skills changed: validate again")).toBeVisible();
});

test("Validate merges the report: the row turns INVALID with the rule's message", async ({
  page,
  baseURL,
}) => {
  const s = sub(1, 1);
  await open(page, baseURL!, tree([macro(1, [s])]), undefined, (req, path, route) =>
    path.endsWith("/isatvon/validate")
      ? json(route, { sub_skills: [{ id: s.id, state: "INVALID", validation_errors: [RULE5] }] })
      : undefined,
  );
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Validated: 1 invalid")).toBeVisible();
  await expect(page.getByText("1 Macro Skill · 1 Sub-Skill · 1 invalid")).toBeVisible();
  await expect(maxCell(page)).toHaveCSS("background-color", "rgb(255, 241, 240)");
  await expect(generateButton(page)).toBeDisabled();
});

test("e8_7_t09 Export Excel downloads the server's workbook", async ({ page, baseURL }) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), undefined, (req, path, route) =>
    path.endsWith("/isatvon/export.xlsx")
      ? route.fulfill({
          status: 200,
          contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          headers: { "content-disposition": 'attachment; filename="workflow-isatvon.xlsx"' },
          body: Buffer.from("PK fake xlsx"),
        })
      : undefined,
  );
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export Excel" }).click(),
  ]);
  expect(file.suggestedFilename()).toBe("workflow-isatvon.xlsx");
});

test("Delete asks first, sends If-Match, and the row is gone after the reload", async ({
  page,
  baseURL,
}) => {
  let deleted: string | null = null;
  let ifMatch: string | undefined;
  await open(
    page,
    baseURL!,
    () => tree([macro(1, deleted ? [sub(1, 1)] : [sub(1, 1), sub(1, 2)])]),
    undefined,
    (req, path, route) => {
      if (req.method() !== "DELETE") return undefined;
      deleted = path;
      ifMatch = req.headers()["if-match"];
      return route.fulfill({ status: 204 });
    },
  );
  await expect(grid(page).getByText("Step 2", { exact: true })).toBeVisible();
  await grid(page).getByRole("button", { name: "Delete MS01.2_step_2" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(grid(page).getByText("Step 2", { exact: true })).toHaveCount(0);
  expect(deleted).toMatch(/\/sub-skills\/0190f3a2-7b1c-7d4e-8f00-000000001002$/);
  expect(ifMatch).toBe(`"${"a".repeat(64)}"`);
});

test("Regenerate a Sub-Skill sends the hint; a busy Macro answers 409", async ({
  page,
  baseURL,
}) => {
  const sent: { path: string; body: unknown; key?: string }[] = [];
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])]), undefined, (req, path, route) => {
    if (!path.endsWith("/regenerate")) return undefined;
    sent.push({ path, body: req.postDataJSON(), key: req.headers()["idempotency-key"] });
    return path.includes("/macro-skills/")
      ? problem(route, 409, "The Workflow is building")
      : json(route, {}, 202);
  });
  await grid(page).getByRole("button", { name: "Regenerate MS01.1_step_1" }).click();
  await page.getByRole("textbox", { name: "Hint" }).fill("split the approval step out");
  await page.getByRole("dialog").getByRole("button", { name: "Regenerate" }).click();
  await expect(page.getByText("Regenerating MS01.1_step_1…")).toBeVisible();
  expect(sent[0]).toMatchObject({ body: { hint: "split the approval step out" } });
  expect(sent[0]!.path).toContain("/sub-skills/");
  expect(sent[0]!.key).toBeTruthy();

  await grid(page).getByRole("button", { name: "Regenerate MS01 Macro 1" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Regenerate" }).click();
  await expect(page.getByText("The Workflow is building")).toBeVisible();
  expect(sent[1]).toMatchObject({ body: {} });
});

test("outside review there are no row actions or Validate, and Generate is off", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, tree([macro(1, [sub(1, 1)])], "BUILDING"));
  await expect(grid(page).getByText("Step 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^(Regenerate|Delete) / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Validate" })).toHaveCount(0);
  await expect(generateButton(page)).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export Excel" })).toBeEnabled();
});
