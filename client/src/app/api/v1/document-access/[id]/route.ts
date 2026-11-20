// Signed access to an uploaded document (#895). The API answers `GET .../documents/<id>/access` with a
// short-lived link to `/api/v1/document-access/<id>?t=&p=&exp=&sig=`, which the browser opens against this
// origin (it holds no token). This hands the request to the API, which checks the signature (403 when it is
// invalid or expired) and that the document is still released (409 when it was deleted or blocked since).
//
// No session is needed (the signature is the authority) and none is sent: the browser's Cookie and
// Authorization never reach the API. The API address is read when the request arrives.
import type { NextRequest } from "next/server";

import { signedDocumentUpstream } from "@/lib/proxy-path";

const FORWARD_RESPONSE = [
  "cache-control", // no-store on downloads of tenant data
  "content-disposition", // always an attachment
  "content-length",
  "content-security-policy", // sandbox: the file never renders in this origin
  "content-type",
  "cross-origin-resource-policy",
  "retry-after",
  "x-content-type-options",
  "x-correlation-id",
];

const problem = (status: number, title: string, detail?: string) =>
  Response.json(
    { type: "about:blank", title, status, ...(detail ? { detail } : {}) },
    { status, headers: { "content-type": "application/problem+json" } },
  );

type Ctx = { params: Promise<{ id: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const path = signedDocumentUpstream((await ctx.params).id, req.nextUrl.searchParams);
  if (!path) return problem(400, "Bad Request", "Not a signed document link.");

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
