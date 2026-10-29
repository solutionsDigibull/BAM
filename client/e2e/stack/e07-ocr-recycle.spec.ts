import { expect, test } from "@playwright/test";

import {
  api,
  archive,
  documentRow,
  fixture,
  guard,
  newProject,
  settled,
  signedIn,
  uploadDocuments,
  USERS,
  type Doc,
} from "./stack";

// E07-E08 (E15.1-T07): a scanned PDF is read by OCR (its pages are in the parse report), and a
// deleted document goes to the recycle bin and comes back on Restore.
const SCANNED = fixture("documents/scanned_full.pdf", "application/pdf");

test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t07 a scanned PDF is OCR'd; delete then restore brings it back", async ({
  browser,
}) => {
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "E07 OCR and recycle", USERS.fdeA);
  try {
    await uploadDocuments(fde, project, [SCANNED]);
    const docs = await settled(fde, project, [SCANNED.name]);
    const scanned = docs[SCANNED.name]!;
    expect(scanned.state).toBe("PARSED");
    expect(scanned.parse_report?.ocr_pages?.length ?? 0).toBeGreaterThan(0); // OCR text exists

    // E08: delete from the table, find it in the recycle bin, restore it
    await fde.reload();
    await fde.getByRole("button", { name: `Delete ${SCANNED.name}` }).click();
    await fde.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(documentRow(fde, SCANNED.name)).toHaveCount(0);
    await fde.getByRole("tab", { name: /Recycle bin \(1\)/ }).click();
    await fde.getByRole("button", { name: `Restore ${SCANNED.name}` }).click();
    await expect
      .poll(async () => {
        const all = await api<{ items: Doc[] }>(fde, "GET", `/projects/${project}/documents`);
        const doc = all.items.find((d) => d.id === scanned.id);
        return doc ? [doc.state === "DELETED", doc.deleted_at ?? null] : null;
      })
      .toEqual([false, null]); // back, and scanned and parsed again (it restarts at UPLOADED)
    await fde.getByRole("tab", { name: /^Documents/ }).click();
    await expect(documentRow(fde, SCANNED.name)).toBeVisible();
  } finally {
    await archive(admin, project);
  }
});
