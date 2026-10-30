import { expect, test, type Page, type Request, type Route } from "@playwright/test";

// Knowledge Explorer (E5.5). The API is faked at the proxy; the canvas, layout worker and drawer are real.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const KV = "0190f3a2-7b1c-7d4e-8f00-00000000c0a1";
const API = "**/api/proxy/api/v1";
// the projects list, with or without the `?limit=` a list that follows its cursor sends
const PROJECTS = /\/api\/proxy\/api\/v1\/projects(\?.*)?$/;
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing",
  client_name: null,
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "SOW_REVIEW",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};

const version = (status = "READY") => ({
  id: KV,
  version: 1,
  status,
  coverage: { documents: [], entities: 3 },
  error_reason: null,
  created_at: "2026-10-16T10:00:00Z",
  finished_at: status === "READY" ? "2026-10-16T10:03:00Z" : null,
  okf_sha256: status === "READY" ? "ab".repeat(32) : null,
  counts: { documents: 1, entities: 3, relations: 2, chunks: 2 },
  documents: [],
});

type Node = { id: string; type: string; name: string; degree: number };
const node = (id: string, name: string, type = "Concept", degree = 1): Node => ({
  id,
  type,
  name,
  degree,
});

type Fake = {
  nodes: Node[];
  passages?: Record<string, string>;
  neighbours?: Record<string, Node[]>;
};

/** A graph API over `fake.nodes`: q / type filters, 500-node pages with a cursor. */
async function fakeGraph(page: Page, fake: Fake) {
  await page.route(`${API}/projects/${PROJECT}/knowledge/${KV}/graph**`, (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const types = (url.searchParams.get("type") ?? "").split(",").filter(Boolean);
    const offset = Number(url.searchParams.get("cursor") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "100");
    const matching = fake.nodes.filter(
      (n) => (!q || n.name.toLowerCase().includes(q)) && (!types.length || types.includes(n.type)),
    );
    const slice = matching.slice(offset, offset + limit);
    const ids = new Set(slice.map((n) => n.id));
    const edges = slice
      .slice(1)
      .map((n, i) => ({ id: `e${offset + i}`, type: "related_to", from: slice[i]!.id, to: n.id }))
      .filter((e) => ids.has(e.from));
    const end = offset + slice.length;
    return json(route, {
      knowledge_version_id: KV,
      status: "READY",
      nodes: slice,
      edges,
      next_cursor: end < matching.length ? String(end) : null,
      total_estimate: matching.length,
    });
  });
  await page.route(`${API}/projects/${PROJECT}/knowledge/${KV}/nodes/**`, (route) => {
    const id = decodeURIComponent(route.request().url().split("/nodes/")[1]!);
    const n = fake.nodes.find((x) => x.id === id)!;
    return json(route, {
      knowledge_version_id: KV,
      status: "READY",
      node: n,
      neighbours: fake.neighbours?.[id] ?? [],
      has_more: false,
      source_passages: [
        {
          document_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000d0c1",
          title: "rfq.pdf",
          location: { scope: "block", page: 2 },
          text: fake.passages?.[id] ?? `${n.name} appears here.`,
          truncated: false,
        },
      ],
    });
  });
}

async function open(page: Page, baseURL: string, versions: () => unknown[] = () => [version()]) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route(PROJECTS, (r) => json(r, { items: [project] }));
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) =>
    json(r, { versions: versions() }),
  );
  await page.goto(`/projects/${PROJECT}/knowledge`);
}

const canvasNodes = (page: Page) => page.locator(".react-flow__node");
const mutations = (requests: Request[]) =>
  requests.filter((r) => r.url().includes("/api/proxy/") && r.method() !== "GET");

test("e5_5_t01 explore: first page, search highlights, click shows passages", async ({
  page,
  baseURL,
}) => {
  await fakeGraph(page, {
    nodes: [
      node("inv", "Invoice approval", "Process"),
      node("m", "Mouser", "Supplier"),
      node("z", "ZEBRA-7731"),
    ],
  });
  await open(page, baseURL!);
  await expect(page.getByText("3 of 3 entities")).toBeVisible();
  await page.getByLabel("Search the graph").fill("Invoice");
  await expect(page.locator(".react-flow__node.kx-hit")).toHaveCount(1);
  await page.locator(".react-flow__node.kx-hit").click();
  await expect(page.getByText("Invoice approval appears here.")).toBeVisible();
  await expect(page.getByText("page 2")).toBeVisible();
});

test("e5_5_t02 expand 12 neighbours: added once, no duplicates", async ({ page, baseURL }) => {
  const spokes = Array.from({ length: 12 }, (_, i) => node(`s${i}`, `Spoke ${i}`));
  await fakeGraph(page, {
    nodes: [node("hub", "Hub", "Concept", 12), spokes[0]!],
    neighbours: { hub: spokes },
  });
  await open(page, baseURL!);
  await expect(page.getByText("2 of 2 entities")).toBeVisible();
  await canvasNodes(page).filter({ hasText: "Hub" }).click();
  await page.getByRole("button", { name: "Expand" }).click();
  await expect(page.getByText("13 of 2 entities")).toBeVisible(); // 2 from the page + 11 new
  await page.getByRole("button", { name: "Expand" }).click();
  await expect(page.getByText("13 of 2 entities")).toBeVisible();
});

