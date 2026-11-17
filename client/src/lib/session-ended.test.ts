import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession, guardPoll, resetSessionEnded, sessionHasEnded } from "./session-ended";

const assign = vi.fn();

const at = (pathname: string, search = "") =>
  vi.stubGlobal("location", { origin: "http://localhost:3000", pathname, search, assign });

beforeEach(() => {
  resetSessionEnded();
  assign.mockClear();
  at("/projects/p1/documents", "?tab=2");
});
afterEach(() => vi.unstubAllGlobals());

const query = (error: unknown) => ({ state: { error } });

describe("polling after the session ended (#796)", () => {
  it("a polled query that failed with 401 stops polling and sends the user to login once", () => {
    const poll = guardPoll(() => 2000);
    expect(poll(query({ statusCode: 401 }))).toBe(false);
    expect(poll(query({ statusCode: 401 }))).toBe(false);
    expect(poll(query({ statusCode: 401 }))).toBe(false);
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(
      "/login-fde?callbackUrl=%2Fprojects%2Fp1%2Fdocuments%3Ftab%3D2",
    );
    expect(sessionHasEnded()).toBe(true);
  });

  it("keeps the caller's interval while there is no 401", () => {
    const poll = guardPoll(() => 2000);
    expect(poll(query(null))).toBe(2000);
    expect(poll(query({ statusCode: 500 }))).toBe(2000);
    expect(poll(query({ statusCode: 403 }))).toBe(2000);
    expect(assign).not.toHaveBeenCalled();
  });

  it("passes the query to the caller's callback and keeps its `false`", () => {
    const poll = guardPoll((q: ReturnType<typeof query>) => (q.state.error ? 1 : false));
    expect(poll(query(null))).toBe(false);
  });

  it("the admin portal goes to its own login", () => {
    at("/admin/projects");
    endSession();
    expect(assign).toHaveBeenCalledWith("/login-admin?callbackUrl=%2Fadmin%2Fprojects");
  });

  it("on a login page nothing happens, so there is no redirect loop", () => {
    at("/login-fde");
    expect(guardPoll(() => 1)(query({ statusCode: 401 }))).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});
