import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearSharedRefreshes,
  needsRefresh,
  refresh,
  REFRESH_BEFORE_SECONDS,
  refreshShared,
  SHARE_REFRESH_SECONDS,
  type Post,
} from "./token-refresh";

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

/** Keycloak with `revokeRefreshToken` and `refreshTokenMaxReuse: 0`: a refresh token works once. */
function rotating(): { post: Post; calls: () => number } {
  const used = new Set<string>();
  let n = 0;
  const post = vi.fn(async (body: URLSearchParams) => {
    const token = body.get("refresh_token") ?? "";
    await new Promise((r) => setTimeout(r, 5)); // the round trip: the parallel callers overlap
    if (used.has(token)) {
      return { ok: false, json: async () => ({ error: "invalid_token" }) };
    }
    used.add(token);
    n += 1;
    return {
      ok: true,
      json: async () => ({
        access_token: `access-${n}`,
        refresh_token: `refresh-${n}`,
        expires_in: 300,
      }),
    };
  });
  return { post, calls: () => post.mock.calls.length };
}

describe("one refresh per refresh token (#406)", () => {
  beforeEach(() => clearSharedRefreshes());

  it("e2_406_t01 requests that refresh at the same moment make one call and all get the new tokens", async () => {
    const kc = rotating();
    const all = await Promise.all(
      Array.from({ length: 6 }, () => refreshShared(tokens, "sbam-fde", NOW, kc.post)),
    );
    expect(kc.calls()).toBe(1);
    for (const next of all) {
      expect(next.error).toBeUndefined();
      expect(next.accessToken).toBe("access-1");
      expect(next.refreshToken).toBe("refresh-1");
    }
  });

  it("e2_406_t02 without sharing, the same six requests fail as they did (the bug)", async () => {
    const kc = rotating();
    const all = await Promise.all(
      Array.from({ length: 6 }, () => refresh(tokens, "sbam-fde", NOW, kc.post)),
    );
    expect(all.filter((r) => r.error === "RefreshFailed")).toHaveLength(5);
  });

  it("e2_406_t03 a request that arrives after the rotation with the old token gets the same result", async () => {
    const kc = rotating();
    const first = await refreshShared(tokens, "sbam-fde", NOW, kc.post);
    const late = await refreshShared(tokens, "sbam-fde", NOW + SHARE_REFRESH_SECONDS, kc.post);
    expect(kc.calls()).toBe(1);
    expect(late.refreshToken).toBe(first.refreshToken);
  });

  it("e2_406_t04 after the sharing window an old token refreshes on its own (and is refused)", async () => {
    const kc = rotating();
    await refreshShared(tokens, "sbam-fde", NOW, kc.post);
    const later = await refreshShared(tokens, "sbam-fde", NOW + SHARE_REFRESH_SECONDS + 1, kc.post);
    expect(kc.calls()).toBe(2);
    expect(later.error).toBe("RefreshFailed"); // Keycloak has revoked it: a stale cookie ends
  });

  it("e2_406_t05 different refresh tokens never share a result", async () => {
    const kc = rotating();
    const [a, b] = await Promise.all([
      refreshShared({ ...tokens, refreshToken: "user-a" }, "sbam-fde", NOW, kc.post),
      refreshShared({ ...tokens, refreshToken: "user-b" }, "sbam-fde", NOW, kc.post),
    ]);
    expect(kc.calls()).toBe(2);
    expect(a.accessToken).not.toBe(b.accessToken);
  });

  it("e2_406_t06 a failed refresh is shared too: one call, every caller ends its session", async () => {
    const post = vi.fn(async () => Promise.reject(new Error("ECONNREFUSED")));
    const all = await Promise.all(
      Array.from({ length: 4 }, () => refreshShared(tokens, "sbam-fde", NOW, post as Post)),
    );
    expect(post).toHaveBeenCalledTimes(1);
    for (const next of all) expect(next.error).toBe("RefreshFailed");
  });

  it("e2_406_t07 without a refresh token it fails and calls nothing", async () => {
    const kc = rotating();
    const next = await refreshShared({ expiresAt: NOW }, "sbam-admin", NOW, kc.post);
    expect(next.error).toBe("RefreshFailed");
    expect(kc.calls()).toBe(0);
  });

  it("e2_406_t08 the shared state is bounded and old entries are dropped", async () => {
    const kc = rotating();
    for (let i = 0; i < 260; i += 1) {
      await refreshShared({ ...tokens, refreshToken: `t-${i}` }, "sbam-fde", NOW, kc.post);
    }
    const map = (globalThis as { __sbamSharedRefresh?: Map<string, unknown> }).__sbamSharedRefresh;
    expect(map!.size).toBeLessThanOrEqual(200);
    await refreshShared({ ...tokens, refreshToken: "t-new" }, "sbam-fde", NOW + 1000, kc.post);
    expect(map!.size).toBe(1); // everything older than the window was dropped
  });
});
