// The files of a workflow's certified UI bundle for the Staging frame (#861). GET only. Like the API proxy it takes the
// access token from the session, so the token never reaches the browser; unlike it, it passes the API's
// Content-Security-Policy on (the bundle may reach only its own files, its own workflow server and its declared hosts).
// The entry page gets the host script that routes the bundle's `/mcp` calls to the Staging MCP route.
import type { NextRequest } from "next/server";

import { auth } from "@/auth";
import { HOST_SCRIPT, hostScript, withHostScript, stagingUpstream } from "@/lib/staging-ui";

const API_URL = process.env.SBAM_API_URL ?? "http://localhost:8000";
const PASS = [
  "content-type",
  "content-security-policy",
  "x-content-type-options",
  "referrer-policy",
];

type Ctx = { params: Promise<{ projectId: string; workflowId: string; path?: string[] }> };

const problem = (status: number, title: string) =>
  Response.json(
    { type: "about:blank", title, status },
    { status, headers: { "content-type": "application/problem+json" } },
  );

async function serve(req: NextRequest & { auth?: { accessToken?: string } | null }, ctx: Ctx) {
  const { projectId, workflowId, path } = await ctx.params;
  const upstream = stagingUpstream(
    projectId,
    workflowId,
    path?.length === 1 && path[0] === HOST_SCRIPT ? [] : path,
  );
  if (!upstream) return problem(404, "Not Found");
  const token = req.auth?.accessToken;
  if (typeof token !== "string") return problem(401, "Unauthorized");
  if (path?.length === 1 && path[0] === HOST_SCRIPT) {
    return new Response(hostScript(), {
      headers: {
        "content-type": "text/javascript; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    });
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${upstream}`, {
      headers: { authorization: `Bearer ${token}`, accept: "*/*" },
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return problem(502, "Bad Gateway");
  }
  const out = new Headers({ "cache-control": "no-store" });
  for (const name of PASS) {
    const value = res.headers.get(name);
    if (value) out.set(name, value);
  }
  const type = res.headers.get("content-type") ?? "";
  if (res.ok && type.includes("text/html") && (path?.length ?? 0) === 0) {
    out.delete("content-length");
    return new Response(withHostScript(await res.text(), projectId, workflowId), {
      status: 200,
      headers: out,
    });
  }
  return new Response(res.body, { status: res.status, headers: out });
}

const handle = (req: NextRequest, ctx: Ctx) =>
  process.env.AUTH_SECRET && process.env.KEYCLOAK_ISSUER
    ? (auth(serve as never) as unknown as (r: NextRequest, c: Ctx) => Promise<Response>)(req, ctx)
    : serve(req, ctx);

export { handle as GET };
