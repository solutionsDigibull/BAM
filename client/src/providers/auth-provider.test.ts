import { describe, expect, it } from "vitest";

import { authProvider } from "./auth-provider";

describe("authProvider (E1.14)", () => {
  it("sends a 401 to the login page and ignores other errors", async () => {
    window.history.pushState({}, "", "/admin/projects");
    expect(await authProvider.onError({ statusCode: 401 })).toMatchObject({
      redirectTo: "/login-admin",
    });
    window.history.pushState({}, "", "/projects");
    expect(await authProvider.onError({ statusCode: 401 })).toMatchObject({
      redirectTo: "/login-fde",
    });
    expect(await authProvider.onError({ statusCode: 409 })).toEqual({});
  });
});
