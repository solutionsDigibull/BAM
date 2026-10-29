import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import {
  api,
  archive,
  fixture,
  guard,
  newProject,
  settled,
  signedIn,
  uploadDocuments,
  USERS,
} from "./stack";

// E09-E10 (E15.1-T08): parsed documents become a READY knowledge version with every document
// covered (100%), then the pipeline writes the SOW: schema-valid (every required section), cited,
// within 5 minutes. The knowledge explorer and the SOW screen show them.
const SOW_SCHEMA = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../server/contracts/schemas/sow/sow_document.schema.json",
);
const DOCS = [
  fixture("documents/sample.md", "text/markdown"),
  fixture("documents/sample.txt", "text/plain"),
  fixture("documents/sample.csv", "text/csv"),
];
const COVERED = new Set(["COVERED", "CHUNKS_ONLY", "ADDED"]);
const SOW_WITHIN = 5 * 60_000; // E10: a SOW within 5 minutes of the knowledge being ready

type Knowledge = {
  versions: {
    id: string;
    status: string;
    documents: { title: string; status: string; reason?: string | null }[];
  }[];
};
type Sow = { current?: { version: number; status: string } | null };
type SowVersion = {
  status: string;
  structured: Record<string, unknown> | null;
  citations: { okf_ref_id: string }[];
};

test.beforeAll(({ baseURL }) => guard(baseURL));

test("e15_1_t08 knowledge is READY with full coverage; the SOW is cited and schema-valid in 5 min", async ({
  browser,
}) => {
  test.setTimeout(20 * 60_000);
  const fde = await signedIn(browser, "fde", USERS.fdeA);
  const admin = await signedIn(browser, "admin", USERS.adminA);
  const project = await newProject(admin, "E09 knowledge and SOW", USERS.fdeA);
  try {
    await uploadDocuments(fde, project, DOCS);
    await settled(
      fde,
      project,
      DOCS.map((d) => d.name),
    );
    await fde.goto(`/projects/${project}`);
    await fde.getByRole("button", { name: "Start processing" }).click();

    // E09: the knowledge build ends READY, and every uploaded document is in it
    await expect
      .poll(
        async () => {
          const k = await api<Knowledge>(fde, "GET", `/projects/${project}/knowledge`);
          return k.versions[0]?.status ?? "NONE";
        },
        { timeout: 10 * 60_000, intervals: [5_000, 10_000] },
      )
      .toBe("READY");
    const knowledge = await api<Knowledge>(fde, "GET", `/projects/${project}/knowledge`);
    const rows = knowledge.versions[0]!.documents;
    for (const d of DOCS) {
      const row = rows.find((r) => r.title === d.name);
      expect(row, `${d.name} is in the knowledge version`).toBeDefined();
      expect(COVERED.has(row!.status), `${d.name}: ${row!.status} ${row!.reason ?? ""}`).toBe(true);
    }
    await fde.goto(`/projects/${project}/knowledge`);
    await expect(fde.getByTestId("knowledge-canvas")).toBeVisible({ timeout: 60_000 });

    // E10: the pipeline writes the SOW (one-shot generation) and waits for review
    await expect
      .poll(
        async () =>
          (await api<{ state: string }>(fde, "GET", `/projects/${project}/pipeline`)).state,
        { timeout: SOW_WITHIN, intervals: [5_000, 10_000] },
      )
      .toBe("SOW_REVIEW");
    const sow = await api<Sow>(fde, "GET", `/projects/${project}/sow`);
    const n = sow.current!.version;
    const version = await api<SowVersion>(fde, "GET", `/projects/${project}/sow/versions/${n}`);
    const schema = JSON.parse(readFileSync(SOW_SCHEMA, "utf-8")) as { required?: string[] };
    expect(version.structured).not.toBeNull();
    for (const section of schema.required ?? [])
      expect(version.structured, `section ${section}`).toHaveProperty(section);
    expect(version.citations.length).toBeGreaterThan(0);
    const ref = encodeURIComponent(version.citations[0]!.okf_ref_id);
    await api(fde, "GET", `/projects/${project}/sow/versions/${n}/citations/${ref}`);

    await fde.goto(`/projects/${project}/sow`);
    await expect(fde.getByTestId("sow-screen")).toBeVisible({ timeout: 60_000 });
    await expect(fde.getByRole("heading", { name: `SOW v${n}` })).toBeVisible();
  } finally {
    await archive(admin, project);
  }
});
