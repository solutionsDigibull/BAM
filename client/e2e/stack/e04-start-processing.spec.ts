import { expect, test } from "@playwright/test";

import { api, archive, guard, newProject, signedIn, uploadDocuments, USERS } from "./stack";

// E04 (E15.1-T04): the FDE opens their project, uploads a document and starts processing. The
// pipeline runs, and no Workflow exists (or can be created) before the SOW is approved.
test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t04 start processing runs the pipeline; no Workflow before SOW approval", async ({
  browser,
}) => {
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "E04 start processing", USERS.fdeA);
  try {
    await uploadDocuments(fde, project, [
      {
        name: "e04-notes.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("RFQ notes for E04.\n"),
      },
    ]);
    await fde.goto(`/projects/${project}`);
    await fde.getByRole("button", { name: "Start processing" }).click();
    await expect(fde.getByText("Processing is running.")).toBeVisible();
    await expect
      .poll(
        async () =>
          (await api<{ state: string }>(fde, "GET", `/projects/${project}/pipeline`)).state,
      )
      .not.toBe("IDLE");

    const workflows = await api<{ items: unknown[] }>(fde, "GET", `/projects/${project}/workflows`);
    expect(workflows.items).toEqual([]);
    await expect(fde.getByRole("button", { name: /create|new workflow/i })).toHaveCount(0);
    await expect(fde.getByRole("link", { name: /create|new workflow/i })).toHaveCount(0);
  } finally {
    await archive(admin, project);
  }
});
