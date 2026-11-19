import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #861: the workbench's Staging mode. After Commit the toggle flips to the orange Staging mode (same login) and shows the
// committed workflow's generated UI; FDE mode shows no workflow UI. The default project runs `next dev` with the dev role
// cookie and no sbam-api, so the API is faked at the proxy and the UI bundle at its own route.
// Each state is saved as a screenshot (STAGING_SHOTS or test-results/staging-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SHOTS = process.env.STAGING_SHOTS ?? path.join("test-results", "staging-shots");
const HASH = "sha256:" + "ab".repeat(32);
const DIGEST = "sha256:" + "cd".repeat(32);
const W = `/projects/${PROJECT}/workflows/${WORKFLOW}`;

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: detail, status, detail }, status);

const deployment = (over: Record<string, unknown> = {}) => ({
  version: "1.0.0",
  registry_name: "wf_onboarding_v1_0_stg",
  state: "RUNNING",
  image_digest: DIGEST,
  ui_bundle_digest: "sha256:" + "ef".repeat(32),
  egress_allowed_hosts: ["api.ledger.example.com"],
  egress_enforced: true,
  secret_names: ["LEDGER_API_KEY"],
  started_at: "2026-11-19T10:00:00Z",
  ...over,
});
const staging = (over: Record<string, unknown> = {}) => ({
  workflow_id: WORKFLOW,
  state: "RUNNING",
  health: "HEALTHY",
  current: deployment(),
  ui_url: `/api/staging-ui/${PROJECT}/${WORKFLOW}/`,
  history: [deployment()],
  ...over,
});
const NAMES = ["WORKFLOW_CERTIFIED", "SUB_SKILLS_CERTIFIED", "MCP_SERVER", "UI_BUNDLE", "SECRETS"];
const commitStatus = (over: Record<string, unknown> = {}) => ({
  workflow_id: WORKFLOW,
  manifest_hash: HASH,
  ready: true,
  checks: NAMES.map((name) => ({ name, passed: true, reason: null })),
  blockers: [],
  state: "NOT_COMMITTED",
  ...over,
});
const BUNDLE = `<!doctype html><html><head><meta charset="utf-8"><title>Onboarding</title>
<style>body{font-family:Inter,system-ui,sans-serif;margin:0;background:#f6f8fb;color:#101828}
header{padding:14px 18px;background:#fff;border-bottom:1px solid #d0d5dd;font-weight:600}
main{display:grid;grid-template-columns:200px 1fr;min-height:420px}aside{padding:14px;border-right:1px solid #d0d5dd;background:#fff}
section{padding:18px}.step{padding:8px 10px;border-radius:6px;margin-bottom:6px;background:#e8f1fb;color:#0068a8}</style></head>
<body><header>Onboarding workflow (generated UI)</header><main><aside><div class="step">MS01 Ledger sync</div><div class="step">MS02 Review</div></aside>
<section><h2>Ledger sync</h2><p>Upload the ledger export, then run the step.</p></section></main></body></html>`;

type Handler = (route: Route) => Promise<void> | void;
interface Opts {
  staging?: Handler;
  commit?: Handler;
  notes?: Handler;
  post?: Handler;
}

async function open(page: Page, baseURL: string, o: Opts = {}) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route(`**/api/staging-ui/${PROJECT}/${WORKFLOW}/**`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: BUNDLE }),
  );
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/pipeline`)
      return json(route, {
        state: "WORKFLOW_CREATED",
        steps: [],
        counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0 },
      });
    if (p === `/projects/${PROJECT}/documents`)
      return json(route, { items: [], next_cursor: null });
    if (p === `/projects/${PROJECT}/knowledge`) return json(route, { versions: [] });
    if (p === `/projects/${PROJECT}/sow`)
      return json(route, { sow_id: "s1", current: null, approved: null, etag: "e" });
    if (p === `/projects/${PROJECT}/workflows`)
      return json(route, {
        items: [
          {
            id: WORKFLOW,
            name: "Onboarding",
            state: "CERTIFIED",
            created_at: "2026-11-10T10:00:00Z",
          },
        ],
      });
    if (p === `${W}/staging`) return (o.staging ?? ((r) => json(r, staging())))(route);
    if (p === `${W}/commit`)
      return route.request().method() === "POST"
        ? (o.post ?? ((r) => json(r, { run: null }, 202)))(route)
        : (o.commit ?? ((r) => json(r, commitStatus())))(route);
    if (p === `${W}/request-change`)
      return route.request().method() === "POST"
        ? (o.post ?? ((r) => json(r, { note: { id: "n1" } }, 202)))(route)
        : (o.notes ?? ((r) => json(r, { notes: [], used: 0, limit: 3 })))(route);
    if (p === `${W}/builds`)
      return json(route, {
        workflow_id: WORKFLOW,
        workflow_state: "CERTIFIED",
        active: false,
        sub_skills: [
          {
            sub_skill_id: "ss1",
            skill_id: "MS01.1",
            skill_name: "Ledger sync",
            state: "CERTIFIED",
            stage: "DONE",
          },
        ],
      });
    if (p === `/projects/${PROJECT}/connections`)
      return json(route, { project_id: PROJECT, connections: [] });
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench#/validation/commit`);
  await expect(page.locator(".bam-wb")).toBeVisible();
}

