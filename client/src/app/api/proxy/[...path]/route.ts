// API proxy (TD §9.1, E1.14): the browser calls /api/proxy/api/v1/*. This handler attaches the access
// token from the encrypted httpOnly session cookie, so the token never reaches browser JS or storage.
// Silent refresh (E2.5): wrapped in Auth.js `auth()`, so the jwt callback refreshes a token that is about to
// expire and the rotated tokens are written back to the session cookie on this response. A failed refresh
// answers 401 and the UI sends the user to login.
import type { NextRequest } from "next/server";

import { auth } from "@/auth";

import { upstreamPath } from "@/lib/proxy-path";

const API_URL = process.env.SBAM_API_URL ?? "http://localhost:8000";
const FORWARD_REQUEST = [
  "accept",
  "content-type",
  "if-match",
  "idempotency-key",
  "x-correlation-id",
];
const FORWARD_RESPONSE = ["content-type", "etag", "location", "retry-after", "x-correlation-id"];

const problem = (status: number, title: string, detail?: string) =>
  Response.json(
    { type: "about:blank", title, status, ...(detail ? { detail } : {}) },
    { status, headers: { "content-type": "application/problem+json" } },
  );

function sameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false; // "null" or malformed Origin
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

async function forward(req: NextRequest & { auth?: { accessToken?: string } | null }, ctx: Ctx) {
  const path = upstreamPath((await ctx.params).path);
  if (!path) return problem(400, "Bad Request", "Proxy only forwards /api/v1/ paths.");

  // CSRF: the session cookie is SameSite=Lax; writes must also come from this origin. Compare with the
  // Host the browser used: in a container nextUrl.origin is the bind address (0.0.0.0), not the site.
  if (req.method !== "GET" && !sameOrigin(req)) {
    return problem(403, "Forbidden", "Cross-origin write refused.");
  }

  const accessToken = req.auth?.accessToken;
  if (typeof accessToken !== "string") return problem(401, "Unauthorized");

  // Allowlist: the browser's Authorization and Cookie never reach sbam-api.
  const headers = new Headers({ authorization: `Bearer ${accessToken}` });
  for (const name of FORWARD_REQUEST) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}${path}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "DELETE" ? undefined : req.body,
      // @ts-expect-error -- Node fetch needs duplex to stream a request body (not in the DOM types).
      duplex: "half",
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return problem(502, "Bad Gateway", "API unreachable.");
  }

  const type = upstream.headers.get("content-type") ?? "";
  if (upstream.status >= 500 && !type.includes("json")) {
    return problem(
      upstream.status === 503 ? 503 : 502,
      "Bad Gateway",
      "API returned a non-JSON error.",
    );
  }
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

// Auth.js needs AUTH_SECRET and an issuer; without them there is no session to forward.
const handle = (req: NextRequest, ctx: Ctx) =>
  process.env.AUTH_SECRET && process.env.KEYCLOAK_ISSUER
    ? (auth(forward as never) as unknown as (r: NextRequest, c: Ctx) => Promise<Response>)(req, ctx)
    : problem(401, "Unauthorized");

export { handle as DELETE, handle as GET, handle as PATCH, handle as POST, handle as PUT };
