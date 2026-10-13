"use server";

import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { providerId, signIn, signOut as authSignOut, type Portal } from "@/auth";
import { landing, safeCallback } from "@/lib/landing";
import { isAdmin, parseRoles } from "@/lib/roles";
import { DEV_ROLES_COOKIE, devAuthEnabled, keycloakEnabled, logoutHint } from "@/lib/session";

export async function devSignIn(formData: FormData) {
  if (!devAuthEnabled()) notFound();
  const roles = parseRoles(String(formData.get("roles") ?? ""));
  (await cookies()).set(DEV_ROLES_COOKIE, roles.join(","), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
  // Straight to the landing page: a server-action redirect to "/" is not re-routed by src/proxy.ts.
  if (roles.length === 0) redirect("/login-fde");
  redirect(isAdmin(roles) ? landing("admin", roles) : landing("fde", roles));
}

export async function keycloakSignIn(formData: FormData) {
  if (!keycloakEnabled()) notFound();
  const portal: Portal = formData.get("portal") === "admin" ? "admin" : "fde";
  // Only a same-origin path inside this portal survives (open-redirect guard, E2.5-T05). Otherwise
  // the admin portal goes through "/" (src/proxy.ts picks tenants vs projects by role) and the FDE
  // portal straight to its projects.
  const callback = safeCallback(String(formData.get("callbackUrl") ?? ""), portal);
  // Keycloak down: Auth.js redirects to pages.error (the login card) with error=Configuration.
  await signIn(providerId(portal), {
    redirectTo: callback ?? (portal === "admin" ? "/" : landing("fde", [])),
  });
}

export async function signOut() {
  (await cookies()).delete(DEV_ROLES_COOKIE);
  if (keycloakEnabled()) {
    const { portal = "fde", idToken } = await logoutHint();
    if (idToken) {
      // RP-initiated logout: clear the Auth.js session, then end the Keycloak SSO session too, so
      // the next "Sign in" asks for credentials again. Keycloak returns to this portal's login.
      await authSignOut({ redirect: false });
      const url = new URL(`${process.env.KEYCLOAK_ISSUER}/protocol/openid-connect/logout`);
      url.searchParams.set("id_token_hint", idToken);
      const site = process.env.AUTH_URL ?? "http://localhost:3000";
      url.searchParams.set(
        "post_logout_redirect_uri",
        `${site.replace(/\/$/, "")}/login-${portal}`,
      );
      redirect(url.toString());
    }
  }
  redirect("/login-fde");
}
