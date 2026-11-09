import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import { SNAPSHOT_POLL_MS } from "./data";

describe("the snapshot poll does not flood the API (#711)", () => {
  test("test_711_the_snapshot_is_polled_no_faster_than_every_three_seconds", () => {
    expect(SNAPSHOT_POLL_MS).toBeGreaterThanOrEqual(3000);
  });

  test("test_711_useSnapshot_uses_that_interval_not_a_literal", () => {
    const text = readFileSync(join(process.cwd(), "src", "workbench", "lib", "data.ts"), "utf8");
    expect(text).toContain("refetchInterval: SNAPSHOT_POLL_MS");
    expect(text).not.toMatch(/refetchInterval:\s*\d/);
  });
});
