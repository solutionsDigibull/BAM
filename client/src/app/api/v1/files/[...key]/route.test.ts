import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signedFileUpstream } from "@/lib/proxy-path";

import { GET } from "./route";

const upstream = vi.fn<typeof fetch>();
const ORIGIN = "http://localhost:3000";
const TENANT = "6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11";
const PROJECT = "01a18113-88f5-705d-bb92-3f1873169aef";
const SHA = "a".repeat(64);
const KEY = `${TENANT}/${PROJECT}/exports/${SHA}`;
const SIG = "b".repeat(64);

function call(path: string, headers: Record<string, string> = {}) {
  const req = new NextRequest(`${ORIGIN}/api/v1/files/${path}`, { headers });
  return GET(req, { params: Promise.resolve({ key: path.split("?")[0]!.split("/") }) });
}

describe("signed downloads (#398)", () => {
  beforeEach(() => {
    vi.stubEnv("SBAM_API_URL", "http://sbam-api:8000");
    vi.stubGlobal("fetch", upstream);
    upstream.mockReset();
    upstream.mockResolvedValue(
      new Response("zip-bytes", {
        headers: {
          "content-type": "application/zip",
          "content-disposition": 'attachment; filename="okf.zip"',
          "cache-control": "no-store",
          etag: '"x"',
          "set-cookie": "leak=1",
        },
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("e5_398_t01 forwards a signed link to the API and returns the file with its download headers", async () => {
    const res = await call(`${KEY}?exp=1893456000&sig=${SIG}`);
    const [url] = upstream.mock.calls[0]!;
    expect(String(url)).toBe(`http://sbam-api:8000/api/v1/files/${KEY}?exp=1893456000&sig=${SIG}`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/zip");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="okf.zip"');
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.text()).toBe("zip-bytes");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("e5_398_t02 never sends the browser's cookie or token to the API", async () => {
    await call(`${KEY}?exp=1893456000&sig=${SIG}`, {
      cookie: "authjs.session-token=secret",
      authorization: "Bearer browser-token",
    });
    const init = upstream.mock.calls[0]![1];
    const sent = [...new Headers(init?.headers).keys()];
    expect(sent).not.toContain("cookie");
    expect(sent).not.toContain("authorization");
    expect(init?.redirect).toBe("manual");
  });

  it("e5_398_t03 answers what the API answers for an invalid or expired signature", async () => {
    upstream.mockResolvedValue(
      Response.json(
        { title: "Forbidden", status: 403 },
        { status: 403, headers: { "content-type": "application/problem+json" } },
      ),
    );
    const res = await call(`${KEY}?exp=1&sig=${SIG}`);
    expect(res.status).toBe(403);
  });

  it("e5_398_t04 refuses a path that could leave /api/v1/files without calling the API", async () => {
    for (const path of [
      `../me?exp=1&sig=${SIG}`,
      `${TENANT}/..%2F..%2Fadmin/x?exp=1&sig=${SIG}`,
      `${KEY}?exp=1`,
      `${KEY}?sig=${SIG}`,
      `${KEY}?exp=abc&sig=${SIG}`,
      `${KEY}?exp=1&sig=not-hex`,
    ]) {
      const res = await call(path);
      expect(res.status, path).toBe(400);
    }
    expect(upstream).not.toHaveBeenCalled();
  });

  it("e5_398_t05 passes on only exp and sig, nothing else from the query", async () => {
    await call(`${KEY}?exp=1893456000&sig=${SIG}&other=1&token=abc`);
    expect(String(upstream.mock.calls[0]![0])).toBe(
      `http://sbam-api:8000/api/v1/files/${KEY}?exp=1893456000&sig=${SIG}`,
    );
  });

  it("e5_398_t06 answers 502 when the API is unreachable or breaks with a non-JSON error", async () => {
    upstream.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    expect((await call(`${KEY}?exp=1&sig=${SIG}`)).status).toBe(502);
    upstream.mockResolvedValueOnce(
      new Response("<html>boom</html>", { status: 500, headers: { "content-type": "text/html" } }),
    );
    expect((await call(`${KEY}?exp=1&sig=${SIG}`)).status).toBe(502);
  });

  it("the helper rejects an empty key and dot segments", () => {
    const q = new URLSearchParams({ exp: "1", sig: SIG });
    expect(signedFileUpstream([], q)).toBeNull();
    expect(signedFileUpstream([".", "x"], q)).toBeNull();
    expect(signedFileUpstream([TENANT, SHA], q)).toBe(
      `/api/v1/files/${TENANT}/${SHA}?exp=1&sig=${SIG}`,
    );
  });
});
