// Signed downloads (#398). The API answers a download request (OKF bundle, SOW export) with a 302 to
// `/api/v1/files/<key>?exp=&sig=`, which the browser follows against this origin. Nothing here served
// that path, so every such download was a 404. This hands the request to the API, which checks the
// signature (403 when it is invalid or expired).
//
// No session is needed (the signature is the authority) and none is sent: the browser's Cookie and
// Authorization never reach the API. The API address is read when the request arrives, not when the
// app is built, because it is a runtime setting (a `next.config` rewrite would fix it at build time).
import type { NextRequest } from "next/server";

import { signedFileUpstream } from "@/lib/proxy-path";

const FORWARD_RESPONSE = [
  "cache-control", // no-store on downloads of tenant data
  "content-disposition", // the file name of the download
  "content-length",
  "content-type",
  "etag",
  "retry-after",
  "x-correlation-id",
];

const problem = (status: number, title: string, detail?: string) =>
  Response.json(
    { type: "about:blank", title, status, ...(detail ? { detail } : {}) },
    { status, headers: { "content-type": "application/problem+json" } },
  );

type Ctx = { params: Promise<{ key: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const path = signedFileUpstream((await ctx.params).key, req.nextUrl.searchParams);
  if (!path) return problem(400, "Bad Request", "Not a signed download link.");

  let upstream: Response;
  try {
    upstream = await fetch(`${process.env.SBAM_API_URL ?? "http://localhost:8000"}${path}`, {
      method: "GET",
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return problem(502, "Bad Gateway", "API unreachable.");
  }

  const type = upstream.headers.get("content-type") ?? "";
  if (upstream.status >= 500 && !type.includes("json")) {
    return problem(502, "Bad Gateway", "API returned a non-JSON error.");
  }
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}
