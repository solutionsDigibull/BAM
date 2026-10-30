// Route guard that runs before rendering (Next 16 proxy, formerly middleware). Route-group layouts are not an
// auth boundary: Next renders the layout and the page in parallel, so a page's payload would still ship
// when its layout redirects or renders Forbidden. The layouts keep their guards as defence in depth.
import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, newNonce } from "@/lib/csp";
import { landing } from "@/lib/landing";
import { isAdmin, isFde, isUuid } from "@/lib/roles";
import { getSession } from "@/lib/session";

// Where the browser uploads to in local development (the API returns the same URL, compose.yaml).
const DEV_UPLOAD_URL = process.env.TUSD_PUBLIC_URL || "http://localhost:1080/files/";

export async function proxy(req: NextRequest) {
  const nonce = newNonce();
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development", DEV_UPLOAD_URL);
  const res = await route(req, nonce, csp);
  res.headers.set("Content-Security-Policy", csp);
  return res;
}

// The paths the auth checks apply to; every other page only gets the CSP.
const GUARDED = /^\/(admin(\/|$)|projects(\/|$)|registry$|$)/;

async function route(req: NextRequest, nonce: string, csp: string) {
  const { pathname } = req.nextUrl;
  if (!GUARDED.test(pathname)) return passThrough(req, nonce, csp);
  const portal = pathname.startsWith("/admin") ? "admin" : "fde";
  const session = await getSession();
  if (pathname === "/") {
    // Same landing choice as app/page.tsx, but as a real 307 before the streamed render starts.
    const home = !session
      ? "/login-fde"
      : isAdmin(session.roles)
        ? landing("admin", session.roles)
        : landing("fde", session.roles);
    return NextResponse.redirect(new URL(home, req.url));
  }
  if (!session) {
    // Back to where the user was going after sign-in; actions.ts re-checks it (safeCallback).
    const login = new URL(`/login-${portal}`, req.url);
    login.searchParams.set("callbackUrl", `${pathname}${req.nextUrl.search}`);
    return NextResponse.redirect(login);
  }
  if (!(portal === "admin" ? isAdmin : isFde)(session.roles)) {
    return NextResponse.rewrite(new URL(`/forbidden/${portal}`, req.url), {
      status: 403,
      request: withNonce(req, nonce, csp),
    });
  }
  // /projects/<projectId>/workflows/<workflowId>/...: ids are checked before any data fetch.
  const [, root, projectId, , workflowId] = pathname.split("/");
  if (
    root === "projects" &&
    [projectId, workflowId].some((id) => id !== undefined && !isUuid(id))
  ) {
    return NextResponse.rewrite(new URL("/not-found", req.url), {
      status: 404,
      request: withNonce(req, nonce, csp),
    });
  }
  return passThrough(req, nonce, csp);
}

/** Next reads the nonce from the request's CSP header while rendering and stamps its own scripts. */
function withNonce(req: NextRequest, nonce: string, csp: string) {
  const headers = new Headers(req.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  return { headers };
}

function passThrough(req: NextRequest, nonce: string, csp: string) {
  return NextResponse.next({ request: withNonce(req, nonce, csp) });
}

// Every page (the CSP header goes on all of them); the auth checks above only act on the portal paths.
export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
