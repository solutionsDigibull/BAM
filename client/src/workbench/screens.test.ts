import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import { SUBTASKS, TASKS, subtasksOf } from "./tracker/trackerConfig";

// Workbench.tsx maps a screen id to the component that renders it (PANES, SINGLE, VIEWS). A screen in the rail
// without one would crash the page, as 1.9 (Parse) did once. The maps hold components, so the check reads the
// source rather than importing the whole workbench. Only these three maps count: a footer (FOOT) is not a screen.
const source = readFileSync(join(process.cwd(), "src", "workbench", "Workbench.tsx"), "utf8");

function block(name: string): string {
  const start = source.indexOf(`const ${name}`);
  if (start < 0) return "";
  const end = source.indexOf("\n}\n", start);
  return source.slice(start, end < 0 ? undefined : end);
}

const renderers = ["PANES", "SINGLE", "VIEWS"].map(block).join("\n");
const mapped = (id: string) => new RegExp(`['"]${id.replace(".", "\\.")}['"]\\s*:`).test(renderers);

describe("every screen the rail offers has a renderer (#711)", () => {
  test("test_711_the_renderer_maps_were_found", () => {
    expect(["PANES", "SINGLE", "VIEWS"].every((n) => block(n).length > 0)).toBe(true);
  });

  test("test_711_each_visible_screen_is_mapped_to_a_component", () => {
    const missing = TASKS.flatMap((t) => subtasksOf(t.id))
      .filter((s) => !mapped(s.id))
      .map((s) => s.id);
    expect(missing).toEqual([]);
  });

  test("test_711_the_screens_without_a_backend_stay_hidden", () => {
    const hidden = SUBTASKS.filter((s) => s.hidden).map((s) => s.id);
    for (const id of ["2.2", "2.3", "4.5", "4.6"]) expect(hidden).toContain(id);
  });
});
