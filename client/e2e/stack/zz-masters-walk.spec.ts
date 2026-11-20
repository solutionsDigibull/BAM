// Walkthrough probe of Masters 0.3 on the real stack (#781 test report). Runs only with E2E_STACK=1.
/* eslint-disable @typescript-eslint/no-explicit-any -- probes untyped API JSON */
import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { archive, fixture, guard, newProject, postFile, PREFIX, signedIn, USERS } from "./stack";

const OUT = process.env.WALK_OUT ?? "walkthrough-out/masters";
const results: { step: string; ok: boolean; detail: string }[] = [];
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const TYPES = ["ORGANIZATION", "RULES", "AGENTS", "SKILLS", "TOOLING"] as const;
const ID_KEY = {
  ORGANIZATION: "org_id",
  RULES: "rule_id",
  AGENTS: "agent_id",
  SKILLS: "skill_id",
  TOOLING: "tooling_id",
};
const RUN = PREFIX.replace(/[^a-z0-9]/gi, "")
  .slice(-8)
  .toUpperCase();

async function raw(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
) {
  const origin = new URL(page.url()).origin;
  const res = await page.request.fetch(`${origin}/api/proxy/api/v1${path}`, {
    method,
    headers: {
      origin,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...headers,
    },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json: any = undefined;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    json = text;
  }
  return {
    status: res.status(),
    headers: res.headers(),
    json,
    body: await res.body().catch(() => Buffer.alloc(0)),
  };
}

function check(step: string, ok: boolean, detail = "") {
  results.push({ step, ok, detail });
  expect.soft(ok, `${step}: ${detail}`).toBe(true);
}

test.beforeAll(({ baseURL }) => {
  guard(baseURL);
  mkdirSync(OUT, { recursive: true });
});
test.afterAll(() => writeFileSync(`${OUT}/report.json`, JSON.stringify(results, null, 2)));

test("masters 0.3 in depth on the real stack", async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const adminB = await signedIn(browser, "admin", USERS.adminB);
  const project = await newProject(admin, "masters-walk", USERS.fdeA);
  try {
    // master-types: the id column first, 0.3 headers
    const types = (await raw(admin, "GET", "/admin/master-types")).json;
    for (const t of TYPES) {
      const info =
        types.items?.find?.((i: any) => i.master_type === t) ??
        types[t] ??
        types.types?.find?.((i: any) => i.master_type === t);
      check(
        `master-types ${t}: id column first`,
        info?.columns?.[0]?.field === ID_KEY[t],
        JSON.stringify(info?.columns?.[0]),
      );
    }

    // per type: blank id 422, create, read, version, stale If-Match
    const created = {} as Record<(typeof TYPES)[number], { id: string; etag: string }>;
    const PAYLOAD: Record<string, Record<string, unknown>> = {
      ORGANIZATION: { company_type: "Walk Co - HQ" },
      RULES: { rule_type_description: "walk rule", record_status: "Proposed" },
      AGENTS: { agent_name: "Walk agent", reference: "https://github.com/example/walk-agent" },
      SKILLS: { skill: "walk skill" },
      TOOLING: {
        tool_name: "Walk tool",
        category: "MCP",
        record_status: "Proposed",
        links_path: "https://github.com/example/walk-tool",
      },
    };
    for (const t of TYPES) {
      const blank = await raw(admin, "POST", `/admin/masters/${t}`, { payload: PAYLOAD[t] });
      check(
        `${t}: blank id is 422`,
        blank.status === 422,
        `${blank.status} ${JSON.stringify(blank.json?.errors?.map((e: any) => e.path))}`,
      );
      const id = `WALK-${t.slice(0, 3)}-${RUN}`;
      const res = await raw(admin, "POST", `/admin/masters/${t}`, {
        payload: { [ID_KEY[t]]: id, ...PAYLOAD[t] },
      });
      check(
        `${t}: create 201, row label = id`,
        res.status === 201 && res.json?.row_label === id && res.json?.stable_id === id,
        `${res.status} ${res.json?.row_label}`,
      );
      created[t] = { id: res.json?.id, etag: res.headers["etag"] ?? "" };
      const got = await raw(admin, "GET", `/admin/masters/${t}/${res.json?.id}`);
      check(
        `${t}: read back`,
        got.status === 200 && got.json?.payload?.[ID_KEY[t]] === id,
        String(got.status),
      );
      const v2 = await raw(
        admin,
        "POST",
        `/admin/masters/${t}/${res.json?.id}/versions`,
        {
          payload: {
            ...got.json.payload,
            [t === "SKILLS" ? "skill" : (Object.keys(PAYLOAD[t] ?? {})[0] ?? "x")]: "edited",
          },
        },
        { "if-match": got.headers["etag"] ?? "" },
      );
      check(
        `${t}: edit = version 2`,
        v2.status === 201 && v2.json?.version === 2,
        `${v2.status} v${v2.json?.version}`,
      );
      const stale = await raw(
        admin,
        "POST",
        `/admin/masters/${t}/${res.json?.id}/versions`,
        { payload: got.json.payload },
        { "if-match": got.headers["etag"] ?? "" },
      );
      check(
        `${t}: stale If-Match refused`,
        [409, 412].includes(stale.status),
        `${stale.status} ${stale.json?.code}`,
      );
      const none = await raw(admin, "POST", `/admin/masters/${t}/${res.json?.id}/versions`, {
        payload: got.json.payload,
      });
      check(
        `${t}: missing If-Match is 428`,
        none.status === 428,
        `${none.status} ${none.json?.code}`,
      );
    }

    // status: Rules/Tooling from record_status; others keep their entry status
    const rule = await raw(admin, "GET", `/admin/masters/RULES/${created.RULES.id}`);
    const act = await raw(
      admin,
      "POST",
      `/admin/masters/RULES/${created.RULES.id}/versions`,
      { payload: { ...rule.json.payload, record_status: "Active" } },
      { "if-match": rule.headers["etag"] ?? "" },
    );
    check(
      "RULES: record_status Active -> APPROVED",
      act.json?.status === "APPROVED",
      act.json?.status,
    );
    const sk = await raw(admin, "GET", `/admin/masters/SKILLS/${created.SKILLS.id}`);
    check(
      "SKILLS: no status column, entry keeps PROPOSED",
      sk.json?.status === "PROPOSED",
      sk.json?.status,
    );

    // project scope: a project's own entry shows only with ?project_id
    const pr = await raw(admin, "POST", "/admin/masters/RULES", {
      project_id: project,
      payload: { rule_id: `WALK-PRJ-${RUN}`, rule_type_description: "project rule" },
    });
    check(
      "project-scoped entry created",
      pr.status === 201 && pr.json?.project_id === project,
      String(pr.status),
    );
    const tenantList = (await raw(admin, "GET", "/admin/masters/RULES")).json.items.map(
      (e: any) => e.stable_id,
    );
    const projectList = (
      await raw(admin, "GET", `/admin/masters/RULES?project_id=${project}`)
    ).json.items.map((e: any) => e.stable_id);
    check("tenant list excludes the project rule", !tenantList.includes(`WALK-PRJ-${RUN}`), "");
    check("project list has the project rule", projectList.includes(`WALK-PRJ-${RUN}`), "");

    // template + export + re-import unchanged + validate-only + structure change
    const tpl = await raw(admin, "GET", "/admin/masters/template");
    check(
      "template downloads as xlsx",
      tpl.status === 200 &&
        (tpl.headers["content-type"] ?? "").includes("spreadsheetml") &&
        tpl.body.length > 5_000,
      `${tpl.status} ${tpl.body.length}B`,
    );
    const exp = await raw(admin, "GET", "/admin/masters/export");
    check(
      "export downloads as xlsx",
      exp.status === 200 && exp.body.length > 5_000,
      `${exp.status} ${exp.body.length}B`,
    );
    writeFileSync(`${OUT}/export.xlsx`, exp.body);
    const re = await postFile(
      admin,
      "/admin/masters/import",
      { name: "export.xlsx", mimeType: XLSX, buffer: exp.body },
      { mode: "validate-only" },
    );
    const tabs = (re.body as any)?.tabs ?? [];
    const changed = tabs.reduce(
      (n: number, t: any) => n + (t.created ?? 0) + (t.updated ?? 0) + (t.errors?.length ?? 0),
      0,
    );
    check(
      "export -> validate-only re-import: nothing changes",
      re.status === 200 && changed === 0,
      JSON.stringify(
        tabs.map((t: any) => [t.name, t.created, t.updated, t.unchanged, t.errors?.length]),
      ),
    );
    const up = await postFile(
      admin,
      "/admin/masters/import",
      fixture("masters/masters_0_3.xlsx", XLSX),
      { mode: "upsert" },
    );
    check(
      "0.3 fixture imports",
      up.status === 200 && (up.body as any)?.applied === true,
      `${up.status} ${JSON.stringify((up.body as any)?.tabs?.map((t: any) => [t.name, t.created, t.errors?.length]))}`,
    );
    const again = await postFile(
      admin,
      "/admin/masters/import",
      fixture("masters/masters_0_3.xlsx", XLSX),
      { mode: "upsert" },
    );
    const again2 = ((again.body as any)?.tabs ?? []).reduce(
      (n: number, t: any) => n + (t.created ?? 0) + (t.updated ?? 0),
      0,
    );
    check(
      "re-import of the same workbook is a no-op",
      again.status === 200 && again2 === 0,
      JSON.stringify((again.body as any)?.tabs?.map((t: any) => [t.name, t.created, t.updated])),
    );
    const bad = await postFile(
      admin,
      "/admin/masters/import",
      fixture("masters/structure_changed_rules.xlsx", XLSX),
      { mode: "upsert" },
    );
    check(
      "structure-changed workbook is 422 with the diff",
      bad.status === 422 && (bad.body as any)?.errors?.[0]?.diff?.actual === "Owner",
      `${bad.status} ${JSON.stringify((bad.body as any)?.errors?.[0]?.diff)}`,
    );

    // repo catalog (FDE, project): Agents reference + Tooling links_path
    const cat = await raw(fde, "GET", `/projects/${project}/repo-catalog`);
    const urls = (cat.json?.candidates ?? []).map((c: any) => c.url);
    check(
      "repo catalog reads Agents reference",
      urls.some((u: string) => u.includes("walk-agent")),
      `${cat.status} ${urls.length} urls`,
    );
    check(
      "repo catalog reads Tooling links_path",
      urls.some((u: string) => u.includes("walk-tool")),
      "",
    );
    check(
      "repo catalog structure_version is 0.3",
      String(cat.json?.structure_version ?? "").includes("0.3"),
      String(cat.json?.structure_version),
    );

    // RBAC / IDOR
    const fdeList = await raw(fde, "GET", "/admin/masters/RULES");
    check(
      "FDE cannot read admin masters",
      [403, 404].includes(fdeList.status),
      String(fdeList.status),
    );
    const fdeWrite = await raw(fde, "POST", "/admin/masters/RULES", {
      payload: { rule_id: "X", rule_type_description: "x" },
    });
    check(
      "FDE cannot write admin masters",
      [403, 404].includes(fdeWrite.status),
      String(fdeWrite.status),
    );
    const cross = await raw(adminB, "GET", `/admin/masters/RULES/${created.RULES.id}`);
    check("admin-b gets 404 on tenant A's entry", cross.status === 404, String(cross.status));
    const crossV = await raw(
      adminB,
      "POST",
      `/admin/masters/RULES/${created.RULES.id}/versions`,
      { payload: { rule_id: "X" } },
      { "if-match": '"1"' },
    );
    check(
      "admin-b cannot version tenant A's entry (404)",
      crossV.status === 404,
      String(crossV.status),
    );
    const crossList = (await raw(adminB, "GET", "/admin/masters/RULES")).json?.items ?? [];
    check(
      "admin-b's list has none of tenant A's ids",
      !crossList.some((e: any) => String(e.stable_id).includes(RUN)),
      `${crossList.length} items`,
    );
    const crossProj = await raw(adminB, "GET", `/admin/masters/RULES?project_id=${project}`);
    check(
      "admin-b with tenant A's project_id is 404",
      crossProj.status === 404,
      String(crossProj.status),
    );

    // documents list still works; UI renders each tab with the 0.3 columns
    const docs = await raw(admin, "GET", "/admin/master-documents");
    check("master documents list", docs.status === 200, String(docs.status));
    await admin.goto("/admin/masters");
    await admin.waitForTimeout(3_000);
    await admin.screenshot({ path: `${OUT}/masters-screen.png`, fullPage: true });
    for (const tab of ["Organization", "Rules", "Agents", "Skills", "Tooling"]) {
      const t = admin.getByRole("tab", { name: new RegExp(tab, "i") }).first();
      if (await t.isVisible().catch(() => false)) await t.click();
      await admin.waitForTimeout(1_500);
      await admin.screenshot({ path: `${OUT}/tab-${tab}.png`, fullPage: true });
      const text = await admin.locator("body").innerText();
      check(`UI ${tab} tab shows 0.3 id column`, /ID/.test(text), "");
    }
  } finally {
    await archive(admin, project);
  }
});
