import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signedDocumentUpstream } from "@/lib/proxy-path";

import { GET } from "./route";

const upstream = vi.fn<typeof fetch>();
const ORIGIN = "http://localhost:3000";
const DOC = "01a18113-88f5-705d-bb92-3f1873169aef";
const TENANT = "6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11";
const PROJECT = "01a18113-88f5-705d-bb92-3f1873169af0";
const SIG = "b".repeat(64);
const QUERY = `t=${TENANT}&p=${PROJECT}&exp=1893456000&sig=${SIG}`;

function call(id: string, query: string, headers: Record<string, string> = {}) {
  const req = new NextRequest(`${ORIGIN}/api/v1/document-access/${id}?${query}`, { headers });
  return GET(req, { params: Promise.resolve({ id }) });
}

describe("signed document links (#895)", () => {
  beforeEach(() => {
    vi.stubEnv("SBAM_API_URL", "http://sbam-api:8000");
    vi.stubGlobal("fetch", upstream);
    upstream.mockReset();
    upstream.mockResolvedValue(
      new Response("pdf-bytes", {
        headers: {
          "content-type": "application/octet-stream",
          "content-disposition": 'attachment; filename="spec.pdf"',
          "cache-control": "private, no-store",
          "x-content-type-options": "nosniff",
          "content-security-policy": "sandbox; default-src 'none'",
          "set-cookie": "leak=1",
        },
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("ui2b_t01 forwards a signed link to the API and returns the file as an attachment", async () => {
    const res = await call(DOC, QUERY);
    const [url] = upstream.mock.calls[0]!;
    expect(String(url)).toBe(`http://sbam-api:8000/api/v1/document-access/${DOC}?${QUERY}`);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="spec.pdf"');
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toContain("sandbox");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await res.text()).toBe("pdf-bytes");
  });

  it("ui2b_t02 never sends the browser's cookie or token to the API", async () => {
    await call(DOC, QUERY, {
      cookie: "authjs.session-token=secret",
      authorization: "Bearer browser-token",
    });
    const init = upstream.mock.calls[0]![1];
    expect(JSON.stringify(init)).not.toMatch(/secret|browser-token/);
  });

  it("ui2b_t03 passes the API's refusal on as it is", async () => {
    upstream.mockResolvedValue(
      new Response('{"code":"DOCUMENT_NOT_RELEASED"}', {
        status: 409,
        headers: { "content-type": "application/problem+json" },
      }),
    );
    const res = await call(DOC, QUERY);
    expect(res.status).toBe(409);
  });

  it("ui2b_t04 refuses a malformed link without calling the API", async () => {
    for (const [id, query] of [
      ["not-a-uuid", QUERY],
      [DOC, `t=${TENANT}&p=${PROJECT}&exp=abc&sig=${SIG}`],
      [DOC, `t=${TENANT}&p=${PROJECT}&exp=1893456000&sig=zz`],
      [DOC, `p=${PROJECT}&exp=1893456000&sig=${SIG}`],
      [DOC, `t=../x&p=${PROJECT}&exp=1893456000&sig=${SIG}`],
    ] as const) {
      expect((await call(id, query)).status).toBe(400);
    }
    expect(upstream).not.toHaveBeenCalled();
  });

  it("ui2b_t05 passes on only the signed fields", () => {
    const q = new URLSearchParams(`${QUERY}&evil=1`);
    expect(signedDocumentUpstream(DOC, q)).toBe(`/api/v1/document-access/${DOC}?${QUERY}`);
  });
});
