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

export const GET = (req: NextRequest) => (keycloakEnabled() ? handlers.GET(req) : notConfigured());
export const POST = (req: NextRequest) =>
  keycloakEnabled() ? handlers.POST(req) : notConfigured();
