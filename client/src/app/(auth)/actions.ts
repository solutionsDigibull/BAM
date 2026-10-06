"use server";

import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { parseRoles } from "@/lib/roles";
import { DEV_ROLES_COOKIE, devAuthEnabled } from "@/lib/session";

export async function devSignIn(formData: FormData) {
  if (!devAuthEnabled()) notFound();
  const roles = parseRoles(String(formData.get("roles") ?? ""));
  (await cookies()).set(DEV_ROLES_COOKIE, roles.join(","), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
  redirect("/");
}

export async function signOut() {
  (await cookies()).delete(DEV_ROLES_COOKIE);
  redirect("/login-fde");
}
