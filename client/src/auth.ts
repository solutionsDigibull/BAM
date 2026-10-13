// Auth.js with Keycloak (E2.5 #51, from spike #31). Off unless KEYCLOAK_ISSUER and AUTH_SECRET are set.
// One provider per portal, each bound to its own public Keycloak client (PKCE S256 + state + nonce).
// Tokens live only in the encrypted httpOnly JWT cookie and on the server-side session object;
// /api/auth/session strips them (app/api/auth/[...nextauth]/route.ts), so no token reaches browser JS.
import NextAuth, { customFetch } from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

import { landing, type Portal } from "@/lib/landing";
import { isAdmin, isFde, parseRoles, type Role } from "@/lib/roles";
import { needsRefresh, refresh, type KeycloakTokens } from "@/lib/token-refresh";

export type { Portal };

const PORTAL_CLIENT: Record<Portal, string> = { admin: "sbam-admin", fde: "sbam-fde" };
const CAN_ENTER: Record<Portal, (roles: readonly Role[]) => boolean> = {
  admin: isAdmin,
  fde: isFde,
};

export const providerId = (portal: Portal) => `keycloak-${portal}`;
const portalOf = (provider: string | undefined): Portal | undefined =>
  provider === "keycloak-admin" ? "admin" : provider === "keycloak-fde" ? "fde" : undefined;

// The issuer is the browser-facing URL (it must match the tokens' `iss`). Inside Compose the server
// reaches Keycloak at another origin, so server-side requests to the issuer's origin are rewritten.
const ISSUER = process.env.KEYCLOAK_ISSUER ?? "";
const INTERNAL = process.env.KEYCLOAK_INTERNAL_URL;

export function internalUrl(url: string): string {
  if (!INTERNAL || !ISSUER) return url;
  const issuerOrigin = new URL(ISSUER).origin;
  return url.startsWith(issuerOrigin)
    ? INTERNAL.replace(/\/$/, "") + url.slice(issuerOrigin.length)
    : url;
}

const keycloakFetch: typeof fetch = (input, init) =>
  fetch(internalUrl(input instanceof Request ? input.url : input.toString()), init);

// Payload of a token just received from Keycloak's token endpoint; sbam-api verifies it again.
function rolesOf(accessToken: string | undefined): Role[] {
  if (!accessToken) return [];
  const payload = JSON.parse(Buffer.from(accessToken.split(".")[1] ?? "", "base64url").toString());
  return parseRoles((payload?.realm_access?.roles ?? []).join(","));
}

const tokenEndpoint = () => internalUrl(`${ISSUER}/protocol/openid-connect/token`);

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: (["admin", "fde"] as const).map((portal) =>
    Keycloak({
      id: providerId(portal),
      name: `Keycloak (${PORTAL_CLIENT[portal]})`,
      issuer: ISSUER,
      clientId: PORTAL_CLIENT[portal],
      clientSecret: "", // public client
      client: { token_endpoint_auth_method: "none" },
      checks: ["pkce", "state", "nonce"],
      [customFetch]: keycloakFetch,
    }),
  ),
  pages: { signIn: "/login-fde", error: "/login-fde" },
  callbacks: {
    // Wrong portal and no SBAM role are refused at sign-in: no session is created.
    signIn({ account }) {
      const portal = portalOf(account?.provider);
      if (!portal) return false;
      const roles = rolesOf(account?.access_token);
      if (roles.length === 0) return `/login-${portal}?error=NoRole`;
      return CAN_ENTER[portal](roles) ? true : `/login-${portal}?error=WrongPortal`;
    },
    async jwt({ token, account }) {
      if (account) {
        const portal = portalOf(account.provider);
        return {
          ...token,
          portal,
          roles: rolesOf(account.access_token),
          accessToken: account.access_token,
          refreshToken: account.refresh_token,
          idToken: account.id_token,
          expiresAt: account.expires_at,
          error: undefined,
        };
      }
      const now = Math.floor(Date.now() / 1000);
      if (!needsRefresh(token as KeycloakTokens, now)) return token;
      const portal = (token.portal as Portal | undefined) ?? "fde";
      const next = await refresh(token as KeycloakTokens, PORTAL_CLIENT[portal], now, (body) =>
        fetch(tokenEndpoint(), { method: "POST", body }),
      );
      // Roles can change in Keycloak; they take effect at the next refresh (<= 300 s).
      return next.error ? next : { ...next, roles: rolesOf(next.accessToken) };
    },
    session({ session, token }) {
      return {
        ...session,
        portal: token.portal as Portal,
        roles: (token.error ? [] : (token.roles ?? [])) as Role[],
        error: token.error as KeycloakTokens["error"],
        // Server-side only: stripped from /api/auth/session before it reaches the browser.
        accessToken: token.error ? undefined : (token.accessToken as string | undefined),
        idToken: token.idToken as string | undefined,
      };
    },
  },
});

/** Where this user should land after signing in through `portal`. */
export const landingFor = (portal: Portal, roles: readonly Role[]) => landing(portal, roles);

declare module "next-auth" {
  interface Session {
    portal?: Portal;
    roles?: Role[];
    error?: KeycloakTokens["error"];
    accessToken?: string;
    idToken?: string;
  }
}
