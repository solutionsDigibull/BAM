import { expect, test, type Page, type Route } from "@playwright/test";

// #756: when no repository is named and the Admin catalogue has no clear winner, the build pauses and the FDE-mode
// workbench shows a question card in Translate, above the read-only code views. The API is faked at the proxy (no
// sbam-api here). Set UI_SHOTS_DIR to save a screenshot of each state.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const API = "**/api/proxy/api/v1";
const A = "https://github.com/acme/stripe-webhooks";
const B = "https://github.com/acme/payments-kit";
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const problem = (route: Route, status: number, title: string, detail?: string) =>
  json(route, { type: "about:blank", title, status, detail }, status);

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "Payments onboarding",
  client_name: "Illustrative client",
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "WORKFLOW_CREATED",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};
const workflow = {
  id: WORKFLOW,
  project_id: PROJECT,
  sow_version_id: "v1",
  name: "Onboarding",
  version: "1.0.0",
  state: "BUILDING",
  created_at: "2026-11-10T10:00:00Z",
};
const question = (kind: string, candidates: unknown[]) => ({
  sub_skill_name: "Verify payment",
  build_no: 1,
  question: { sub_skill_id: SUB, kind, candidates, asked_at: "2026-11-18T09:00:00Z" },
});
const TIE = question("CLOSE_CANDIDATES", [
  { url: A, source: "ADMIN_AGENTS", score: 0.86, reasons: ["notes name the Stripe webhooks"] },
  { url: B, source: "ADMIN_TOOLING", score: 0.82, reasons: ["same payment domain"] },
]);
const NONE = question("NO_CANDIDATE", []);
const DATA = (questions: unknown[]) => ({ workflow_id: WORKFLOW, questions, decisions: [] });

