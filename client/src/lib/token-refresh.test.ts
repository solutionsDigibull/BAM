import { describe, expect, it, vi } from "vitest";

import { needsRefresh, refresh, REFRESH_BEFORE_SECONDS, type Post } from "./token-refresh";

const NOW = 1_800_000_000;
const tokens = {
  accessToken: "old-access",
  refreshToken: "old-refresh",
  idToken: "id-1",
  expiresAt: NOW + 30,
};

const ok = (body: object): Post => vi.fn(async () => ({ ok: true, json: async () => body }));

describe("silent refresh (E2.5-T07)", () => {
  it("refreshes only inside the window before expiry", () => {
    expect(needsRefresh({ expiresAt: NOW + REFRESH_BEFORE_SECONDS + 1 }, NOW)).toBe(false);
    expect(needsRefresh({ expiresAt: NOW + REFRESH_BEFORE_SECONDS }, NOW)).toBe(true);
    expect(needsRefresh({ expiresAt: NOW - 1 }, NOW)).toBe(true);
    expect(needsRefresh({ expiresAt: NOW - 1, error: "RefreshFailed" }, NOW)).toBe(false);
  });

  it("uses the refresh grant for the portal's client and stores the rotated tokens", async () => {
    const post = ok({ access_token: "new-access", refresh_token: "new-refresh", expires_in: 300 });
    const next = await refresh(tokens, "sbam-fde", NOW, post);
    const body = vi.mocked(post).mock.calls[0]![0];
    expect(Object.fromEntries(body)).toEqual({
      grant_type: "refresh_token",
      client_id: "sbam-fde",
      refresh_token: "old-refresh",
    });
    expect(next).toEqual({
      accessToken: "new-access",
      refreshToken: "new-refresh", // rotation: never keep the old one
      idToken: "id-1",
      expiresAt: NOW + 300,
      error: undefined,
    });
  });

  it.each([
    [
      "rejected (revoked / reused refresh token)",
      vi.fn(async () => ({ ok: false, json: async () => ({ error: "invalid_grant" }) })),
    ],
    ["incomplete response", ok({ access_token: "a" })],
    ["Keycloak unreachable", vi.fn(async () => Promise.reject(new Error("ECONNREFUSED")))],
  ])("ends the session when the refresh is %s", async (_, post) => {
    const next = await refresh(tokens, "sbam-fde", NOW, post as Post);
    expect(next.error).toBe("RefreshFailed");
    expect(next.accessToken).toBeUndefined();
    expect(next.refreshToken).toBeUndefined();
  });

  it("fails without a refresh token and never calls Keycloak", async () => {
    const post = ok({});
    const next = await refresh({ expiresAt: NOW }, "sbam-admin", NOW, post);
    expect(next.error).toBe("RefreshFailed");
    expect(post).not.toHaveBeenCalled();
  });
});