test("e5_5_t03 2,000 nodes in 4 pages stay responsive (no task > 200 ms while panning)", async ({
  page,
  baseURL,
}) => {
  const nodes = Array.from({ length: 2000 }, (_, i) =>
    node(`n${String(i).padStart(4, "0")}`, `Entity ${i}`),
  );
  await fakeGraph(page, { nodes });
  await open(page, baseURL!);
  for (let loaded = 500; loaded < 2000; loaded += 500) {
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByText(`${loaded + 500} of 2000 entities`)).toBeVisible({
      timeout: 30_000,
    });
  }
  await page.waitForTimeout(3_000); // let the last page's layout land before measuring
  await page.evaluate(() => {
    (window as unknown as { longTasks: number[] }).longTasks = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries())
        (window as unknown as { longTasks: number[] }).longTasks.push(e.duration);
    }).observe({ type: "longtask", buffered: false });
  });
  const box = (await page.getByTestId("knowledge-canvas").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  for (let i = 0; i < 20; i++)
    await page.mouse.move(box.x + box.width / 2 - i * 10, box.y + box.height / 2);
  await page.mouse.up();
  await page.mouse.wheel(0, -400);
  const longest = await page.evaluate(() =>
    Math.max(0, ...(window as unknown as { longTasks: number[] }).longTasks),
  );
  expect(longest).toBeLessThan(200);
});

test("e5_5_t04 markup in a passage is shown literally, nothing runs", async ({ page, baseURL }) => {
  let dialogs = 0;
  page.on("dialog", (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  const evil = "<img src=x onerror=alert(1)>";
  await fakeGraph(page, { nodes: [node("x", "Exhibit")], passages: { x: evil } });
  await open(page, baseURL!);
  await canvasNodes(page).first().click();
  await expect(page.getByText(evil)).toBeVisible();
  expect(await page.locator(".ant-drawer img").count()).toBe(0);
  expect(dialogs).toBe(0);
});

test("e5_5_t05 read-only: no connect, no delete, no mutation request", async ({
  page,
  baseURL,
}) => {
  const requests: Request[] = [];
  page.on("request", (r) => requests.push(r));
  await fakeGraph(page, { nodes: [node("a", "Alpha"), node("b", "Beta")] });
  await open(page, baseURL!);
  await expect(canvasNodes(page)).toHaveCount(2);
  // edges render a moment after nodes; the fake links Alpha -> Beta, so wait for that one
  const edges = page.locator(".react-flow__edge");
  await expect(edges).toHaveCount(1);
  const [a, b] = [canvasNodes(page).nth(0), canvasNodes(page).nth(1)];
  const from = (await a.boundingBox())!;
  const to = (await b.boundingBox())!;
  await page.mouse.move(from.x + from.width, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y + to.height / 2);
  await page.mouse.up();
  await a.click();
  await page.keyboard.press("Delete");
  await page.keyboard.press("Backspace");
  await page.keyboard.press("Escape"); // close the side panel the click opened
  await expect(page.locator(".ant-drawer-open")).toHaveCount(0);
  await a.dblclick({ force: true });
  await expect(canvasNodes(page)).toHaveCount(2);
  expect(await edges.count()).toBe(1);
  expect(mutations(requests)).toEqual([]);
});

test("e5_5_t06 malformed graph data shows the empty state without errors", async ({
  page,
  baseURL,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(`${API}/projects/${PROJECT}/knowledge/${KV}/graph**`, (r) =>
    json(r, { nodes: null, edges: [{ from: "x", to: "y" }] }),
  );
  await open(page, baseURL!);
  await expect(page.getByText("This knowledge version has no entities yet.")).toBeVisible();
  expect(errors).toEqual([]);
});

test("e5_5_t07 another tenant's version is not found and nothing is drawn", async ({
  page,
  baseURL,
}) => {
  await page.route(`${API}/projects/${PROJECT}/knowledge/${KV}/graph**`, (r) =>
    r.fulfill({
      status: 404,
      contentType: "application/problem+json",
      body: JSON.stringify({ status: 404, code: "NOT_FOUND", title: "Not found" }),
    }),
  );
  await open(page, baseURL!);
  await expect(page.getByText(/not found/i).first()).toBeVisible({ timeout: 20_000 });
  await expect(canvasNodes(page)).toHaveCount(0);
});

test("e5_5_t08 building: progress, then the canvas when READY", async ({ page, baseURL }) => {
  await fakeGraph(page, { nodes: [node("a", "Alpha")] });
  let polls = 0;
  await open(page, baseURL!, () => {
    polls += 1;
    return [version(polls < 3 ? "BUILDING" : "READY")];
  });
  await expect(page.getByLabel("Building progress")).toBeVisible();
  await expect(canvasNodes(page)).toHaveCount(0);
  await expect(canvasNodes(page)).toHaveCount(1, { timeout: 20_000 });
});
