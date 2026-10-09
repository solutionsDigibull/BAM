// Route guard that runs before rendering (Next 16 proxy, formerly middleware). Route-group layouts are not an
// auth boundary: Next renders the layout and the page in parallel, so a page's payload would still ship
// when its layout redirects or renders Forbidden. The layouts keep their guards as defence in depth.
import { NextResponse, type NextRequest } from "next/server";

import { isAdmin, isFde, isUuid } from "@/lib/roles";
import { getSession } from "@/lib/session";

export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const portal = pathname.startsWith("/admin") ? "admin" : "fde";
  const session = await getSession();
  if (pathname === "/") {
    // Same landing choice as app/page.tsx, but as a real 307 before the streamed render starts.
    const home = !session ? "/login-fde" : isAdmin(session.roles) ? "/admin/projects" : "/projects";
    return NextResponse.redirect(new URL(home, req.url));
  }
  if (!session) return NextResponse.redirect(new URL(`/login-${portal}`, req.url));
  if (!(portal === "admin" ? isAdmin : isFde)(session.roles)) {
    return NextResponse.rewrite(new URL(`/forbidden/${portal}`, req.url), { status: 403 });
  }
  // /projects/<projectId>/workflows/<workflowId>/...: ids are checked before any data fetch.
  const [, root, projectId, , workflowId] = pathname.split("/");
  if (
    root === "projects" &&
    [projectId, workflowId].some((id) => id !== undefined && !isUuid(id))
  ) {
    return NextResponse.rewrite(new URL("/not-found", req.url), { status: 404 });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/", "/admin/:path*", "/projects/:path*"] };
