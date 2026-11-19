import path from "node:path";
import { mkdirSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

// #712: the knowledge graph's inspector shows the passages an entity was read from (read only). The API is faked at the
// proxy; each state is saved as a screenshot (NODE_SHOTS or test-results/node-shots).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const KV = "0190f3a2-7b1c-7d4e-8f00-0000000000a1";
const SHOTS = process.env.NODE_SHOTS ?? path.join("test-results", "node-shots");

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, detail: string) =>
  json(route, { type: "about:blank", title: detail, status, detail }, status);

const node = (over: Record<string, unknown>) => ({
  knowledge_version_id: KV,
  status: "READY",
  node: { id: "n1", type: "Customer", name: "Acme" },
  neighbours: [],
  source_passages: [],
  ...over,
});

async function open(page: Page, baseURL: string, nodeReply: (r: Route) => Promise<void>) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy/api/v1", "");
    if (p === `/projects/${PROJECT}/documents`)
      return json(route, { items: [], next_cursor: null });
    if (p === `/projects/${PROJECT}/pipeline`)
      return json(route, {
        state: "SOW_GENERATING",
        steps: [],
        counts: { uploaded: 1, parsed: 1, rejected: 0, failed: 0 },
      });
    if (p === `/projects/${PROJECT}/knowledge`)
      return json(route, {
        versions: [
          {
            id: KV,
            version: 1,
            status: "READY",
            counts: { documents: 1, entities: 2, relations: 1, chunks: 4 },
            created_at: "2026-11-09T10:00:00Z",
          },
        ],
      });
    if (p === `/projects/${PROJECT}/knowledge/${KV}/graph`)
      return json(route, {
        knowledge_version_id: KV,
        status: "READY",
        nodes: [
          { id: "n1", type: "Customer", name: "Acme" },
          { id: "n2", type: "Product", name: "Widget" },
        ],
        edges: [{ id: "e1", type: "orders", source: "n1", target: "n2" }],
        total_estimate: 2,
      });
    if (p === `/projects/${PROJECT}/knowledge/${KV}/nodes/n1`) return nodeReply(route);
    return problem(route, 404, "Not found");
  });
  mkdirSync(SHOTS, { recursive: true });
  await page.goto(`/projects/${PROJECT}/workbench#/rag/graph`);
  await page.getByText("Acme", { exact: true }).first().click();
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(SHOTS, `712-node-sources-${name}.png`), fullPage: true });
const inspector = (page: Page) => page.getByLabel("Acme details");

test("e712_t06 loaded: the passages an entity came from", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) =>
    json(
      r,
      node({
        source_passages: [
          {
            document_version_id: "dv1",
            title: "rfq.pdf",
            location: { page: 4 },
            text: "Acme asked for 500 units by March.",
          },
          {
            document_version_id: "dv2",
            title: "costs.xlsx",
            location: { sheet: "Costs", cell: "B7" },
            text: "Unit price 4.20",
          },
        ],
      }),
    ),
  );
  const list = inspector(page).getByRole("list", { name: "Source passages" });
  await expect(list).toContainText("rfq.pdf");
  await expect(list).toContainText("sheet Costs, cell B7");
  await shot(page, "loaded");
});

test("e712_t07 empty: no passage is recorded", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, node({})));
  await expect(inspector(page)).toContainText("No source passage is recorded");
  await shot(page, "empty");
});

test("e712_t08 error: the server's detail and Retry", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => problem(r, 503, "The graph store is unavailable"));
  await expect(inspector(page)).toContainText("The graph store is unavailable");
  await expect(inspector(page).getByRole("button", { name: "Retry" })).toBeVisible();
  await shot(page, "error");
});

test("e712_t09 forbidden: an account not assigned to the project", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Not assigned"));
  await expect(inspector(page)).toContainText("not assigned to this project");
  await shot(page, "forbidden");
});
