"use server";

import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { auth, providerId, signIn, signOut as authSignOut, type Portal } from "@/auth";
import { isAdmin, parseRoles } from "@/lib/roles";
import { DEV_ROLES_COOKIE, devAuthEnabled, keycloakEnabled } from "@/lib/session";

export async function devSignIn(formData: FormData) {
  if (!devAuthEnabled()) notFound();
  const roles = parseRoles(String(formData.get("roles") ?? ""));
  (await cookies()).set(DEV_ROLES_COOKIE, roles.join(","), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
  // Straight to the landing page: a server-action redirect to "/" is not re-routed by src/proxy.ts.
  redirect(roles.length === 0 ? "/login-fde" : isAdmin(roles) ? "/admin/projects" : "/projects");
}

export async function keycloakSignIn(formData: FormData) {
  if (!keycloakEnabled()) notFound();
  const portal: Portal = formData.get("portal") === "admin" ? "admin" : "fde";
  // Keycloak down: Auth.js redirects to pages.error (the login card) with error=Configuration.
  await signIn(providerId(portal), { redirectTo: "/" });
}

export async function signOut() {
  (await cookies()).delete(DEV_ROLES_COOKIE);
  if (keycloakEnabled() && (await auth())) await authSignOut({ redirectTo: "/login-fde" });
  redirect("/login-fde");
}
