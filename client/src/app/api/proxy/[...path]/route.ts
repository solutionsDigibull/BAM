// API proxy (TD §9): the browser calls /api/proxy/*, this handler attaches the access token read from
// the encrypted session cookie, so the token never leaves the server. GET only for the spike (#31);
// #45 (E1.14) adds the other methods and problem+json mapping.
import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";

const API_URL = process.env.SBAM_API_URL ?? "http://localhost:8000";

const unauthorized = () =>
  Response.json(
    { type: "about:blank", title: "Unauthorized", status: 401 },
    { status: 401, headers: { "content-type": "application/problem+json" } },
  );

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  if (!process.env.AUTH_SECRET) return unauthorized(); // getToken throws MissingSecret otherwise
  const token = await getToken({
    req,
    secret: process.env.AUTH_SECRET,
    secureCookie: req.nextUrl.protocol === "https:",
  });
  if (typeof token?.accessToken !== "string") return unauthorized();
  const { path } = await ctx.params;
  const upstream = await fetch(`${API_URL}/${path.map(encodeURIComponent).join("/")}`, {
    headers: { authorization: `Bearer ${token.accessToken}` },
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
