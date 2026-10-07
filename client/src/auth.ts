// Auth.js with Keycloak (spike E0.13, #31; #51 hardens it). Off unless KEYCLOAK_ISSUER is set.
// One provider per portal, each bound to its own public Keycloak client (PKCE S256). The session is
// an encrypted httpOnly JWT cookie: the access token stays inside it and only roles and portal are
// exposed to pages, so no token ever reaches browser JS or storage.
import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

import { isAdmin, isFde, parseRoles, type Role } from "@/lib/roles";

export type Portal = "admin" | "fde";

const PORTAL_CLIENT: Record<Portal, string> = { admin: "sbam-admin", fde: "sbam-fde" };
const CAN_ENTER: Record<Portal, (roles: readonly Role[]) => boolean> = {
  admin: isAdmin,
  fde: isFde,
};

export const providerId = (portal: Portal) => `keycloak-${portal}`;
const portalOf = (provider: string | undefined): Portal | undefined =>
  provider === "keycloak-admin" ? "admin" : provider === "keycloak-fde" ? "fde" : undefined;

// Payload of a token just received from Keycloak's token endpoint; sbam-api verifies it again.
function rolesOf(accessToken: string | undefined): Role[] {
  if (!accessToken) return [];
  const payload = JSON.parse(Buffer.from(accessToken.split(".")[1] ?? "", "base64url").toString());
  return parseRoles((payload?.realm_access?.roles ?? []).join(","));
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: (["admin", "fde"] as const).map((portal) =>
    Keycloak({
      id: providerId(portal),
      name: `Keycloak (${PORTAL_CLIENT[portal]})`,
      issuer: process.env.KEYCLOAK_ISSUER,
      clientId: PORTAL_CLIENT[portal],
      clientSecret: "", // public client
      client: { token_endpoint_auth_method: "none" },
      checks: ["pkce", "state"],
    }),
  ),
  pages: { signIn: "/login-fde", error: "/login-fde" },
  callbacks: {
    // Wrong portal is refused at sign-in: an admin cannot enter through the FDE client and vice versa.
    signIn({ account }) {
      const portal = portalOf(account?.provider);
      if (!portal) return false;
      return CAN_ENTER[portal](rolesOf(account?.access_token))
        ? true
        : `/login-${portal}?error=WrongPortal`;
    },
    jwt({ token, account }) {
      if (account) {
        token.portal = portalOf(account.provider);
        token.roles = rolesOf(account.access_token);
        token.accessToken = account.access_token;
      }
      return token;
    },
    session({ session, token }) {
      return { ...session, portal: token.portal as Portal, roles: (token.roles ?? []) as Role[] };
    },
  },
});

declare module "next-auth" {
  interface Session {
    portal?: Portal;
    roles?: Role[];
  }
}
