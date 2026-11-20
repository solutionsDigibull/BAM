import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #777: the Context step names why a knowledge build was ended at once (the model account is out of credit, or the
// knowledge token cap was reached) and what to do. The API is faked at the proxy; every state is saved as a screenshot
// (CONTEXT_SHOTS or test-results/context-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const KV = "0190f3a2-7b1c-7d4e-8f00-0000000000a1";
const SHOTS = process.env.CONTEXT_SHOTS ?? path.join("test-results", "context-shots");

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: detail, status, detail }, status);

const failed = (reason: string | null) => ({
  id: KV,
  version: 1,
  status: "FAILED",
  error_reason: reason,
  counts: { documents: 1, entities: 0, relations: 0, chunks: 0 },
  coverage: { documents: [], entities: 0, masters_included: 0 },
  created_at: "2026-11-20T10:00:00Z",
});

async function open(page: Page, baseURL: string, knowledge: (r: Route) => Promise<void>) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/documents`)
      return json(route, { items: [], next_cursor: null });
    if (p === `/projects/${PROJECT}/pipeline`)
      return json(route, {
        state: "KNOWLEDGE_FAILED",
        steps: [],
        counts: { uploaded: 1, parsed: 1, rejected: 0, failed: 0 },
      });
    if (p === `/projects/${PROJECT}/knowledge`) return knowledge(route);
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench#/rag/review`);
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `777-context-${name}.png`), fullPage: true });

test("e777_t01 a credit refusal says the model account is out of credit and what to do", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, { versions: [failed("ModelCreditExhausted")] }));
  await expect(page.getByText("The model account is out of credit.")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(/add credit to the model provider account/)).toBeVisible({
    timeout: 20_000,
  });
  await shot(page, "credit");
});

test("e777_t02 a reached cap says the build was stopped and who raises it", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, { versions: [failed("KnowledgeTokenCap")] }));
  await expect(page.getByText(/reached its token limit/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Ask an Admin to raise the limit/)).toBeVisible({ timeout: 20_000 });
  await shot(page, "cap");
});

test("e777_t03 any other failure keeps its own reason", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { versions: [failed("EmptyCorpus")] }));
  await expect(page.getByText(/EmptyCorpus/)).toBeVisible({ timeout: 20_000 });
  await shot(page, "other");
});

test("e777_t04 loading", async ({ page, baseURL }) => {
  let calls = 0;
  // the workbench snapshot reads the knowledge first; a later request (the Context step) never answers
  await open(page, baseURL!, (r) =>
    ++calls <= 2 ? json(r, { versions: [] }) : new Promise<void>(() => {}),
  );
  await expect(page.getByRole("status", { name: "Loading the knowledge" })).toBeVisible({
    timeout: 20_000,
  });
  await shot(page, "loading");
});

test("e777_t05 empty", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { versions: [] }));
  await expect(page.getByText("No knowledge yet")).toBeVisible({ timeout: 20_000 });
  await shot(page, "empty");
});

test("e777_t06 error", async ({ page, baseURL }) => {
  let calls = 0; // the workbench snapshot reads the knowledge first; the Context step then fails
  await open(page, baseURL!, (r) =>
    ++calls <= 2 ? json(r, { versions: [] }) : problem(r, 500, "The knowledge service failed"),
  );
  await expect(page.getByText("Could not load the knowledge")).toBeVisible({ timeout: 20_000 });
  await shot(page, "error");
});

test("e777_t07 forbidden", async ({ page, baseURL }) => {
  let calls = 0;
  await open(page, baseURL!, (r) =>
    ++calls <= 2 ? json(r, { versions: [] }) : problem(r, 403, "Forbidden"),
  );
  await expect(page.getByText("You can't see this project's knowledge")).toBeVisible({
    timeout: 20_000,
  });
  await shot(page, "forbidden");
});
