import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import type { Snapshot } from "./api/types";
import { prerequisite } from "./lib/prereq";
import { SUBTASKS } from "./tracker/trackerConfig";

const root = join(process.cwd(), "src", "workbench");
const sources = (dir: string) =>
  readdirSync(join(root, dir))
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f))
    .map((f) => ({ file: `${dir}/${f}`, text: readFileSync(join(root, dir, f), "utf8") }));

// Screens with no backend are hidden in the tracker, so no screen may send the FDE into one. Step 4's footers
// still point at Certify and Commit until Step 4 is wired (#715): the one known exception.
const HIDDEN = ["2.2", "2.3", "4.5", "4.6"];
const KNOWN = new Set(["steps/Step4.tsx"]);

describe("the workbench flow never leads into a hidden screen (#711)", () => {
  test("test_711_the_hidden_screens_are_the_ones_without_a_backend", () => {
    const hidden = SUBTASKS.filter((s) => s.hidden).map((s) => s.id);
    for (const id of HIDDEN) expect(hidden).toContain(id);
  });

  test("test_711_no_screen_navigates_into_a_hidden_screen", () => {
    const offenders: string[] = [];
    for (const { file, text } of ["steps", "lib", "rag", "components"].flatMap(sources)) {
      if (KNOWN.has(file)) continue;
      for (const id of HIDDEN) {
        if (text.includes(`goScreen('${id}'`) || text.includes(`goScreen("${id}"`))
          offenders.push(`${file} -> ${id}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test("test_711_uploaded_documents_not_yet_started_point_to_standardize", () => {
    const snap = { uploads: [{ id: "u" }], ingestion: undefined, sow: undefined } as unknown as Snapshot;
    const blocker = prerequisite(snap, { runId: undefined, steps: [] }, 1);
    expect(blocker?.target).toBe("1.8");
    expect(blocker?.reason).toMatch(/Start it in Standardize/);
  });

  test("test_711_ingestion_runs_upload_standardize_parse_review_in_that_order", () => {
    const visible = SUBTASKS.filter((s) => s.parentId === "1" && !s.hidden).map((s) => s.id);
    expect(visible).toEqual(["1.1", "1.8", "1.9", "1.4"]);
  });

  test("test_711_each_ingestion_footer_moves_on_to_the_next_screen_and_only_standardize_starts_the_run", () => {
    const ingestion = readFileSync(join(root, "steps", "Ingestion.tsx"), "utf8");
    const standardize = readFileSync(join(root, "steps", "Standardize.tsx"), "utf8");
    expect(ingestion).toContain("goScreen('1.8')"); // Upload: Continue
    expect(ingestion).toContain("goScreen('1.4')"); // Parse, when the extraction is done
    expect(ingestion).toContain("goScreen('2.5')"); // Review: Continue to Context
    expect(standardize).toContain("start(ok.map"); // Standardize: Start ingestion
    expect(ingestion).not.toMatch(/useIngestStart\(snap\)/); // Upload no longer starts the run
  });
});
