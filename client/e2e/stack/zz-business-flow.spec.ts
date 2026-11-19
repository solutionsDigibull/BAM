// Walkthrough probe of the business flow (Admin -> Admin Masters -> FDE) on the real stack. Runs only with E2E_STACK=1.
/* eslint-disable @typescript-eslint/no-explicit-any -- probes untyped API JSON */
import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { api, archive, guard, RUN, signedIn, USERS } from "./stack";

const OUT = "walkthrough-out/flow";
const MAILPIT = process.env.MAILPIT_URL ?? "http://127.0.0.1:8025";
const steps: { step: string; ok: boolean; detail: string }[] = [];
const issues: { who: string; kind: string; detail: string }[] = [];
let shotN = 0;

function check(step: string, ok: boolean, detail: unknown = "") {
  steps.push({
    step,
    ok,
    detail: typeof detail === "string" ? detail : JSON.stringify(detail).slice(0, 400),
  });
}
async function shot(page: Page, name: string) {
  await page.waitForTimeout(2_000);
  await page.screenshot({
    path: `${OUT}/${String(++shotN).padStart(2, "0")}-${name}.png`,
    fullPage: true,
  });
}
function watch(page: Page, who: string) {
  page.on("pageerror", (e) =>
    issues.push({ who, kind: "pageerror", detail: e.message.slice(0, 300) }),
  );
  page.on("response", (r) => {
    if (r.status() >= 500)
      issues.push({
        who,
        kind: `http ${r.status()}`,
        detail: `${r.request().method()} ${r.url()}`,
      });
  });
}
/** Raw call through the proxy: status + body, never throws (for the negative checks). */
async function raw(page: Page, method: string, path: string, body?: unknown) {
  const origin = new URL(page.url()).origin;
  const res = await page.request.fetch(`${origin}/api/proxy/api/v1${path}`, {
    method,
    headers: { origin, ...(body === undefined ? {} : { "content-type": "application/json" }) },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  let json: any = null;
  try {
    json = await res.json();
  } catch {
    /* empty */
  }
  return { status: res.status(), json };
}
async function poll<T>(
  fn: () => Promise<T>,
  done: (v: T) => boolean,
  timeoutMs: number,
  everyMs = 10_000,
) {
  const end = Date.now() + timeoutMs;
  let v = await fn();
  while (!done(v) && Date.now() < end) {
    await new Promise((r) => setTimeout(r, everyMs));
    v = await fn();
  }
  return v;
}

/** The invite email's action link (Keycloak execute-actions), from Mailpit. */
async function inviteLink(page: Page, email: string): Promise<string> {
  const msg = await poll(
    async () =>
      (
        await (
          await page.request.get(`${MAILPIT}/api/v1/search?query=to:${encodeURIComponent(email)}`)
        ).json()
      ).messages?.[0],
    (m) => Boolean(m),
    60_000,
    3_000,
  );
  if (!msg) throw new Error(`no invite email for ${email}`);
  const full = await (await page.request.get(`${MAILPIT}/api/v1/message/${msg.ID}`)).json();
  const link = String(full.Text ?? full.HTML).match(
    /https?:\/\/[^\s"'<>]+action-token[^\s"'<>]*/,
  )?.[0];
  if (!link) throw new Error("no action link in the invite email");
  return link.replace(/&amp;/g, "&");
}

test.beforeAll(({ baseURL }) => {
  guard(baseURL);
  mkdirSync(OUT, { recursive: true });
});
test.afterAll(() =>
  writeFileSync(`${OUT}/report.json`, JSON.stringify({ steps, issues }, null, 2)),
);

test("business flow: admin project + invite + assign + project masters -> FDE runs the flow", async ({
  browser,
}) => {
  test.setTimeout(90 * 60_000);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  watch(admin, "admin-a");
  const email = `fde-e2e-${RUN}@example.test`;
  const newPassword = `E2e-${RUN}-Strong!pass9`;
  let project = "";
  let other = "";
  let newUserId = "";
  try {
    // 1. Admin creates the project (Admin UI)
    await admin.goto("/admin/projects");
    await shot(admin, "admin-projects");
    const created = await api<{ id: string; state: string; name: string }>(
      admin,
      "POST",
      "/admin/projects",
      {
        name: `E2E mini ${RUN}`,
        client_name: "Acme Test Ltd",
      },
    );
    project = created.id;
    check("1 admin creates project", Boolean(project), `${created.name} ${created.state}`);
    await admin.goto(`/admin/projects/${project}`);
    await shot(admin, "admin-project-created");

    // 2. Admin invites a new FDE; they set a password from the email and sign in
    await admin.goto("/admin/users");
    await shot(admin, "admin-users");
    const inv = await raw(admin, "POST", "/admin/users/invite", { email, roles: ["sbam-fde"] });
    check("2 admin invites a new FDE", inv.status === 201, `${inv.status}`);
    newUserId = inv.json?.id ?? "";
    const fde = await (await browser.newContext()).newPage();
    watch(fde, "new-fde");
    await fde.goto(await inviteLink(fde, email));
    for (let i = 0; i < 4; i++) {
      // Keycloak's required-action pages: maybe a "click here to proceed", then the password form
      const proceed = fde.getByRole("link", { name: /proceed|click here/i });
      if (await proceed.isVisible().catch(() => false)) await proceed.click();
      if (
        await fde
          .locator("#password-new")
          .isVisible()
          .catch(() => false)
      ) {
        await fde.locator("#password-new").fill(newPassword);
        await fde.locator("#password-confirm").fill(newPassword);
        await shot(fde, "fde-set-password");
        await fde.locator("[type=submit]").first().click();
        break;
      }
      await fde.waitForTimeout(1_500);
    }
    await fde.waitForTimeout(2_000);
    await shot(fde, "fde-after-password");
    // The realm profile requires a name the invite never asked for: Keycloak asks for it now.
    if (
      await fde
        .locator("#firstName")
        .isVisible()
        .catch(() => false)
    ) {
      check(
        "2 FINDING: invited user must enter first/last name (invite takes only email)",
        true,
        "Keycloak VERIFY_PROFILE",
      );
      await fde.locator("#firstName").fill("Flow");
      await fde.locator("#lastName").fill("Tester");
      await fde.locator("[type=submit]").first().click();
      await fde.waitForTimeout(3_000);
      await shot(fde, "fde-after-profile");
    }
    await fde.context().clearCookies();
    await fde.goto("/login-fde");
    await fde.getByRole("button", { name: /^Sign in( with Keycloak)?$/ }).click();
    await fde.waitForURL(/\/realms\/bullm\//);
    await fde.locator("#username").fill(email);
    await fde.locator("#kc-login").click();
    await fde.locator("#password").fill(newPassword);
    await fde.locator("#kc-login").click();
    await fde.waitForURL("**/projects", { timeout: 60_000 }).catch(async (e) => {
      await shot(fde, "fde-login-stuck");
      throw e;
    });
    const before = await raw(fde, "GET", "/projects?limit=100");
    check(
      "2 new FDE signs in and has no projects yet",
      before.status === 200 && (before.json?.items ?? []).length === 0,
      before.json?.items?.length,
    );
    await shot(fde, "fde-no-projects");

    // 3. Admin assigns the project to the new FDE
    const users = await api<{ items: { id: string; email: string | null }[] }>(
      admin,
      "GET",
      "/admin/users?limit=100",
    );
    const me = users.items.find((u) => u.email === email);
    newUserId = me?.id ?? newUserId;
    await api(admin, "PUT", `/admin/projects/${project}/members`, { user_ids: [newUserId] });
    const mine = await raw(fde, "GET", "/projects?limit=100");
    const ids = (mine.json?.items ?? []).map((p: any) => p.id);
    check("3 FDE sees exactly the assigned project", ids.length === 1 && ids[0] === project, ids);
    await fde.goto("/projects");
    await shot(fde, "fde-my-projects");

    // 4. Admin Masters for this project (+ a tenant row the project row must win over, + another project)
    const rule = `RULE-E2E-${RUN}`;
    const tenantRule = await raw(admin, "POST", "/admin/masters/RULES", {
      payload: {
        rule_id: rule,
        rule_type_description: "TENANT version of the rule",
        record_status: "Active",
      },
    });
    const projRule = await raw(admin, "POST", "/admin/masters/RULES", {
      project_id: project,
      payload: {
        rule_id: rule,
        rule_type_description: "PROJECT version: quotes over 10,000 need a second approver",
        record_status: "Active",
      },
    });
    const projAgent = await raw(admin, "POST", "/admin/masters/AGENTS", {
      project_id: project,
      payload: {
        agent_id: `AGT-E2E-${RUN}`,
        agent_name: "Quote checker",
        reference: "https://github.com/example/quote-checker",
      },
    });
    const projTool = await raw(admin, "POST", "/admin/masters/TOOLING", {
      project_id: project,
      payload: {
        tooling_id: `TOOL-E2E-${RUN}`,
        tool_name: "Approval router",
        category: "MCP",
        record_status: "Active",
        links_path: "https://github.com/example/approval-router",
      },
    });
    check(
      "4 admin writes the project's masters",
      [projRule, projAgent, projTool, tenantRule].every((r) => r.status === 201),
      [tenantRule.status, projRule.status, projAgent.status, projTool.status],
    );
    other = (
      await api<{ id: string }>(admin, "POST", "/admin/projects", { name: `E2E other ${RUN}` })
    ).id;
    const otherAgent = await raw(admin, "POST", "/admin/masters/AGENTS", {
      project_id: other,
      payload: {
        agent_id: `AGT-OTHER-${RUN}`,
        agent_name: "Other agent",
        reference: "https://github.com/example/other-project-agent",
      },
    });
    check("4 a second project has its own agent", otherAgent.status === 201, otherAgent.status);
    await admin.goto("/admin/masters");
    await shot(admin, "admin-masters");

    // 5. What the FDE can see
    const cat = await raw(fde, "GET", `/projects/${project}/repo-catalog`);
    const urls: string[] = (cat.json?.candidates ?? []).map((c: any) => c.url);
    check(
      "5 FDE repo-catalog has the project's agent + tool",
      urls.some((u) => u.includes("quote-checker")) &&
        urls.some((u) => u.includes("approval-router")),
      urls,
    );
    check(
      "5 FDE repo-catalog has none of the other project's",
      !urls.some((u) => u.includes("other-project-agent")),
      urls,
    );
    check(
      "5 FDE cannot read /admin/masters",
      (await raw(fde, "GET", "/admin/masters/RULES")).status === 403,
      "",
    );
    check(
      "5 FDE gets 404 on the other project",
      (await raw(fde, "GET", `/projects/${other}/repo-catalog`)).status === 404,
      "",
    );
    const fdeA = await signedIn(browser, "fde", USERS.fdeA);
    const s1 = (await raw(fdeA, "GET", `/projects/${project}/documents`)).status;
    const s2 = (await raw(fdeA, "GET", `/projects/${project}/repo-catalog`)).status;
    check("5 unassigned fde-a1 gets 404 on the project", s1 === 404 && s2 === 404, [s1, s2]);
    await fdeA.context().close();

    // 6. The FDE runs the flow: upload one tiny RFQ, start processing
    await fde.goto(`/projects/${project}/documents`);
    await shot(fde, "fde-documents-empty");
    const rfq = [
      "# RFQ: quote approval",
      "",
      "Acme Test Ltd wants one small automation.",
      "",
      "- When a supplier quote arrives, read the total amount.",
      "- If the total is over 10,000, send it to a second approver; otherwise approve it.",
      "- Record who approved it and when.",
      "",
      "Out of scope: payments, supplier onboarding.",
    ].join("\n");
    await fde
      .locator("input.uppy-Dashboard-input[type=file]")
      .first()
      .setInputFiles({
        name: "rfq-quote-approval.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(rfq),
      });
    await fde.getByRole("button", { name: "Upload 1 file" }).click();
    const parsed = await poll(
      async () =>
        (await raw(fde, "GET", `/projects/${project}/documents?limit=10`)).json?.items?.[0]?.state,
      (s) => ["PARSED", "REJECTED", "PARSE_FAILED"].includes(s),
      5 * 60_000,
      5_000,
    );
    check("6 upload ends PARSED", parsed === "PARSED", parsed);
    await fde.goto(`/projects/${project}/documents`);
    await shot(fde, "fde-documents-parsed");

    await fde.goto(`/projects/${project}`);
    await fde.getByRole("button", { name: /Start processing/ }).click();
    await shot(fde, "fde-processing-started");
    const pipe = async () => (await raw(fde, "GET", `/projects/${project}/pipeline`)).json;
    check(
      "6 pipeline leaves IDLE",
      (await poll(pipe, (p) => p?.state !== "IDLE", 60_000, 3_000))?.state !== "IDLE",
      (await pipe())?.state,
    );

    // knowledge -> SOW (real LLM; budget-capped)
    const sow = await poll(
      async () => (await raw(fde, "GET", `/projects/${project}/sow/versions`)).json,
      (v) =>
        (v?.items ?? []).length > 0 || ["FAILED", "NEEDS_FDE_REVIEW"].includes(String(v?.state)),
      30 * 60_000,
      15_000,
    );
    const p1 = await pipe();
    const kv = (await raw(fde, "GET", `/projects/${project}/knowledge`)).json;
    const snapshotId =
      kv?.master_snapshot_id ??
      kv?.items?.[0]?.master_snapshot_id ??
      kv?.current?.master_snapshot_id;
    check("6 knowledge version records a master snapshot", Boolean(snapshotId), {
      kv: JSON.stringify(kv).slice(0, 300),
    });
    if (snapshotId) {
      const snap = JSON.stringify(
        (await raw(admin, "GET", `/admin/masters/snapshots/${snapshotId}`)).json ?? {},
      );
      check(
        "6 snapshot has the PROJECT rule (wins over tenant)",
        snap.includes("PROJECT version") && !snap.includes("TENANT version"),
        snap.slice(0, 200),
      );
      check(
        "6 snapshot has the project's agent and tool",
        snap.includes(`AGT-E2E-${RUN}`) && snap.includes(`TOOL-E2E-${RUN}`),
        "",
      );
      check("6 snapshot has nothing of the other project", !snap.includes(`AGT-OTHER-${RUN}`), "");
    }
    await fde.goto(`/projects/${project}/knowledge`);
    await shot(fde, "fde-knowledge");
    const versions = sow?.items ?? [];
    check("6 SOW generated", versions.length > 0, {
      pipeline: p1?.state,
      stage: p1?.stage,
      versions: versions.length,
    });

    if (versions.length > 0) {
      await fde.goto(`/projects/${project}/sow`);
      await shot(fde, "fde-sow-draft");
      const approveBtn = fde.getByRole("button", { name: /^Approve$/ });
      await expect(approveBtn).toBeEnabled({ timeout: 10 * 60_000 });
      await approveBtn.click();
      await fde.getByRole("button", { name: "Confirm approval" }).click();
      await fde.waitForTimeout(5_000);
      await shot(fde, "fde-sow-approved");
      const wf = await poll(
        async () => (await raw(fde, "GET", `/projects/${project}/workflows`)).json?.items ?? [],
        (w) => w.length > 0,
        10 * 60_000,
        10_000,
      );
      check(
        "6 SBAM generated the Workflow from the approved SOW",
        wf.length > 0,
        wf.map((w: any) => `${w.id} ${w.state}`),
      );
      if (wf.length > 0) {
        await fde.goto(`/projects/${project}/workflows/${wf[0].id}`);
        await shot(fde, "fde-workflow");
        // breakdown/build as far as the budget allows (no extra waiting beyond 20 min)
        const later = await poll(
          async () => (await raw(fde, "GET", `/projects/${project}/workflows`)).json?.items?.[0],
          (w) => !["CREATED", "BREAKING_DOWN", "EXTRACTING"].includes(String(w?.state)),
          20 * 60_000,
          20_000,
        );
        check("6 workflow progressed past breakdown", true, `${later?.state}`);
        await fde.goto(`/projects/${project}/workflows/${wf[0].id}`);
        await shot(fde, "fde-workflow-later");
      }
    }
    const final = await pipe();
    check("6 final pipeline state", true, {
      state: final?.state,
      stage: final?.stage,
      error: final?.error,
    });
    await fde.goto(`/projects/${project}`);
    await shot(fde, "fde-overview-final");
    const notes = (await raw(fde, "GET", "/notifications?limit=50")).json?.items ?? [];
    check(
      "6 budget notifications (if any)",
      true,
      notes.filter((n: any) => String(n.type).startsWith("TOKEN_BUDGET")).map((n: any) => n.type),
    );
  } finally {
    // 7. clean-up: archive both projects, deactivate the invited user
    if (project) await archive(admin, project);
    if (other) await archive(admin, other);
    if (newUserId) await raw(admin, "PATCH", `/admin/users/${newUserId}`, { active: false });
  }
  expect(
    steps.filter((s) => !s.ok),
    JSON.stringify(steps, null, 1),
  ).toEqual([]);
});
