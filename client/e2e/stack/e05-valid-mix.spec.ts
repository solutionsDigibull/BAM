import { expect, test } from "@playwright/test";

import {
  archive,
  documentRow,
  fixture,
  guard,
  newProject,
  settled,
  signedIn,
  uploadDocuments,
  USERS,
} from "./stack";

// E05 (E15.1-T05): the FDE uploads one file of each everyday type; every one is scanned, typed and
// parsed (PARSED), and the documents table shows it.
const MIX = [
  fixture("documents/text.pdf", "application/pdf"),
  fixture(
    "documents/sample.docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ),
  fixture(
    "documents/multi_sheet.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ),
  fixture("documents/sample.csv", "text/csv"),
  fixture("documents/sample.md", "text/markdown"),
  fixture("documents/sample.txt", "text/plain"),
  fixture("documents/with_attachment.eml", "message/rfc822"),
];

test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t05 PDF, DOCX, XLSX, CSV, MD, TXT and EML all end PARSED", async ({ browser }) => {
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "E05 valid mix", USERS.fdeA);
  try {
    await uploadDocuments(fde, project, MIX);
    const docs = await settled(
      fde,
      project,
      MIX.map((f) => f.name),
    );
    expect(Object.fromEntries(MIX.map((f) => [f.name, docs[f.name]?.state]))).toEqual(
      Object.fromEntries(MIX.map((f) => [f.name, "PARSED"])),
    );
    await fde.reload();
    for (const f of MIX) await expect(documentRow(fde, f.name)).toContainText("Parse: Parsed");
  } finally {
    await archive(admin, project);
  }
});
