import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #895: the Open action of a document row on the workbench's Upload screen. The API is faked at the proxy; the signed
// link is faked at this origin's /api/v1/document-access route. Each state is saved as a screenshot (OPEN_SHOTS or
// test-results/open-file-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const SHOTS = process.env.OPEN_SHOTS ?? path.join("test-results", "open-file-shots");

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
const link = (expiresInMs = 300_000) => ({
  url: `/api/v1/document-access/d-live?t=t&p=p&exp=1&sig=${"a".repeat(64)}`,
  expires_at: new Date(Date.now() + expiresInMs).toISOString(),
  filename: "supplier-quote.pdf",
  mime: "application/pdf",
});

interface Seen {
  access: number;
  followed: string[];
}

async function open(
  page: Page,
  baseURL: string,
  items: unknown[],
  access: (r: Route, n: number) => Promise<void> | void,
): Promise<Seen> {
  const seen: Seen = { access: 0, followed: [] };
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  // The signed link is followed against this origin; the file itself is not under test here.
  await page.context().route("**/api/v1/document-access/**", (route) => {
    seen.followed.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      status: 200,
      headers: { "content-disposition": 'attachment; filename="supplier-quote.pdf"' },
      contentType: "application/octet-stream",
      body: "%PDF-1.7",
    });
  });
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/documents`) return json(route, { items, next_cursor: null });
    if (p === `/projects/${PROJECT}/documents/d-live/access`) return access(route, ++seen.access);
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
  return seen;
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `895-open-file-${name}.png`), fullPage: true });
const openButton = (page: Page) => page.getByRole("button", { name: "Open supplier-quote.pdf" });

test("e895_t01 loaded: a released file has an Open action", async ({ page, baseURL }) => {
  await open(page, baseURL!, [doc({})], (r) => json(r, link()));
  await expect(openButton(page)).toBeEnabled();
  await shot(page, "loaded");
});

test("e895_t02 open: the signed link is followed in a new tab", async ({ page, baseURL }) => {
  const seen = await open(page, baseURL!, [doc({})], (r) => json(r, link()));
  const popup = page.waitForEvent("popup");
  await openButton(page).click();
  await popup;
  await expect.poll(() => seen.followed).toEqual(["/api/v1/document-access/d-live"]);
  expect(seen.access).toBe(1);
  await shot(page, "opened");
});

test("e895_t03 loading: the button says it is opening and cannot be pressed twice", async ({
  page,
  baseURL,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const seen = await open(page, baseURL!, [doc({})], async (r) => {
    await gate;
    return json(r, link());
  });
  await openButton(page).click();
  await expect(openButton(page)).toBeDisabled();
  await expect(openButton(page)).toContainText("Opening");
  await shot(page, "loading");
  release();
  await expect.poll(() => seen.followed.length).toBe(1);
  expect(seen.access).toBe(1);
});

test("e895_t04 error: a refusal shows the server's reason in place", async ({ page, baseURL }) => {
  await open(page, baseURL!, [doc({})], (r) =>
    problem(
      r,
      409,
      "This file is in the recycle bin. Restore it to open it.",
      "DOCUMENT_NOT_RELEASED",
    ),
  );
  await openButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "recycle bin" })).toBeVisible();
  await shot(page, "error");
});

test("e895_t05 forbidden: a project the user may not read says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, [doc({})], (r) => problem(r, 403, "Not assigned to this project"));
  await openButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "not allowed" })).toBeVisible();
  await shot(page, "forbidden");
});

test("e895_t06 expired: a link that already ran out is asked for once more", async ({
  page,
  baseURL,
}) => {
  const seen = await open(page, baseURL!, [doc({})], (r, n) =>
    json(r, n === 1 ? link(-60_000) : link()),
  );
  await openButton(page).click();
  await expect.poll(() => seen.followed.length).toBe(1);
  expect(seen.access).toBe(2);
  await shot(page, "expired-refreshed");
});

test("e895_t07 quarantined: a blocked file cannot be opened and says why", async ({
  page,
  baseURL,
}) => {
  const seen = await open(
    page,
    baseURL!,
    [
      doc({}),
      doc({ id: "d-bad", title: "invoice.pdf", state: "REJECTED", reject_code: "MALWARE" }),
    ],
    (r) => json(r, link()),
  );
  await expect(openButton(page)).toBeEnabled();
  const blocked = page.getByRole("button", { name: "Open invoice.pdf" });
  await expect(blocked).toBeDisabled();
  await expect(blocked).toHaveAttribute("title", /quarantined/);
  expect(seen.access).toBe(0);
  await shot(page, "quarantined");
});

test("e895_t08 deleted: the file is gone from the list and the server refuses a stale click", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, [doc({})], (r) => problem(r, 404, "Not found", "NOT_FOUND"));
  await openButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "no longer exists" })).toBeVisible();
  await shot(page, "deleted");
});
