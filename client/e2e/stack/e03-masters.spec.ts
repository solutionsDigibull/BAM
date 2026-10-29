import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { guard, postFile, signedIn, USERS } from "./stack";

// E03 (E15.1-T03): the consolidated masters workbook imports, exports and re-imports unchanged (an
// identical round-trip); a workbook whose structure changed is refused with the difference.
const MASTERS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../server/tests/fixtures/masters",
);
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const workbook = (name: string, buffer: Buffer = readFileSync(path.join(MASTERS, name))) => ({
  name,
  mimeType: XLSX,
  buffer,
});

type Report = {
  applied: boolean;
  tabs: { name: string; created: number; updated: number; errors: unknown[] }[];
};

test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t03 masters round-trip is identical; a changed structure is rejected", async ({
  browser,
}) => {
  const admin = await signedIn(browser, "admin", USERS.adminA);
  await admin.goto("/admin/masters");
  await expect(admin.getByRole("button", { name: "Import workbook" })).toBeVisible();

  const upsert = await postFile(
    admin,
    "/admin/masters/import",
    workbook("BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"),
    { mode: "upsert" },
  );
  expect(upsert.status, JSON.stringify(upsert.body)).toBe(200);
  const applied = upsert.body as Report;
  expect(applied.tabs.flatMap((t) => t.errors)).toEqual([]);

  const origin = new URL(admin.url()).origin;
  const exported = await admin.request.get(`${origin}/api/proxy/api/v1/admin/masters/export`);
  expect(exported.status()).toBe(200);
  expect(exported.headers()["content-type"]).toContain(XLSX);

  const again = await postFile(
    admin,
    "/admin/masters/import",
    workbook("export.xlsx", await exported.body()),
    { mode: "validate-only" },
  );
  expect(again.status, JSON.stringify(again.body)).toBe(200);
  const report = again.body as Report;
  expect(report.applied).toBe(false);
  for (const t of report.tabs)
    expect([t.name, t.created, t.updated, t.errors]).toEqual([t.name, 0, 0, []]);

  const changed = await postFile(
    admin,
    "/admin/masters/import",
    workbook("structure_changed_rules.xlsx"),
    { mode: "validate-only" },
  );
  expect(changed.status).toBe(422);
  const problem = changed.body as { code: string; errors: { diff?: Record<string, string> }[] };
  expect(problem.code).toBe("STRUCTURE_MISMATCH");
  expect(problem.errors[0]?.diff).toMatchObject({ tab: "Rules", actual: "BOM", kind: "renamed" });
});
