import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #712: the recycle bin on the workbench's Upload screen (restore a removed file). The API is faked at the proxy;
// each state is saved as a screenshot (BIN_SHOTS or test-results/recycle-bin-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const SHOTS = process.env.BIN_SHOTS ?? path.join("test-results", "recycle-bin-shots");

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string, code?: string) =>
  json(route, { type: "about:blank", title: detail, status, detail, code }, status);

const doc = (over: Record<string, unknown>) => ({
  id: "d-live",
  title: "supplier-quote.pdf",
  state: "PARSED",
  size: 52_000,
  mime: "application/pdf",
  sha256: "x",
  created_at: "2026-11-09T10:00:00Z",
  ...over,
});
const removed = doc({
  id: "d-bin",
  title: "old-rfq.pdf",
  state: "DELETED",
  deleted_at: "2026-11-15T10:00:00Z",
  purge_after: "2027-02-13T10:00:00Z",
});

async function open(
  page: Page,
  baseURL: string,
  documents: (r: Route) => Promise<void>,
  restore?: (r: Route) => Promise<void>,
) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/documents`) return documents(route);
    if (p === `/projects/${PROJECT}/documents/d-bin/restore` && restore) return restore(route);
    if (p === `/projects/${PROJECT}/pipeline`)
      return json(route, {
        state: "IDLE",
        steps: [],
        counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0 },
      });
    if (p === `/projects/${PROJECT}/knowledge`) return json(route, { versions: [] });
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench`);
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `712-recycle-bin-${name}.png`), fullPage: true });

test("e712_t01 loaded: a removed file is listed with its keep-until date and Restore", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, { items: [doc({}), removed], next_cursor: null }));
  const bin = page.getByLabel("Recycle bin");
  await expect(bin.getByText("old-rfq.pdf")).toBeVisible();
  await expect(bin).toContainText("kept until");
  await expect(bin.getByRole("button", { name: /Restore/ })).toBeVisible();
  await shot(page, "loaded");
});

test("e712_t02 empty: the bin says nothing is removed", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { items: [doc({})], next_cursor: null }));
  await expect(page.getByLabel("Recycle bin")).toContainText("No removed files");
  await shot(page, "empty");
});

test("e712_t03 restore: the file comes back through the API", async ({ page, baseURL }) => {
  let restored = false;
  await open(
    page,
    baseURL!,
    (r) =>
      json(r, {
        items: restored
          ? [doc({}), { ...removed, state: "UPLOADED", deleted_at: null, purge_after: null }]
          : [doc({}), removed],
        next_cursor: null,
      }),
    (r) => {
      restored = true;
      return json(r, { ...removed, state: "UPLOADED", deleted_at: null, purge_after: null });
    },
  );
  await page
    .getByLabel("Recycle bin")
    .getByRole("button", { name: /Restore/ })
    .click();
  await expect(page.getByLabel("Recycle bin")).toContainText("No removed files");
  await shot(page, "restored");
});

test("e712_t04 error: a refused restore shows the server's reason in place", async ({
  page,
  baseURL,
}) => {
  await open(
    page,
    baseURL!,
    (r) => json(r, { items: [doc({}), removed], next_cursor: null }),
    (r) => problem(r, 410, "Purged"),
  );
  await page
    .getByLabel("Recycle bin")
    .getByRole("button", { name: /Restore/ })
    .click();
  await expect(page.getByLabel("Recycle bin").getByRole("alert")).toContainText(
    "permanently deleted",
  );
  await shot(page, "error");
});

test("e712_t05 forbidden: a project the user is not assigned to shows no bin", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Not assigned to this project"));
  await expect(page.getByLabel("Recycle bin")).toHaveCount(0);
  await shot(page, "forbidden");
});
