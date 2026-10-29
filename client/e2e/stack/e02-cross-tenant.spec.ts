import { expect, test } from "@playwright/test";

import { api, archive, guard, newProject, signedIn, USERS } from "./stack";

// E02 (E15.1-T02): an FDE of tenant B opens a tenant A project URL. The page is a 404 (no existence
// leak) and tenant B's audit log records the refused access.
test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t02 a tenant B FDE gets 404 for a tenant A project, and it is audited", async ({
  browser,
}) => {
  const fdeA = await signedIn(browser, "fde", USERS.fdeA);
  const adminA = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(adminA, "E02 cross-tenant", USERS.fdeA);
  try {
    await fdeA.goto(`/projects/${project}`); // the owner sees it
    await expect(fdeA.getByRole("heading", { level: 4 })).toBeVisible();

    const fdeB = await signedIn(browser, "fde", USERS.fdeB);
    await fdeB.goto(`/projects/${project}`);
    // the overview's queries retry before giving up on a 404, so allow for their backoff
    await expect(fdeB.getByText("Not found").first()).toBeVisible({ timeout: 60_000 });
    await expect(fdeB.getByText(/E02 cross-tenant/)).toHaveCount(0);

    const adminB = await signedIn(browser, "admin", USERS.adminB);
    await expect
      .poll(async () => {
        const page = await api<{ items: { action: string }[] }>(
          adminB,
          "GET",
          `/admin/audit?entity_type=route&entity_id=${project}`,
        );
        return page.items.map((e) => e.action);
      })
      .toContain("access_denied");
  } finally {
    await archive(adminA, project);
  }
});
