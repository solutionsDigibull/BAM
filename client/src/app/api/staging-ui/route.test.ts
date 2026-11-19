import { describe, expect, test, vi } from "vitest";

vi.mock("@/auth", () => ({ auth: (fn: unknown) => fn }));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const ctx = (path?: string[], projectId = P) => ({
  params: Promise.resolve({ projectId, workflowId: W, path }),
});
const req = (token?: string) => ({ auth: token ? { accessToken: token } : null }) as never;

async function load() {
  process.env.AUTH_SECRET = "x";
  process.env.KEYCLOAK_ISSUER = "http://kc";
  vi.resetModules();
  return (await import("./[projectId]/[workflowId]/[[...path]]/route")).GET;
}

describe("the Staging UI route (#861)", () => {
  test("test_861_the_route_answers_401_without_a_session_and_404_for_a_bad_id_or_path", async () => {
    const GET = await load();
    expect((await GET(req(), ctx([]))).status).toBe(401);
    expect((await GET(req("t"), ctx([], "not-a-uuid"))).status).toBe(404);
    expect((await GET(req("t"), ctx([".."]))).status).toBe(404);
  });

  test("test_861_the_entry_page_gets_the_host_script_and_the_apis_csp_and_the_token_goes_upstream_only", async () => {
    const GET = await load();
    const fetched: { url: string; auth: string | null }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      fetched.push({ url, auth: new Headers(init.headers).get("authorization") });
      return new Response("<html><head></head><body>ui</body></html>", {
        headers: {
          "content-type": "text/html",
          "content-security-policy": "default-src 'self'; connect-src 'self'",
        },
      });
    });
    const res = await GET(req("tok"), ctx());
    const body = await res.text();
    expect(fetched[0]).toEqual({
      url: `http://localhost:8000/api/v1/projects/${P}/workflows/${W}/staging/ui/`,
      auth: "Bearer tok",
    });
    expect(body).toContain("__host.js");
    expect(res.headers.get("content-security-policy")).toContain("connect-src 'self'");
    expect(res.headers.get("cache-control")).toBe("no-store");
    const script = await GET(req("tok"), ctx(["__host.js"]));
    expect(script.headers.get("content-type")).toContain("javascript");
    expect(fetched).toHaveLength(1); // the host script is ours, never fetched upstream
    vi.unstubAllGlobals();
  });
});
