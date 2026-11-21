import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { fakeMasters, openTab, pageOverflow, signInAs } from "./admin-masters-fake";

// MUI-5 (#784): the Rules Engine on AG Grid and the BAM API, with the API faked at the proxy.
// Screenshots: set E2E_SHOTS to a folder to also write the light / dark / 390 px set.

test.use({ actionTimeout: 15_000 });
test.beforeEach(async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
});

const ruleRow = (
  page: import("@playwright/test").Page,
  stable: string,
  rows: { stable_id: string; id: string }[],
) => page.locator(`.ag-row[row-id$="${rows.find((r) => r.stable_id === stable)!.id}"]`);

test("mui5_t12 the workflow map lists the skills in Next order and filters the rules", async ({
  page,
}) => {
  const { rows } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Rules");
  const map = page.getByRole("group", { name: "Workflow map" });
  await expect(map.getByText(/1\. SK-01 Intake · 1 rule/)).toBeVisible();
  await expect(map.getByText(/2\. SK-02 Quote · 1 rule/)).toBeVisible();
  await map.getByText(/SK-02 Quote/).click();
  await expect(ruleRow(page, "RULE-1", rows)).toHaveCount(0);
  await expect(ruleRow(page, "RULE-2", rows)).toHaveCount(1);
});

test("mui5_t13 status and category filters narrow the rules", async ({ page }) => {
  const { rows } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Rules");
  await page.getByRole("combobox", { name: "Filter by Status" }).click();
  await page.locator(".ant-select-item-option", { hasText: "Approved" }).click();
  await page.keyboard.press("Escape");
  await expect(ruleRow(page, "RULE-1", rows)).toHaveCount(0);
  await expect(ruleRow(page, "RULE-2", rows)).toHaveCount(1);
});

test("mui5_t14 the editor saves system and micro prompts as a new version with If-Match", async ({
  page,
}) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Rules");
  await page.getByRole("button", { name: "Edit RULE-1" }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByRole("tab", { name: "Prompts" }).click();
  await drawer.getByLabel("System prompt").fill("Be very exact");
  await drawer.getByRole("button", { name: /Add micro prompt/ }).click();
  await drawer.getByLabel("Micro prompt 2 name").fill("m2");
  await drawer.getByLabel("Micro prompt 2 text").fill("verify");
  await drawer.getByRole("button", { name: "Save new version" }).click();
  await expect(page.getByText("Saved RULES #RULE-1 (version 2)")).toBeVisible();
  const post = log.find((l) => l.method === "POST")!;
  expect(post.path).toBe(
    `/admin/masters/RULES/${rows.find((r) => r.stable_id === "RULE-1")!.id}/versions`,
  );
  expect(post.ifMatch).toBe('"1"');
  const saved = rows.find((r) => r.stable_id === "RULE-1")!.payload;
  expect(saved.prompts).toEqual({
    system: "Be very exact",
    micro: [
      { name: "m1", text: "check" },
      { name: "m2", text: "verify" },
    ],
  });
  expect(saved.rule_id).toBe("RULE-1"); // the rest of the row is kept
});

test("mui5_t15 a new rule gets its id from the server, and retire removes a rule", async ({
  page,
}) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Rules");
  await page.getByRole("button", { name: "Add Row" }).first().click();
  const drawer = page.getByRole("dialog");
  await drawer.getByLabel("Rule / Type / Description").fill("New. Validation: x");
  await drawer.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText(/Created RULES/)).toBeVisible();
  expect(rows.filter((r) => r.master_type === "RULES")).toHaveLength(3);
  await page.getByRole("button", { name: "Retire RULE-2" }).click();
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByText("Retired RULE-2")).toBeVisible();
  expect(log.some((l) => l.path.endsWith("/retire"))).toBe(true);
});

test("mui5_t16 a list failure shows the problem detail", async ({ page }) => {
  await fakeMasters(page, { error: true });
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Rules", false);
  await expect(page.getByText(/The database is down/)).toBeVisible({ timeout: 20_000 });
});

test("mui5_t17 an FDE gets Forbidden and no masters request", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/masters/tenant");
  await expect(page.getByText("Forbidden")).toBeVisible();
  expect(apiCalls).toEqual([]);
});

for (const width of [320, 390]) {
  test(`mui5_t18 the Rules Engine does not overflow at ${width}px`, async ({ page }) => {
    await fakeMasters(page);
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/admin/masters/tenant");
    await openTab(page, "Rules");
    const { by, offenders } = await pageOverflow(page);
    expect(by, offenders.join(" | ")).toBeLessThanOrEqual(0);
    // the actions column is off screen on a phone: scroll the grid until it is rendered
    const edit = page.getByRole("button", { name: "Edit RULE-1" });
    await expect(async () => {
      await page
        .locator(".ag-body-horizontal-scroll-viewport")
        .evaluate((e) => (e.scrollLeft = 99999));
      await expect(edit).toBeVisible({ timeout: 1_000 });
    }).toPass({ timeout: 15_000 });
    await edit.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect((await pageOverflow(page)).by).toBeLessThanOrEqual(0);
  });
}

test("mui5 screenshots: the Rules Engine, light, dark and 390 px (only with E2E_SHOTS)", async ({
  page,
}) => {
  const dir = process.env.E2E_SHOTS;
  test.skip(!dir, "E2E_SHOTS names the folder");
  mkdirSync(dir!, { recursive: true });
  await fakeMasters(page);
  const shoot = async (name: string, theme: "light" | "dark", width: number) => {
    await page.addInitScript(
      ([k, v]) => window.localStorage.setItem(k!, v!),
      ["sbam-admin-theme", theme],
    );
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/masters/tenant");
    await openTab(page, "Rules");
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(dir!, `rules-engine-${name}.png`) });
    await page.getByRole("button", { name: "Edit RULE-1" }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(dir!, `rules-editor-rule-${name}.png`) });
    await page.getByRole("dialog").getByRole("tab", { name: "Prompts" }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(dir!, `rules-editor-prompts-${name}.png`) });
  };
  await shoot("light", "light", 1280);
  await shoot("dark", "dark", 1280);
  await shoot("390", "light", 390);
});
