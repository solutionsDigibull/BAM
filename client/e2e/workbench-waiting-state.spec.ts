import { expect, test, type Page, type Route } from "@playwright/test";

// WG-43 (#931): a build parked on the FDE's repository answer says "Waiting for your answer", not Building: which question,
// since when, the reminder, and a button to the question card. The API is faked at the proxy (no sbam-api here). A screenshot
// of each state is saved to UI_SHOTS_DIR (the review run used C:\Users\Admin\Downloads\BAM-staging\931).
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const API = "**/api/proxy/api/v1";
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const problem = (route: Route, status: number, title: string, detail?: string) =>
  json(route, { type: "about:blank", title, status, detail }, status);

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
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

const SINCE = "2026-11-17T09:00:00Z";
const waiting = (reminders: number) => ({
  kind: "REPO_QUESTION",
  since: SINCE,
  question_kind: "CLOSE_CANDIDATES",
  reminders_sent: reminders,
  next_reminder_at: "2026-11-23T09:00:00Z",
  link: `/api/v1/projects/${PROJECT}/workflows/${WORKFLOW}/repo-questions`,
});
const row = (over: Record<string, unknown>) => ({
  sub_skill_id: SUB,
  skill_id: "MS01.1_Intake",
  skill_name: "Intake the RFQ",
  state: "BUILDING",
  stage: "generate_bodies",
  attempt: 0,
  gates: { ISATVON_CONTRACT: "PASSED", LIBRARIES: "PASSED" },
  ...over,
});
const builds = (r: Record<string, unknown>) => ({
  workflow_id: WORKFLOW,
  workflow_state: "BUILDING",
  active: true,
  sub_skills: [r],
  workflow_gates: {},
});
const WAITING = (reminders = 0) =>
  builds(row({ stage: "wait_repo_choice", waiting_for: waiting(reminders) }));
const QUESTIONS = {
  workflow_id: WORKFLOW,
  decisions: [],
  questions: [
    {
      sub_skill_name: "Intake the RFQ",
      build_no: 1,
      question: {
        sub_skill_id: SUB,
        kind: "CLOSE_CANDIDATES",
        asked_at: SINCE,
        candidates: [
          {
            url: "https://github.com/acme/rfq-intake",
            source: "ADMIN_AGENTS",
            score: 0.85,
            reasons: ["Parses RFQ emails"],
          },
          {
            url: "https://github.com/acme/quote-tools",
            source: "ADMIN_TOOLING",
            score: 0.82,
            reasons: ["Prices a bill of materials"],
          },
        ],
      },
    },
  ],
};

async function open(
  page: Page,
  baseURL: string,
  buildsFor: (route: Route) => unknown,
  view: "Builds" | "none" = "Builds",
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/builds`, (r) => buildsFor(r));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/repo-questions`, (r) =>
    json(r, QUESTIONS),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  if (view === "Builds") await page.getByRole("button", { name: "Builds" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e931_waiting the Builds view says Waiting for your answer with the question and since when, never Building", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, WAITING(0)));
  const list = page.getByRole("list", { name: "Sub-Skill builds" });
  await expect(list).toContainText("Waiting for your answer");
  await expect(list).not.toContainText("Building");
  const note = page.getByTestId(`waiting-${SUB}`);
  await expect(note).toContainText("which repository should it start from");
  await expect(note).toContainText("2026-11-17 09:00 UTC");
  await expect(page.getByRole("group", { name: "Build summary" })).toContainText(
    "1 waiting for your answer",
  );
  await shot(page, "01-waiting-builds-view");
});

test("e931_waiting the notice and the question card show on the code view and the button goes to the card", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, WAITING(0)), "none");
  await expect(page.getByTestId("waiting-notice")).toContainText(
    "Intake the RFQ is waiting for your answer",
  );
  const card = page.getByRole("region", { name: "Repository for Intake the RFQ" });
  await expect(card).toBeVisible();
  await shot(page, "02-waiting-notice-and-question-card");
  await page.getByRole("button", { name: "Answer the question for Intake the RFQ" }).click();
  await expect(card).toBeFocused();
  await shot(page, "03-waiting-button-went-to-the-card");
});

test("e931_reminder shows how many reminders were sent and when the next is due", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, WAITING(2)));
  const note = page.getByTestId(`waiting-${SUB}`);
  await expect(note).toContainText("2 reminders sent");
  await expect(note).toContainText("the next is due 2026-11-23 09:00 UTC");
  await shot(page, "04-reminder-shown");
});

test("e931_answered once the question is answered the build is Building again and the waiting state is gone", async ({
  page,
  baseURL,
}) => {
  let answered = false;
  await open(page, baseURL!, (r) => json(r, answered ? builds(row({})) : WAITING(0)));
  await expect(page.getByTestId(`waiting-${SUB}`)).toBeVisible();
  answered = true;
  await page.reload();
  await page.getByRole("button", { name: "Builds" }).click();
  const list = page.getByRole("list", { name: "Sub-Skill builds" });
  await expect(list).toContainText("Building");
  await expect(list).not.toContainText("Waiting for your answer");
  await expect(page.getByTestId(`waiting-${SUB}`)).toHaveCount(0);
  await shot(page, "05-answered-building-again");
});

test("e931_loading shows a skeleton until the builds arrive", async ({ page, baseURL }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, WAITING(0));
  });
  await expect(page.getByText("Loading the builds")).toBeAttached();
  await shot(page, "06-loading");
  release();
  await expect(page.getByTestId(`waiting-${SUB}`)).toBeVisible();
});

test("e931_empty a workflow with no Sub-Skills says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { ...WAITING(0), sub_skills: [] }));
  await expect(page.getByText("Nothing is building")).toBeVisible();
  await shot(page, "07-empty");
});

test("e931_error a server error shows its reason", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) =>
    problem(r, 500, "Server Error", "The builds could not be read."),
  );
  await expect(page.getByText("Could not load the builds")).toBeVisible();
  await expect(page.getByText("The builds could not be read.")).toBeVisible();
  await shot(page, "08-error");
});

test("e931_not_found a workflow that is not the FDEs is a 404 and shows the forbidden state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 404, "Not Found", "Workflow not found"));
  await expect(page.getByText("You can't see this workflow's builds")).toBeVisible();
  await shot(page, "09-not-found-404");
});

test("e931_forbidden a 403 shows the forbidden state", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden", "Not allowed"));
  await expect(page.getByText("You can't see this workflow's builds")).toBeVisible();
  await shot(page, "10-forbidden-403");
});