const toStaging = (page: Page) => page.locator("label.eg-staging").click();
const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `861-${name}.png`), fullPage: true });
const frame = (page: Page) => page.frameLocator('iframe[title="Workflow UI (Staging)"]');

test("e861_t01 running: the generated UI, the status strip and the history", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!);
  await toStaging(page);
  await expect(page.locator(".bam-wb")).toHaveAttribute("data-env", "Staging");
  await expect(frame(page).getByText("Onboarding workflow (generated UI)")).toBeVisible();
  const strip = page.getByLabel("Staging status");
  await expect(strip).toContainText("Running");
  await expect(strip).toContainText("Healthy");
  await expect(strip).toContainText("api.ledger.example.com");
  await expect(strip).toContainText("LEDGER_API_KEY");
  await expect(page.getByLabel("Version history")).toContainText("v1.0.0");
  await shot(page, "staging-running");
});

test("e861_t02 fde mode: no workflow UI at all, then the toggle shows it, then back hides it", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!);
  await expect(page.locator(".bam-wb")).toHaveAttribute("data-env", "FDE");
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(page.getByText("Onboarding workflow (generated UI)")).toHaveCount(0);
  await shot(page, "fde-mode-no-workflow-ui");
  await toStaging(page);
  await expect(page.locator("iframe")).toHaveCount(1);
  await shot(page, "toggle-staging-orange");
  await page.locator("label.eg-fde").click();
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("e861_t03 pending runtime: the detail, no UI, and the way to try again", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, {
    staging: (r) =>
      json(
        r,
        staging({
          state: "PENDING_RUNTIME",
          health: "UNKNOWN",
          ui_url: null,
          current: deployment({
            state: "PENDING_RUNTIME",
            detail: "the Staging runtime is not configured on this stack",
          }),
        }),
      ),
  });
  await toStaging(page);
  await expect(page.getByText(/Waiting for the Staging runtime\./)).toBeVisible();
  await expect(page.getByText(/not configured on this stack/)).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
  await shot(page, "staging-pending-runtime");
});

