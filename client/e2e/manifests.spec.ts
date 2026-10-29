import { expect, test, type Page, type Request, type Route } from "@playwright/test";

// Manifest viewer (E9.6). The manifest API is faked at the proxy; Monaco (its workers, the JSON
// Schema hover and the diff editor) is real.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const API = "**/api/proxy/api/v1";
const W = `${API}/projects/${PROJECT}/workflows/${WORKFLOW}`;
const HASH = `sha256:${"ab".repeat(32)}`;
const XSS = "<img src=x onerror=alert(1)>";
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const problem = (route: Route, status: number, code: string) =>
  route.fulfill({
    status,
    contentType: "application/problem+json",
    body: JSON.stringify({
      title: code,
      status,
      code,
      detail: code,
      errors: [],
      correlation_id: "c",
    }),
  });

const manifest = (build: number, name = "Investor Login") => ({
  skill_metadata: { skill_id: "MS01.1_Login", skill_name: name, version: "1.0.0" },
  isatvon_definition: { i_instructions: `Log in, revision ${build}.`, v_variables: { max: 3 } },
  srim_runtime_definition: { execution: { engine: "FastMCP_Execution_Engine" } },
  contracts: { input_schema: {}, output_schema: {} },
  governance: { deterministic_hash: HASH, versioning: { major: 1, minor: 0, patch: build - 1 } },
  sbam_build_record: { ids: { sub_skill_id: SUB } },
});
const version = (build: number) => ({
  version: `1.0.${build - 1}`,
  sha256: HASH,
  build_no: build,
  cycle: 1,
  attempt: 0,
  is_frozen: false,
  created_at: "2026-10-29T10:00:00Z",
});
const SCHEMA = {
  $id: "urn:sbam:contracts:manifest/skill_manifest:1.0.0",
  type: "object",
  properties: {
    governance: {
      type: "object",
      description: "Governance: the deterministic hash and versioning.",
    },
  },
};

type Fake = { name?: string; manifests?: "ok" | "none" | "404" };

async function open(page: Page, baseURL: string, f: Fake = {}) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route(`${API}/schemas/manifest/**`, (r) => json(r, SCHEMA));
  await page.route(`${W}/macro-skills`, (r) =>
    json(r, {
      workflow_id: WORKFLOW,
      workflow_state: "ISATVON_REVIEW",
      sow_approved: true,
      macros: [
        {
          id: "m1",
          macro_category: "MS01_Access",
          name: "Access",
          description: "",
          order: 1,
          sub_skills: [
            {
              id: SUB,
              macro_id: "m1",
              state: "VALID",
              content_sha256: "c".repeat(64),
              sub_skill: { skill_metadata: { skill_id: "MS01.1_Login", skill_name: "Login" } },
            },
          ],
        },
      ],
    }),
  );
  await page.route(`${W}/sub-skills/${SUB}/manifests`, (r) =>
    f.manifests === "404"
      ? problem(r, 404, "NOT_FOUND")
      : json(r, { items: f.manifests === "none" ? [] : [version(2), version(1)] }),
  );
  await page.route(`${W}/sub-skills/${SUB}/manifest?**`, (r) => {
    const build = Number(new URL(r.request().url()).searchParams.get("build_no"));
    return json(r, {
      ...version(build),
      schema_version: "manifest/skill_manifest@1.0",
      reused: false,
      manifest: manifest(build, f.name),
    });
  });
  await page.goto(`/projects/${PROJECT}/workflows/${WORKFLOW}/manifests?sub=${SUB}`);
  await expect(page.getByTestId("manifest-screen")).toBeVisible({ timeout: 30_000 });
}

const editor = (page: Page) =>
  page.getByRole("region", { name: "skill_manifest.json (read only)" });
const writes = (requests: Request[]) =>
  requests.filter((r) => r.url().includes("/api/proxy/") && r.method() !== "GET");

test("e9_6_t01 the manifest opens with its six blocks; hovering governance shows the schema", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!);
  const view = editor(page);
  await expect(view.locator(".view-lines")).toContainText('"governance"', { timeout: 30_000 });
  for (const block of [
    "contracts",
    "governance",
    "isatvon_definition",
    "sbam_build_record",
    "skill_metadata",
    "srim_runtime_definition",
  ])
    await expect(view.locator(".view-lines")).toContainText(`"${block}"`);
  await view
    .locator(".view-line", { hasText: '"governance"' })
    .locator("span", { hasText: "governance" })
    .first()
    .hover();
  await expect(page.locator(".monaco-hover").filter({ hasText: "Governance:" })).toContainText(
    "Governance: the deterministic hash and versioning.",
    { timeout: 15_000 },
  );
});

test("e9_6_t02 Copy hash puts the API's sha256 on the clipboard", async ({
  page,
  baseURL,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page, baseURL!);
  await page.getByTestId("manifest-header").getByRole("button", { name: "Copy hash" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(HASH);
});

test("e9_6_t03 comparing 1.0.0 with 1.0.1 marks only the changed isatvon_definition line", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!);
  await page.getByRole("combobox", { name: "Compare with" }).click();
  await page.locator(".ant-select-item-option", { hasText: "build 1" }).click();
  const diff = page.getByRole("region", { name: "Diff of skill_manifest.json" });
  await expect(diff.locator(".line-insert")).not.toHaveCount(0, { timeout: 30_000 });
  await expect(diff.locator(".editor.modified")).toContainText("Log in, revision 2.");
  // the versioning patch and the instruction are the only differences between the two builds
  const inserted = await diff.locator(".editor.modified .view-overlays .line-insert").count();
  expect(inserted).toBeLessThanOrEqual(2);
});

test("e9_6_t04 read only: typing changes nothing and nothing is written", async ({
  page,
  baseURL,
}) => {
  const requests: Request[] = [];
  page.on("request", (r) => requests.push(r));
  await open(page, baseURL!);
  const lines = editor(page).locator(".view-lines");
  await expect(lines).toContainText('"governance"', { timeout: 30_000 });
  const before = await lines.textContent();
  await lines.click();
  await page.keyboard.type("hello");
  await expect(lines).toHaveText(before ?? "");
  expect(writes(requests)).toEqual([]);
});

test("e9_6_t05 a skill_name with HTML is shown as text and never runs", async ({
  page,
  baseURL,
}) => {
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  await open(page, baseURL!, { name: XSS });
  await expect(editor(page).locator(".view-lines")).toContainText("onerror=alert(1)", {
    timeout: 30_000,
  });
  await expect(page.locator("img[src='x']")).toHaveCount(0);
  expect(dialogs).toBe(0);
});

test("e9_6_t06 not found (another tenant, unassigned): no manifest is shown", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, { manifests: "404" });
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await expect(editor(page)).toHaveCount(0);
});

test("e9_6_t07 no manifest yet: 'Not generated yet' with a Workbench link", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, { manifests: "none" });
  await expect(page.getByText("Not generated yet")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the Workbench" })).toHaveAttribute(
    "href",
    `/projects/${PROJECT}/workflows/${WORKFLOW}/isatvon`,
  );
});
