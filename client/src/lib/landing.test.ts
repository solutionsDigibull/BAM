import { describe, expect, it } from "vitest";

import { landing, safeCallback } from "./landing";

describe("role landing (E2.5)", () => {
  it("sends each role to its portal home", () => {
    expect(landing("admin", ["sbam-admin"])).toBe("/admin/projects");
    expect(landing("admin", ["super-admin"])).toBe("/admin/tenants");
    expect(landing("admin", ["super-admin", "sbam-admin"])).toBe("/admin/projects");
    expect(landing("fde", ["sbam-fde"])).toBe("/projects");
    expect(landing("fde", ["sbam-admin", "sbam-fde"])).toBe("/projects"); // portal decides
  });
});

describe("callbackUrl guard (E2.5-T05)", () => {
  it.each([
    "https://evil.example",
    "https://evil.example/projects",
    "//evil.example/projects",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "/projects\u0000",
    "/ projects",
    "projects",
    "",
    "/admin/projects", // the other portal
    "/projectsX", // prefix look-alike
  ])("e2_5_t05 drops %j for the FDE portal", (raw) => {
    expect(safeCallback(raw, "fde")).toBeNull();
  });

  it("keeps same-origin paths inside the portal, with their query", () => {
    expect(safeCallback("/projects", "fde")).toBe("/projects");
    const id = "01920000-0000-7000-8000-000000000000";
    expect(safeCallback(`/projects/${id}/sow?tab=2`, "fde")).toBe(`/projects/${id}/sow?tab=2`);
    expect(safeCallback("/admin/users", "admin")).toBe("/admin/users");
    expect(safeCallback("/projects", "admin")).toBeNull();
  });

  it("normalises dot segments so they can't climb out of the portal", () => {
    expect(safeCallback("/projects/../admin/users", "fde")).toBeNull();
  });
});
