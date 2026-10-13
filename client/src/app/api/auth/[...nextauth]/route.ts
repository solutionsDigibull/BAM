import type { NextRequest } from "next/server";

import { handlers } from "@/auth";
import { keycloakEnabled } from "@/lib/session";

// Without KEYCLOAK_ISSUER Auth.js has no valid provider and answers every request with a 500.
const notConfigured = () =>
  Response.json(
    {
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "Keycloak sign-in is not configured.",
    },
    { status: 404, headers: { "content-type": "application/problem+json" } },
  );

// The server-side session carries the tokens (for /api/proxy and logout). The browser-facing session
// endpoint must never return them, so they are removed here (E2.5-T06).
const SERVER_ONLY = ["accessToken", "idToken"];

async function withoutTokens(res: Response): Promise<Response> {
  if (!res.headers.get("content-type")?.includes("json")) return res;
  const body = await res.json();
  for (const key of SERVER_ONLY) if (body && typeof body === "object") delete body[key];
  const headers = new Headers(res.headers);
  headers.delete("content-length");
  return new Response(JSON.stringify(body), { status: res.status, headers });
}

export const GET = async (req: NextRequest) => {
  if (!keycloakEnabled()) return notConfigured();
  const res = await handlers.GET(req);
  return req.nextUrl.pathname.endsWith("/session") ? withoutTokens(res) : res;
};
export const POST = (req: NextRequest) =>
  keycloakEnabled() ? handlers.POST(req) : notConfigured();
