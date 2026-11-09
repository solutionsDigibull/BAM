import { describe, expect, test } from "vitest";

import config from "../next.config";

describe("the dev server configuration (#719)", () => {
  test("test_719_turbopacks_dev_file_cache_is_off_because_it_reloads_the_page_in_a_loop", () => {
    expect(config.experimental?.turbopackFileSystemCacheForDev).toBe(false);
  });

  test("test_719_the_production_build_keeps_its_standalone_output_and_hidden_powered_by_header", () => {
    expect(config.output).toBe("standalone");
    expect(config.poweredByHeader).toBe(false);
  });
});
