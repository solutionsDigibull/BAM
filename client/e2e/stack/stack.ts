import { randomBytes } from "node:crypto";

import { expect, type Browser, type Page } from "@playwright/test";

// E15.1: helpers for the real-stack scenarios (TD §19.2, E01-E20). Real Keycloak logins, real API,
// real workers; nothing is faked. Runs in the "stack" project only (E2E_BASE_URL + E2E_STACK=1).
//
//   E2E_SEED_PASSWORD   the test users' password (compose/.env SEED_USER_PASSWORD; a CI secret)
//   E2E_STAGING_HOST    the only non-local host the suite may target; anything else is refused,
//                       so it can never run against production
//   E2E_RUN_ID          names this run's projects (default: a random id); each test archives its own
//   E2E_USER_*          the test accounts, defaulting to compose/keycloak/seed.sh's

export const PASSWORD = process.env.E2E_SEED_PASSWORD ?? "";
export const RUN =
  process.env.E2E_RUN_ID ?? `${Date.now().toString(36)}${randomBytes(2).toString("hex")}`;
export const PREFIX = `e2e-${RUN}`;
export const USERS = {
  adminA: process.env.E2E_USER_ADMIN_A ?? "admin-a",
  fdeA: process.env.E2E_USER_FDE_A ?? "fde-a1",
  adminB: process.env.E2E_USER_ADMIN_B ?? "admin-b",
  fdeB: process.env.E2E_USER_FDE_B ?? "fde-b1",
};
export type Portal = "admin" | "fde";

const LOCAL = new Set(["localhost", "127.0.0.1", "::1"]);

/** Throws unless the target is this machine or the named staging host (never production). */
export function guard(baseURL: string | undefined): void {
  if (!PASSWORD) throw new Error("set E2E_SEED_PASSWORD for the test users");
  const host = new URL(baseURL ?? "http://invalid").hostname;
  const staging = process.env.E2E_STAGING_HOST;
  if (!LOCAL.has(host) && host !== staging)
    throw new Error(
      `refusing to run end-to-end tests against ${host}: not local or E2E_STAGING_HOST`,
    );
}

/** The real login: the portal's sign-in button, then Keycloak's identity-first pages. */
export async function keycloakLogin(page: Page, portal: Portal, username: string, query = "") {
  await page.goto(`/login-${portal}${query}`);
  await page.getByRole("button", { name: "Sign in with Keycloak" }).click();
  await page.waitForURL(/\/realms\/bullm\//);
  // Organizations make the login identity-first: username page, then password page.
  await page.locator("#username").fill(username);
  await page.locator("#kc-login").click();
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
}

/** A fresh browser context logged in as `username`, on its portal's landing page. */
export async function signedIn(browser: Browser, portal: Portal, username: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await keycloakLogin(page, portal, username);
  await page.waitForURL(portal === "fde" ? "**/projects" : "**/admin/**");
  return page;
}

/** The SBAM API through the web app's proxy, with this page's session (as the browser calls it). */
export async function api<T = unknown>(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
  expected = [200, 201, 202, 204],
): Promise<T> {
  const origin = new URL(page.url()).origin;
  const res = await page.request.fetch(`${origin}/api/proxy/api/v1${path}`, {
    method,
    headers: { origin, ...(body === undefined ? {} : { "content-type": "application/json" }) },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(expected, `${method} ${path} -> ${res.status()} ${await res.text()}`).toContain(
    res.status(),
  );
  return (res.status() === 204 ? undefined : await res.json()) as T;
}

type User = { id: string; email: string | null };

/** A project of this run (named with PREFIX), with `fde` assigned. The FDE must have signed in once,
 * which creates their user row. Returns its id; `archive` it when done. */
export async function newProject(admin: Page, title: string, fde: string): Promise<string> {
  const project = await api<{ id: string }>(admin, "POST", "/admin/projects", {
    name: `${PREFIX} ${title}`.slice(0, 120),
  });
  const users = await api<{ items: User[] }>(admin, "GET", "/admin/users?limit=100");
  const member = users.items.find((u) => u.email?.startsWith(`${fde}@`));
  if (!member) throw new Error(`${fde} has no user row yet: sign them in first`);
  await api(admin, "PUT", `/admin/projects/${project.id}/members`, { user_ids: [member.id] });
  return project.id;
}

export const archive = (admin: Page, projectId: string) =>
  api(admin, "POST", `/admin/projects/${projectId}/archive`, undefined, [200, 409]);

export type FilePayload = { name: string; mimeType: string; buffer: Buffer };

/** Pick files in the Documents page's Uppy dashboard and upload them (tusd, as a user does). */
export async function uploadDocuments(page: Page, projectId: string, files: FilePayload[]) {
  await page.goto(`/projects/${projectId}/documents`);
  await page.locator("input.uppy-Dashboard-input[type=file]").first().setInputFiles(files);
  const n = files.length;
  await page.getByRole("button", { name: `Upload ${n} file${n === 1 ? "" : "s"}` }).click();
  for (const f of files)
    await expect(page.getByRole("cell", { name: f.name, exact: true })).toBeVisible({
      timeout: 60_000,
    });
}

/** A multipart POST through the proxy (the masters workbook import). */
export async function postFile(
  page: Page,
  path: string,
  file: FilePayload,
  fields: Record<string, string> = {},
): Promise<{ status: number; body: unknown }> {
  const origin = new URL(page.url()).origin;
  const res = await page.request.fetch(`${origin}/api/proxy/api/v1${path}`, {
    method: "POST",
    headers: { origin },
    multipart: { ...fields, file },
  });
  const text = await res.text();
  return { status: res.status(), body: text ? JSON.parse(text) : undefined };
}
