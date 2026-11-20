// Full-app walkthrough for a local E2E report run on the real stack. Runs only with E2E_STACK=1.
import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import {
  api,
  archive,
  fixture,
  guard,
  newProject,
  settled,
  signedIn,
  uploadDocuments,
  USERS,
} from "./stack";

const OUT = process.env.WALK_OUT ?? "walkthrough-out";
type Issue = { page: string; kind: string; detail: string };
const issues: Issue[] = [];
const visited: { page: string; status: string; shot: string }[] = [];

function watch(page: Page, who: string) {
  page.on("console", (m) => {
    if (m.type() === "error")
      issues.push({
        page: `${who} ${page.url()}`,
        kind: "console",
        detail: m.text().slice(0, 300),
      });
  });
  page.on("pageerror", (e) =>
    issues.push({
      page: `${who} ${page.url()}`,
      kind: "pageerror",
      detail: e.message.slice(0, 300),
    }),
  );
  page.on("requestfinished", async (req) => {
    const r = await req.response();
    if (!r) return;
    if (r.status() >= 500 || (r.status() >= 400 && r.url().includes("/api/")))
      issues.push({
        page: `${who} ${page.url()}`,
        kind: `http ${r.status()}`,
        detail: `${r.request().method()} ${r.url()}`,
      });
  });
}

async function visit(page: Page, who: string, path: string, name: string) {
  const res = await page.goto(path);
  await page.waitForTimeout(3_000);
  const shot = `${OUT}/${String(visited.length + 1).padStart(2, "0")}-${name}.png`;
  await page.screenshot({ path: shot, fullPage: true });
  const heading =
    (await page
      .locator("h1, h2")
      .first()
      .textContent({ timeout: 2_000 })
      .catch(() => null)) ?? "";
  visited.push({
    page: `${who} ${path}`,
    status: `${res?.status() ?? "?"} ${heading.trim().slice(0, 60)}`,
    shot,
  });
}

test.beforeAll(({ baseURL }) => {
  guard(baseURL);
  mkdirSync(OUT, { recursive: true });
});

test.afterAll(() =>
  writeFileSync(`${OUT}/report.json`, JSON.stringify({ visited, issues }, null, 2)),
);

test("walkthrough: admin -> FDE pipeline -> negatives", async ({ browser }) => {
  test.setTimeout(8 * 60_000);
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  watch(fde, "fde-a");
  const admin = await signedIn(browser, "admin", USERS.adminA);
  watch(admin, "admin-a");

  const project = await newProject(admin, "walkthrough", USERS.fdeA);
  try {
    for (const p of ["tenants", "projects", "users", "masters", "registry", "srim-defaults"])
      await visit(admin, "admin-a", `/admin/${p}`, `admin-${p}`);
    await visit(fde, "fde-a", "/projects", "fde-projects");
    await visit(fde, "fde-a", `/projects/${project}`, "fde-project-empty");
    const docs = [
      fixture("documents/sample.md", "text/markdown"),
      fixture("documents/sample.txt", "text/plain"),
      fixture("documents/sample.csv", "text/csv"),
    ];
    await uploadDocuments(fde, project, docs);
    const states = await settled(
      fde,
      project,
      docs.map((d) => d.name),
    );
    visited.push({
      page: "doc states",
      status: JSON.stringify(
        Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v.state])),
      ),
      shot: "",
    });
    await visit(fde, "fde-a", `/projects/${project}/documents`, "fde-documents-parsed");

    await fde.goto(`/projects/${project}`);
    await fde.getByRole("button", { name: "Start processing" }).click();
    let kStatus = "NONE";
    await expect
      .poll(
        async () => {
          const k = await api<{ versions: { status: string }[] }>(
            fde,
            "GET",
            `/projects/${project}/knowledge`,
          );
          kStatus = k.versions[0]?.status ?? "NONE";
          return ["READY", "FAILED"].includes(kStatus);
        },
        { timeout: 10 * 60_000, intervals: [5_000, 10_000] },
      )
      .toBe(true)
      .catch(() => {});
    visited.push({ page: "knowledge status", status: kStatus, shot: "" });
    await visit(fde, "fde-a", `/projects/${project}`, "fde-project-after-processing");
    await visit(fde, "fde-a", `/projects/${project}/knowledge`, "fde-knowledge");
    await fde.waitForTimeout(20_000); // give the pipeline a moment past knowledge
    await visit(fde, "fde-a", `/projects/${project}/sow`, "fde-sow");
    await visit(fde, "fde-a", `/projects/${project}/workflows`, "fde-workflows");
    const wf = await api<{ items?: { id: string }[] }>(
      fde,
      "GET",
      `/projects/${project}/workflows`,
      undefined,
      [200, 404],
    ).catch(() => ({ items: [] }));
    const w = wf?.items?.[0]?.id;
    if (w)
      for (const sub of ["", "isatvon", "code", "build", "manifests", "versions", "certification"])
        await visit(
          fde,
          "fde-a",
          `/projects/${project}/workflows/${w}/${sub}`,
          `fde-wf-${sub || "overview"}`,
        );
    else
      visited.push({
        page: "workflow pages",
        status: "no workflow (SOW not approved: mock LLM)",
        shot: "",
      });
    await visit(fde, "fde-a", "/registry", "fde-registry");

    // negatives
    const fdeB = await signedIn(browser, "fde", USERS.fdeB);
    watch(fdeB, "fde-b");
    await visit(fdeB, "fde-b", `/projects/${project}`, "neg-cross-tenant-project");
    const xt = await fdeB.request.get(
      `${new URL(fdeB.url()).origin}/api/proxy/api/v1/projects/${project}`,
      { headers: { origin: new URL(fdeB.url()).origin } },
    );
    visited.push({ page: "cross-tenant API GET", status: String(xt.status()), shot: "" });
    await visit(fde, "fde-a", "/admin/users", "neg-fde-on-admin");
  } finally {
    await archive(admin, project);
  }
});
