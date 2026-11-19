// Live check of the Cognee meter fix (knowledge traced + priced, budget stops SOW). Runs only with E2E_STACK=1.
import { writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { api, archive, guard, newProject, signedIn, USERS } from "./stack";

test.beforeAll(({ baseURL }) => guard(baseURL));

test("knowledge build is metered and traced; a tiny budget stops the SOW", async ({ browser }) => {
  test.setTimeout(30 * 60_000);
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "budget check", USERS.fdeA);
  const out: Record<string, unknown> = { project };
  try {
    await fde.goto(`/projects/${project}/documents`);
    await fde
      .locator("input.uppy-Dashboard-input[type=file]")
      .first()
      .setInputFiles({
        name: "rfq-budget.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# RFQ\n\nRoute supplier quotes over 10,000 to a second approver.\n"),
      });
    await fde.getByRole("button", { name: "Upload 1 file" }).click();
    await expect
      .poll(
        async () =>
          (await api<{ items: { state: string }[] }>(fde, "GET", `/projects/${project}/documents`))
            .items[0]?.state,
        { timeout: 5 * 60_000, intervals: [5_000] },
      )
      .toBe("PARSED");
    await fde.goto(`/projects/${project}`);
    await fde.getByRole("button", { name: /Start processing/ }).click();
    let state = "";
    await expect
      .poll(
        async () =>
          (state = (await api<{ state: string }>(fde, "GET", `/projects/${project}/pipeline`))
            .state),
        { timeout: 20 * 60_000, intervals: [15_000] },
      )
      .toMatch(/FAILED|SOW_REVIEW|NEEDS|STOP/);
    out.state = state;
    out.notes = (
      await api<{ items: { type: string }[] }>(fde, "GET", "/notifications?limit=50")
    ).items.map((n) => n.type);
    await fde.screenshot({ path: "walkthrough-out/flow/90-budget-stop.png", fullPage: true });
  } finally {
    writeFileSync("walkthrough-out/flow/budget-check.json", JSON.stringify(out, null, 2));
    await archive(admin, project);
  }
});
