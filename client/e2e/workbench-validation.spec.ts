import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #766: the Validation screen of the workbench (automated checks, certification evidence, secrets, Commit to Staging).
// The default project runs `next dev` with the dev role cookie and no sbam-api, so the API is faked at the proxy.
// Each state is saved as a screenshot (VALIDATION_SHOTS or test-results/validation-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SHOTS = process.env.VALIDATION_SHOTS ?? path.join("test-results", "validation-shots");
const HASH = "sha256:" + "ab".repeat(32);
const DIGEST = "sha256:" + "cd".repeat(32);

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: detail, status, detail }, status);

const NAMES = ["WORKFLOW_CERTIFIED", "SUB_SKILLS_CERTIFIED", "MCP_SERVER", "UI_BUNDLE", "SECRETS"];
const checks = (failing: Record<string, string> = {}) =>
  NAMES.map((name) => ({ name, passed: !(name in failing), reason: failing[name] ?? null }));
const status = (over: Record<string, unknown> = {}) => ({
  workflow_id: WORKFLOW,
  manifest_hash: HASH,
  ready: true,
  checks: checks(),
  blockers: [],
  state: "NOT_COMMITTED",
  ...over,
});
const secret = (over: Record<string, unknown> = {}) => ({
  name: "LEDGER_API_KEY",
  source: "ISATVON",
  purpose: "Used by Ledger sync",
  where_to_get: "",
  required: true,
  status: "MISSING",
  masked_tail: "",
  test_mode: "HTTP",
  row_version: 0,
  ...over,
});

type Handler = (route: Route) => Promise<void>;

async function open(
  page: Page,
  baseURL: string,
  commit: Handler,
  connections: Handler = (r) => json(r, { project_id: PROJECT, connections: [] }),
) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
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
    if (p === `/projects/${PROJECT}/workflows/${WORKFLOW}/commit`) return commit(route);
    if (p === `/projects/${PROJECT}/connections`) return connections(route);
    if (p === `/projects/${PROJECT}/workflows/${WORKFLOW}/builds`)
      return json(route, {
        workflow_id: WORKFLOW,
        workflow_state: "CERTIFIED",
        active: false,
        sub_skills: [],
      });
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench#/validation/commit`);
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `766-validation-${name}.png`), fullPage: true });

test("e766_t01 ready: every check passes, the secrets are set, Commit is open", async ({
  page,
  baseURL,
}) => {
  await open(
    page,
    baseURL!,
    (route) => json(route, status()),
    (route) =>
      json(route, {
        project_id: PROJECT,
        connections: [
          secret({
            status: "TESTED",
            masked_tail: "1716",
            row_version: 2,
            test_config: { url: "https://ledger.example.com/ping" },
          }),
        ],
      }),
  );
  await expect(page.getByText(/Every check passes/)).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Automated checks" }).getByRole("listitem"),
  ).toHaveCount(5);
  await expect(page.getByLabel("Secret LEDGER_API_KEY")).toContainText("ends 1716");
  await shot(page, "loaded");
  await page.locator(".bam-btn").click();
  await expect(page.getByRole("heading", { name: "Commit to Staging?" })).toBeVisible();
  await expect(page.getByLabel("Secrets in this commit")).toContainText("LEDGER_API_KEY");
  await shot(page, "commit-dialog");
});

test("e766_t02 blocked: a missing secret and a check that did not pass", async ({
  page,
  baseURL,
}) => {
  await open(
    page,
    baseURL!,
    (route) =>
      json(
        route,
        status({
          ready: false,
          checks: checks({ SECRETS: "LEDGER_API_KEY MISSING" }),
          blockers: [{ name: "LEDGER_API_KEY", reason: "MISSING" }],
        }),
      ),
    (route) => json(route, { project_id: PROJECT, connections: [secret()] }),
  );
  await expect(page.getByText(/Commit is blocked/)).toBeVisible();
  await expect(page.getByLabel("Secret LEDGER_API_KEY")).toContainText("Missing");
  await expect(page.locator(".gate-reason")).toContainText("LEDGER_API_KEY MISSING");
  await shot(page, "commit-blocked-missing-secret");
});

test("e766_t03 committed: the Staging entry and a runtime that is not built yet", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (route) =>
    json(
      route,
      status({
        state: "COMMITTED",
        committed_by: "fde-1",
        committed_at: "2026-11-19T10:00:00Z",
        staging: {
          status: "PENDING_RUNTIME",
          detail: "the Staging runtime (#765) is not available yet",
        },
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
      }),
    ),
  );
  await expect(page.getByText(/Committed to Staging as version 1.0.0/)).toBeVisible();
  await expect(page.getByLabel("Staging entry")).toContainText("wf_onboarding_v1_0_stg");
  await shot(page, "committed");
});