async function open(
  page: Page,
  baseURL: string,
  questions: (route: Route) => unknown,
  view?: "Repositories",
) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  // Calls no test fakes answer 404 (with no sbam-api the proxy would answer 401 and sign the user out, #742).
  await page.route(`${API}/**`, (r) => problem(r, 404, "Not Found"));
  await page.route(
    (u) => u.pathname.endsWith("/api/proxy/api/v1/projects"),
    (r) => json(r, { items: [project] }),
  );
  await page.route(`${API}/projects/${PROJECT}/documents**`, (r) =>
    json(r, { items: [], next_cursor: null }),
  );
  await page.route(`${API}/projects/${PROJECT}/pipeline`, (r) =>
    json(r, { state: "WORKFLOW_CREATED", steps: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) => json(r, { versions: [] }));
  await page.route(`${API}/projects/${PROJECT}/workflows`, (r) => json(r, { items: [workflow] }));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) =>
    json(r, { workflow_id: WORKFLOW, groups: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/repositories`, (r) =>
    json(r, { workflow_id: WORKFLOW, repositories: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/repo-questions`, (r) =>
    questions(r),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  if (view) await page.getByRole("button", { name: view }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e756_tie the card shows the short list with scores and reasons above the code view, and the build is paused", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, DATA([TIE])));
  const card = page.getByRole("region", { name: "Repository for Verify payment" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Several repositories fit about equally well");
  await expect(card).toContainText("The build is paused until you answer");
  await expect(card).toContainText("86% match");
  await expect(card).toContainText("notes name the Stripe webhooks");
  await expect(card.getByRole("button", { name: "Use acme/stripe-webhooks" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Workflow files" })).toBeVisible(); // the code views stay
  await shot(page, "1-tie");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "1b-tie-wide");
});

test("e756_answer choosing a repository sends the choice with an Idempotency-Key and says so", async ({
  page,
  baseURL,
}) => {
  const sent: { body: unknown; key: string | undefined }[] = [];
  await open(page, baseURL!, (r) => json(r, DATA([TIE])));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/repo-answer`,
    async (route) => {
      sent.push({
        body: route.request().postDataJSON(),
        key: route.request().headers()["idempotency-key"],
      });
      await json(route, { sub_skill_id: SUB, build_no: 1 }, 202);
    },
  );
  await page.getByRole("button", { name: "Use acme/payments-kit" }).click();
  await expect(page.getByText("Answer sent. The build carries on")).toBeVisible();
  expect(sent).toHaveLength(1);
  expect(sent[0]!.body).toEqual({ action: "CHOOSE", url: B });
  expect(sent[0]!.key).toMatch(/^[0-9a-f-]{36}$/);
  await shot(page, "2-answered");
});

test("e756_no_repo none of these generates the code", async ({ page, baseURL }) => {
  const bodies: unknown[] = [];
  await open(page, baseURL!, (r) => json(r, DATA([TIE])));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/repo-answer`,
    async (route) => {
      bodies.push(route.request().postDataJSON());
      await json(route, { sub_skill_id: SUB, build_no: 1 }, 202);
    },
  );
  await page.getByRole("button", { name: "None of these: generate the code" }).click();
  await expect(page.getByText("Answer sent.")).toBeVisible();
  expect(bodies).toEqual([{ action: "NO_REPO" }]);
});

test("e756_no_candidate the card asks whether the FDE has a repository and takes one of their own", async ({
  page,
  baseURL,
}) => {
  const bodies: unknown[] = [];
  await open(page, baseURL!, (r) => json(r, DATA([NONE])));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/repo-answer`,
    async (route) => {
      bodies.push(route.request().postDataJSON());
      await json(route, { sub_skill_id: SUB, build_no: 1 }, 202);
    },
  );
  const card = page.getByRole("region", { name: "Repository for Verify payment" });
  await expect(card).toContainText("The catalogue has no repository for this Sub-Skill");
  await expect(
    card.getByRole("button", { name: "I have no repository: generate the code" }),
  ).toBeVisible();
  const submit = card.getByRole("button", { name: "Use this repository" });
  await expect(submit).toBeDisabled();
  await card.getByLabel("Your repository (https address)").fill("https://github.com/me/mine");
  await shot(page, "3-no-candidate");
  await submit.click();
  await expect(page.getByText("Answer sent.")).toBeVisible();
  expect(bodies).toEqual([{ action: "CHOOSE", url: "https://github.com/me/mine" }]);
});

test("e756_refused a refused answer shows the server's reason and the FDE can try again", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, DATA([TIE])));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/repo-answer`,
    (route) =>
      json(
        route,
        {
          type: "about:blank",
          title: "Invalid answer",
          status: 422,
          errors: [{ path: "url", message: "Choose one of the repositories on the short list." }],
        },
        422,
      ),
  );
  await page.getByRole("button", { name: "Use acme/stripe-webhooks" }).click();
  await expect(page.getByText("Choose one of the repositories on the short list.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Use acme/stripe-webhooks" })).toBeEnabled();
  await shot(page, "4-refused");
});

test("e756_empty with nothing waiting the Repositories view says so and the code views show no card", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, DATA([])), "Repositories");
  await expect(page.getByText("Nothing to decide")).toBeVisible();
  await shot(page, "5-empty");
  await page.getByRole("button", { name: "Workflow files" }).click();
  await expect(page.getByText("Nothing to decide")).toHaveCount(0);
  await expect(page.locator(".rq")).toHaveCount(0);
});

test("e756_error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true; // stays down (also for the browser's own refetches) until Retry is clicked
  await open(
    page,
    baseURL!,
    (r) =>
      failing
        ? problem(r, 500, "Server error", "The audit trail is unavailable")
        : json(r, DATA([TIE])),
    "Repositories",
  );
  await expect(page.getByText("The audit trail is unavailable")).toBeVisible();
  await shot(page, "6-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("region", { name: "Repository for Verify payment" })).toBeVisible();
});

test("e756_forbidden an unassigned FDE or another tenant gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 404, "Not Found"), "Repositories");
  await expect(page.getByText("You can't answer for this workflow")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "7-forbidden");
});

test("e756_loading the Repositories view shows a skeleton until the question arrives", async ({
  page,
  baseURL,
}) => {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await open(
    page,
    baseURL!,
    async (r) => {
      await gate;
      await json(r, DATA([]));
    },
    "Repositories",
  );
  await expect(page.getByLabel("Loading the repository question")).toBeVisible();
  await shot(page, "8-loading");
  release();
  await expect(page.getByText("Nothing to decide")).toBeVisible();
});
