import { describe, expect, it, vi } from "vitest";

import { LOGOUT_TIMEOUT_MS, revokeApiSession } from "./api-logout";

const ok = () => Promise.resolve(new Response(null, { status: 204 }));

describe("revokeApiSession (A-32)", () => {
  it("posts the access token to the API's logout and says it worked", async () => {
    const post = vi.fn(ok);
    expect(await revokeApiSession("tok", "http://api:8000", post)).toBe(true);
    const [url, init] = post.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api:8000/api/v1/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ authorization: "Bearer tok" });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("does nothing without an access token", async () => {
    const post = vi.fn(ok);
    expect(await revokeApiSession(undefined, "http://api:8000", post)).toBe(false);
    expect(post).not.toHaveBeenCalled();
  });

  it("never throws when the API is down: signing out goes on", async () => {
    const down = vi.fn(() => Promise.reject(new TypeError("fetch failed")));
    expect(await revokeApiSession("tok", "http://api:8000", down)).toBe(false);
  });

  it("reports a refusal (an already expired token) as not revoked, without throwing", async () => {
    const refused = vi.fn(() => Promise.resolve(new Response(null, { status: 401 })));
    expect(await revokeApiSession("tok", "http://api:8000", refused)).toBe(false);
  });

  it("gives the API a few seconds, not forever", () => {
    expect(LOGOUT_TIMEOUT_MS).toBeLessThanOrEqual(5000);
  });
});
