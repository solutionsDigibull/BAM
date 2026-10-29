import { expect, test } from "@playwright/test";

import {
  api,
  archive,
  documentRow,
  eicar,
  guard,
  newProject,
  rejections,
  settled,
  signedIn,
  uploadDocuments,
  USERS,
} from "./stack";

// E06 (E15.1-T06): EICAR, a file one byte over 15 MB and a 21st file are each refused with their
// reason: malware (the scan quarantines it), 413 FILE_TOO_LARGE, 409 FILE_LIMIT_REACHED.
const OVER = 15 * 1024 * 1024 + 1;

test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t06 EICAR is rejected as malware; 15 MB + 1 is 413; the 21st file is 409", async ({
  browser,
}) => {
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "E06 rejects", USERS.fdeA);
  try {
    // malware: accepted for upload, then the scan rejects and quarantines it
    await uploadDocuments(fde, project, [
      { name: "e06-eicar.txt", mimeType: "text/plain", buffer: eicar() },
    ]);
    const docs = await settled(fde, project, ["e06-eicar.txt"]);
    expect([docs["e06-eicar.txt"]?.state, docs["e06-eicar.txt"]?.reject_code]).toEqual([
      "REJECTED",
      "MALWARE",
    ]);
    await fde.reload();
    await expect(documentRow(fde, "e06-eicar.txt")).toContainText("Scan: Quarantined");

    // 15 MB + 1 byte: the page refuses it with its reason, and so does the API (413)
    await fde
      .locator("input.uppy-Dashboard-input[type=file]")
      .first()
      .setInputFiles({
        name: "e06-too-big.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.alloc(OVER, "a"),
      });
    await expect(rejections(fde)).toContainText("e06-too-big.pdf");
    await expect(rejections(fde)).toContainText("Larger than");
    const big = await api<{ code: string }>(
      fde,
      "POST",
      `/projects/${project}/uploads`,
      { files: [{ name: "e06-too-big.pdf", size: OVER, mime: "application/pdf" }] },
      [413],
    );
    expect(big.code).toBe("FILE_TOO_LARGE");

    // fill the project to 20 active files (a REJECTED file does not count), then the 21st is 409
    const filler = Array.from({ length: 20 }, (_, i) => ({
      name: `e06-${String(i + 1).padStart(2, "0")}.txt`,
      mimeType: "text/plain",
      buffer: Buffer.from(`filler ${i + 1}\n`),
    }));
    await uploadDocuments(fde, project, filler);
    // a fresh dashboard (Uppy's finished one takes no new files), once the 20 documents are listed:
    // the page counts them before it lets a file in
    await fde.reload();
    await expect(documentRow(fde, "e06-20.txt")).toBeVisible();
    await fde
      .locator("input.uppy-Dashboard-input[type=file]")
      .first()
      .setInputFiles({
        name: "e06-21st.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("one too many\n"),
      });
    await expect(rejections(fde)).toContainText("e06-21st.txt");
    await expect(rejections(fde)).toContainText("This project already has 20 files");
    const limit = await api<{ code: string }>(
      fde,
      "POST",
      `/projects/${project}/uploads`,
      { files: [{ name: "e06-21st.txt", size: 13, mime: "text/plain" }] },
      [409],
    );
    expect(limit.code).toBe("FILE_LIMIT_REACHED");
  } finally {
    await archive(admin, project);
  }
});
