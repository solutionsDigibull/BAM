import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toApiError } from "@/providers/data-provider";

import { endSession, resetSessionEnded, sessionHasEnded } from "./session-ended";

const assign = vi.fn();

beforeEach(() => {
  resetSessionEnded();
  assign.mockClear();
  vi.stubGlobal("location", {
    ...window.location,
    origin: "http://localhost:3000",
    pathname: "/projects/p1/documents",
    search: "?tab=2",
    assign,
  });
});
afterEach(() => vi.unstubAllGlobals());

const status = (code: number) => new Response("{}", { status: code });

describe("session ended (#796)", () => {
  it("a 401 sends the user to login once, keeping the page as callbackUrl", async () => {
    await toApiError(status(401));
    await toApiError(status(401));
    await toApiError(status(401));
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(
      "/login-fde?callbackUrl=%2Fprojects%2Fp1%2Fdocuments%3Ftab%3D2",
    );
    expect(sessionHasEnded()).toBe(true);
  });

  it("the admin portal goes to its own login", () => {
    vi.stubGlobal("location", {
      origin: "http://localhost:3000",
      pathname: "/admin/projects",
      search: "",
      assign,
    });
    endSession();
    expect(assign).toHaveBeenCalledWith("/login-admin?callbackUrl=%2Fadmin%2Fprojects");
  });

  it("other errors do not redirect", async () => {
    for (const code of [403, 404, 409, 500]) await toApiError(status(code));
    expect(assign).not.toHaveBeenCalled();
  });

  it("on a login page nothing happens, so there is no redirect loop", async () => {
    vi.stubGlobal("location", {
      origin: "http://localhost:3000",
      pathname: "/login-fde",
      search: "",
      assign,
    });
    await toApiError(status(401));
    expect(assign).not.toHaveBeenCalled();
  });
});