test("e861_t04 failed: the detail and a way back to Commit", async ({ page, baseURL }) => {
  await open(page, baseURL!, {
    staging: (r) =>
      json(
        r,
        staging({
          state: "FAILED",
          health: "UNKNOWN",
          ui_url: null,
          current: deployment({
            state: "FAILED",
            detail: "IMAGE_MISSING: the host does not hold the certified image",
          }),
        }),
      ),
  });
  await toStaging(page);
  await expect(page.getByText("Staging could not start the workflow")).toBeVisible();
  await expect(page.getByText(/IMAGE_MISSING/)).toBeVisible();
  await shot(page, "staging-failed");
  await page.getByRole("button", { name: /Commit again/ }).click();
  await expect(page.locator(".bam-wb")).toHaveAttribute("data-env", "Staging");
  await expect(page).toHaveURL(/#\/validation\/commit/);
});

test("e861_t05 superseded: a status instead of the UI", async ({ page, baseURL }) => {
  await open(page, baseURL!, {
    staging: (r) =>
      json(
        r,
        staging({
          state: "SUPERSEDED",
          health: "UNKNOWN",
          ui_url: null,
          current: deployment({ state: "SUPERSEDED", superseded_by: "1.0.1" }),
          history: [
            deployment({ version: "1.0.1", state: "RUNNING" }),
            deployment({ state: "SUPERSEDED", superseded_by: "1.0.1" }),
          ],
        }),
      ),
  });
  await toStaging(page);
  await expect(page.getByText("This version was superseded")).toBeVisible();
  await expect(page.getByLabel("Version history")).toContainText("v1.0.1");
  await shot(page, "staging-superseded");
});

test("e861_t06 stopped and unreachable", async ({ page, baseURL }) => {
  await open(page, baseURL!, {
    staging: (r) =>
      json(
        r,
        staging({
          state: "STOPPED",
          health: "UNKNOWN",
          ui_url: null,
          current: deployment({
            state: "STOPPED",
            stopped_at: "2026-11-19T11:00:00Z",
            detail: "stopped by the deployer",
          }),
        }),
      ),
  });
  await toStaging(page);
  await expect(page.getByText("Staging is stopped")).toBeVisible();
  await shot(page, "staging-stopped");
});

test("e861_t07 unreachable: the warning above the UI", async ({ page, baseURL }) => {
  await open(page, baseURL!, { staging: (r) => json(r, staging({ health: "UNREACHABLE" })) });
  await toStaging(page);
  await expect(page.getByText(/The workflow server does not answer/)).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  await shot(page, "staging-unreachable");
});

test("e861_t08 not found: never committed or not your project", async ({ page, baseURL }) => {
  await open(page, baseURL!, { staging: (r) => problem(r, 404, "Not found") });
  await toStaging(page);
  await expect(page.getByText("Nothing in Staging yet")).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
  await shot(page, "staging-not-found");
});

test("e861_t09 forbidden, error and loading", async ({ page, baseURL }) => {
  await open(page, baseURL!, { staging: (r) => problem(r, 403, "Forbidden") });
  await toStaging(page);
  await expect(page.getByText("You can't see Staging for this project")).toBeVisible();
  await shot(page, "staging-forbidden");
});

test("e861_t10 error: the server's detail and a retry", async ({ page, baseURL }) => {
  await open(page, baseURL!, {
    staging: (r) => problem(r, 503, "The Staging runtime is unavailable"),
  });
  await toStaging(page);
  await expect(page.getByText("Could not load Staging")).toBeVisible();
  await expect(page.getByText("The Staging runtime is unavailable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await shot(page, "staging-error");
});

test("e861_t11 loading: Staging is still being read", async ({ page, baseURL }) => {
  await open(page, baseURL!, { staging: () => new Promise<void>(() => {}) });
  await toStaging(page);
  await expect(page.getByLabel("Loading Staging")).toBeVisible();
  await shot(page, "staging-loading");
});

test("e861_t12 request change: a note, then the fourth is refused with the Grid as the way", async ({
  page,
  baseURL,
}) => {
  let posts = 0;
  await open(page, baseURL!, {
    post: (r) =>
      ++posts === 1
        ? json(r, { note: { id: "n1" } }, 202)
        : problem(r, 409, "This workflow version already has 3 change notes."),
  });
  await toStaging(page);
  await expect(page.getByRole("button", { name: "Send note" })).toBeDisabled();
  await page.getByLabel("What is wrong").fill("The total ignores tax.");
  await page.getByRole("button", { name: "Send note" }).click();
  await expect(page.getByText(/The note was sent/)).toBeVisible();
  await shot(page, "staging-request-change-sent");
  await page.getByLabel("What is wrong").fill("Another problem.");
  await page.getByRole("button", { name: "Send note" }).click();
  await expect(page.getByRole("button", { name: "Open the ISATVON Grid" })).toBeVisible();
  await shot(page, "staging-request-change-limit");
});

test("e861_t13 the flip: Commit finishes and the workbench switches to Staging by itself", async ({
  page,
  baseURL,
}) => {
  let committed = false;
  await open(page, baseURL!, {
    commit: (r) =>
      json(
        r,
        committed
          ? commitStatus({
              state: "COMMITTED",
              committed_by: "fde-1",
              committed_at: "2026-11-19T10:00:00Z",
              staging: { status: "STARTED", detail: null },
              registration: {
                workflow_id: WORKFLOW,
                version: "1.0.0",
                registry_name: "wf_onboarding_v1_0_stg",
                registry_id: "reg-1",
                image_digest: DIGEST,
                manifest_hash: HASH,
                certification_id: "0190f3a2-7b1c-7d4e-8f00-0000000000c9",
                owner: "fde-1",
                tools: [],
                kind: "staging",
                status: "REGISTERED",
                registered_at: "2026-11-19T10:00:00Z",
              },
            })
          : commitStatus(),
      ),
    post: (r) => {
      committed = true;
      return json(r, { run: null }, 202);
    },
  });
  await expect(page.locator(".bam-wb")).toHaveAttribute("data-env", "FDE");
  await shot(page, "flip-1-fde-before-commit");
  await page.locator(".bam-btn").click();
  await page.getByRole("button", { name: "Commit", exact: true }).last().click();
  await expect(page.locator(".bam-wb")).toHaveAttribute("data-env", "Staging");
  await expect(frame(page).getByText("Onboarding workflow (generated UI)")).toBeVisible();
  await shot(page, "flip-2-staging-after-commit");
});
