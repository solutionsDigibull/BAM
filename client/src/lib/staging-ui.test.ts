import { describe, expect, test } from "vitest";

import { hostScript, stagingFrameSrc, stagingUpstream, withHostScript } from "./staging-ui";

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";

describe("the Staging UI frame (#861)", () => {
  test("test_861_the_frame_address_ends_in_a_slash_so_relative_assets_resolve_under_it", () => {
    expect(stagingFrameSrc(P, W)).toBe(`/api/staging-ui/${P}/${W}/`);
  });

  test("test_861_the_upstream_path_is_the_staging_ui_route_and_the_entry_file_is_the_empty_path", () => {
    expect(stagingUpstream(P, W, undefined)).toBe(
      `/api/v1/projects/${P}/workflows/${W}/staging/ui/`,
    );
    expect(stagingUpstream(P, W, ["assets", "index-ab12.js"])).toBe(
      `/api/v1/projects/${P}/workflows/${W}/staging/ui/assets/index-ab12.js`,
    );
  });

  test("test_861_ids_that_are_not_uuids_and_paths_that_leave_the_bundle_are_refused", () => {
    expect(stagingUpstream("x", W, [])).toBeNull();
    expect(stagingUpstream(P, "../w", [])).toBeNull();
    expect(stagingUpstream(P, W, ["..", "secret"])).toBeNull();
    expect(stagingUpstream(P, W, ["a/b"])).toBeNull();
    expect(stagingUpstream(P, W, ["a%2Fb"])).toBeNull();
  });

  test("test_861_the_host_script_goes_first_in_the_head_of_the_entry_page", () => {
    const html = withHostScript(
      '<!doctype html><html><head><meta charset="utf-8"><script type="module" src="./a.js"></script></head><body></body></html>',
      P,
      W,
    );
    const at = html.indexOf("__host.js");
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(html.indexOf("./a.js"));
    expect(html).toContain(`src="/api/staging-ui/${P}/${W}/__host.js"`);
    expect(withHostScript("<p>x</p>", P, W).startsWith("<base ")).toBe(true);
  });

  test("test_861_the_host_script_sends_only_the_bundles_own_mcp_calls_to_the_staging_mcp_route", async () => {
    const calls: string[] = [];
    const win = {
      fetch: async (input: unknown) => {
        calls.push(String(input));
        return new Response("{}");
      },
    } as unknown as Window & typeof globalThis;
    const loc = {
      pathname: `/api/staging-ui/${P}/${W}/`,
      origin: "https://app.test",
      href: `https://app.test/api/staging-ui/${P}/${W}/`,
    };
    new Function("window", "location", "Request", hostScript())(win, loc, Request);
    await win.fetch("/mcp", { method: "POST" });
    await win.fetch("https://other.test/mcp");
    await win.fetch("https://app.test/other");
    expect(calls).toEqual([
      `https://app.test/api/proxy/api/v1/projects/${P}/workflows/${W}/staging/mcp`,
      "https://other.test/mcp",
      "https://app.test/other",
    ]);
  });

  test("test_908_a_base_pins_relative_assets_under_the_workflow_folder_before_any_script", () => {
    // Next redirects `/api/staging-ui/<p>/<w>/` to `.../<w>` (308): without a <base>, `./app.js` resolved to `.../<p>/app.js`.
    const html = withHostScript('<html><head><script src="./app.js"></script></head></html>', P, W);
    const base = `<base href="/api/staging-ui/${P}/${W}/">`;
    expect(html).toContain(base);
    expect(html.indexOf(base)).toBeLessThan(html.indexOf("./app.js"));
    expect(html.indexOf(base)).toBeLessThan(html.indexOf("__host.js"));
  });

  test("test_908_the_host_script_still_reads_the_ids_when_the_address_lost_its_trailing_slash", async () => {
    const calls: string[] = [];
    const win = {
      fetch: async (input: unknown) => {
        calls.push(String(input));
        return new Response("{}");
      },
    } as unknown as Window & typeof globalThis;
    const loc = {
      pathname: `/api/staging-ui/${P}/${W}`,
      origin: "https://app.test",
      href: `https://app.test/api/staging-ui/${P}/${W}`,
    };
    new Function("window", "location", "Request", hostScript())(win, loc, Request);
    await win.fetch("/mcp", { method: "POST" });
    expect(calls).toEqual([
      `https://app.test/api/proxy/api/v1/projects/${P}/workflows/${W}/staging/mcp`,
    ]);
  });
});
