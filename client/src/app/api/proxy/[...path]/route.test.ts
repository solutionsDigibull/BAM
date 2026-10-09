import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { upstreamPath } from "@/lib/proxy-path";

const getToken = vi.fn();
vi.mock("next-auth/jwt", () => ({ getToken: (...a: unknown[]) => getToken(...a) }));

const { GET, POST } = await import("./route");

const upstream = vi.fn<typeof fetch>();
const ORIGIN = "http://localhost:3000";

function call(
  handler: typeof GET,
  path: string,
  init: { method?: string; headers?: Record<string, string>; body?: string } = {},
) {
  const req = new NextRequest(`${ORIGIN}/api/proxy/${path}`, init);
  return handler(req, { params: Promise.resolve({ path: path.split("?")[0]!.split("/") }) });
}

describe("API proxy (E1.14)", () => {
  beforeEach(() => {
    vi.stubEnv("AUTH_SECRET", "test-secret");
    vi.stubEnv("SBAM_API_URL", "http://sbam-api:8000");
    vi.stubGlobal("fetch", upstream);
    getToken.mockResolvedValue({ accessToken: "session-token" });
    upstream.mockReset();
    upstream.mockResolvedValue(
      new Response('{"ok":true}', { headers: { "content-type": "application/json", etag: '"5"' } }),
    );
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("e1_14_t01 forwards the session token upstream and never returns it", async () => {
    const res = await call(GET, "api/v1/me");
    const [url, init] = upstream.mock.calls[0]!;
    expect(String(url)).toMatch(/\/api\/v1\/me$/);
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer session-token");
    expect(res.status).toBe(200);
    expect(res.headers.get("etag")).toBe('"5"');
    expect([...res.headers.keys()]).not.toContain("authorization");
    expect(await res.text()).not.toContain("session-token");
  });

  it.each([
    ["http:", "169.254.169.254"],
    ["api", "v1", "..", "..", "admin"],
    ["api", "v1", "x%2F..%2Fadmin"],
    ["api", "v1", "a/../../b"],
    ["admin", "users"],
    ["api", "v1"],
  ])("e1_14_t03 SSRF path %j is a 400 and makes no upstream call", (...segments) => {
    expect(upstreamPath(segments)).toBeNull();
  });

  it("e1_14_t03 absolute URL through the route is a 400 with no outbound request", async () => {
    const res = await call(GET, "http://169.254.169.254/latest");
    expect(res.status).toBe(400);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(upstream).not.toHaveBeenCalled();
  });

  it("e1_14_t07 anonymous call is a 401 with no upstream call", async () => {
    getToken.mockResolvedValue(null);
    const res = await call(GET, "api/v1/projects");
    expect(res.status).toBe(401);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("e1_14_t08 a browser Authorization header is replaced by the session token", async () => {
    await call(GET, "api/v1/projects", {
      headers: {
        authorization: "Bearer forged",
        cookie: "authjs.session-token=x",
        "if-match": '"5"',
      },
    });
    const headers = new Headers(upstream.mock.calls[0]![1]?.headers);
    expect(headers.get("authorization")).toBe("Bearer session-token");
    expect(headers.get("cookie")).toBeNull();
    expect(headers.get("if-match")).toBe('"5"');
  });

  it("refuses a cross-origin write (CSRF) and forwards a same-origin one", async () => {
    const evil = await call(POST, "api/v1/projects", {
      method: "POST",
      headers: { origin: "https://evil.test" },
      body: "{}",
    });
    expect(evil.status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();

    await call(POST, "api/v1/projects", {
      method: "POST",
      headers: {
        origin: ORIGIN,
        host: "localhost:3000",
        "idempotency-key": "k1",
        "content-type": "application/json",
      },
      body: '{"name":"a"}',
    });
    const init = upstream.mock.calls[0]![1];
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("idempotency-key")).toBe("k1");
  });

  it("accepts a same-origin write when the server's own URL is the container bind address", async () => {
    const req = new NextRequest("http://0.0.0.0:3000/api/proxy/api/v1/projects", {
      method: "POST",
      headers: { origin: ORIGIN, host: "localhost:3000" },
      body: "{}",
    });
    const res = await POST(req, { params: Promise.resolve({ path: ["api", "v1", "projects"] }) });
    expect(res.status).toBe(200);
    expect(upstream).toHaveBeenCalledOnce();
  });

  it("refuses a write with no or a null Origin", async () => {
    for (const origin of [undefined, "null"]) {
      const res = await call(POST, "api/v1/projects", {
        method: "POST",
        headers: origin ? { origin } : {},
        body: "{}",
      });
      expect(res.status).toBe(403);
    }
    expect(upstream).not.toHaveBeenCalled();
  });

  it("turns a non-JSON upstream 5xx into problem+json 502", async () => {
    upstream.mockResolvedValue(
      new Response("<html>bad gateway</html>", {
        status: 502,
        headers: { "content-type": "text/html" },
      }),
    );
    const res = await call(GET, "api/v1/projects");
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toBe("application/problem+json");
    expect(await res.text()).not.toContain("<html>");
  });
});