test("e766_t04 error: the server's detail and a retry", async ({ page, baseURL }) => {
  await open(page, baseURL!, (route) => problem(route, 503, "The registry is unavailable"));
  await expect(page.getByText("Could not load the checks")).toBeVisible();
  await expect(page.getByText("The registry is unavailable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await shot(page, "error");
});

test("e766_t05 forbidden: an unassigned FDE gets the not-allowed state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (route) => problem(route, 403, "Forbidden"));
  await expect(page.getByText("You can't validate this workflow")).toBeVisible();
  await shot(page, "forbidden");
});

test("e766_t06 loading: the checks are still coming", async ({ page, baseURL }) => {
  await open(page, baseURL!, () => new Promise<void>(() => {}));
  await expect(page.getByLabel("Loading the checks")).toBeVisible();
  await shot(page, "loading");
});

test("e766_t07 empty: no secret is needed", async ({ page, baseURL }) => {
  await open(page, baseURL!, (route) => json(route, status()));
  await expect(page.getByText("No secrets are needed")).toBeVisible();
  await shot(page, "empty-no-secrets");
});

test("e766_t08 secrets: error and forbidden states of the panel", async ({ page, baseURL }) => {
  await open(
    page,
    baseURL!,
    (route) => json(route, status()),
    (route) => problem(route, 503, "The secret store is unavailable"),
  );
  await expect(page.getByText("Could not load the secrets")).toBeVisible();
  await shot(page, "secrets-error");
});

test("e766_t08b secrets: forbidden", async ({ page, baseURL }) => {
  await open(
    page,
    baseURL!,
    (route) => json(route, status()),
    (route) => problem(route, 403, "Forbidden"),
  );
  await expect(page.getByText("You can't see this project's secrets")).toBeVisible();
  await shot(page, "secrets-forbidden");
});

test("e766_t09 secrets: set a value and a test address", async ({ page, baseURL }) => {
  const writes: string[] = [];
  await open(
    page,
    baseURL!,
    (route) =>
      json(route, status({ ready: false, checks: checks({ SECRETS: "LEDGER_API_KEY MISSING" }) })),
    (route) => json(route, { project_id: PROJECT, connections: [secret()] }),
  );
  // Registered after `open`, so it is matched first (the later route wins).
  await page.route("**/api/proxy/api/v1/projects/*/connections/**", async (route) => {
    writes.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    return json(route, {
      connection: secret({ status: "SET", masked_tail: "1716", row_version: 1 }),
    });
  });
  const row = page.getByLabel("Secret LEDGER_API_KEY");
  await row.getByLabel("Value").fill("typed-secret-value-0123");
  await row.getByRole("button", { name: "Save", exact: true }).click();
  await expect
    .poll(() => writes.some((w) => w.startsWith("PUT") && w.endsWith("/value")))
    .toBe(true);
  await row.getByLabel("Test address (https)").fill("https://ledger.example.com/ping");
  await shot(page, "secrets-editing");
  await expect(page.locator("body")).not.toContainText("typed-secret-value-0123");
});

const REGISTRATION = {
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
};
const COMMIT = "**/api/proxy/api/v1/projects/*/workflows/*/commit";
const confirm = (page: Page) =>
  page.locator("dialog", { hasText: "Commit to Staging?" }).locator(".btn-primary");

test("e766_t10 commit success: confirm, send, then committed", async ({ page, baseURL }) => {
  let committed = false;
  const sent: { match: string | null; key: string | null }[] = [];
  await open(page, baseURL!, (route) =>
    json(
      route,
      committed
        ? status({
            state: "COMMITTED",
            committed_by: "fde-1",
            staging: {
              status: "PENDING_RUNTIME",
              detail: "the Staging runtime is not available yet",
            },
            registration: REGISTRATION,
          })
        : status(),
    ),
  );
  await page.route(COMMIT, async (route, request) => {
    if (request.method() !== "POST") return route.fallback();
    sent.push({
      match: request.headers()["if-match"] ?? null,
      key: request.headers()["idempotency-key"] ?? null,
    });
    committed = true;
    return json(route, { manifest_hash: HASH, run: { id: "r1", state: "RUNNING" } }, 202);
  });
  await page.locator(".bam-btn").click();
  await confirm(page).click();
  await expect(page.getByText(/Committed to Staging as version 1.0.0/)).toBeVisible();
  expect(sent).toHaveLength(1);
  expect(sent[0]!.match).toBe(`"${HASH}"`);
  expect(sent[0]!.key).toBeTruthy();
  await shot(page, "commit-success");
});

test("e766_t11 commit refused with 409: the server's reason is shown", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (route) => json(route, status()));
  await page.route(COMMIT, async (route, request) => {
    if (request.method() !== "POST") return route.fallback();
    return problem(route, 409, "The version you reviewed is not the current one; reload it.");
  });
  await page.locator(".bam-btn").click();
  await confirm(page).click();
  await expect(page.getByText(/not the current one; reload it/)).toBeVisible();
  await shot(page, "commit-refused-409");
});
