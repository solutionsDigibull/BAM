// Silent refresh of the Keycloak tokens kept in the Auth.js JWT (E2.5). Pure apart from `post`.
// Keycloak rotates refresh tokens (#47: revokeRefreshToken, maxReuse 0), so the new refresh token
// must replace the old one every time; a failed refresh ends the session.

export const REFRESH_BEFORE_SECONDS = 60;

export type KeycloakTokens = {
  accessToken?: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: number; // seconds since epoch
  error?: "RefreshFailed";
};

type TokenResponse = {
  access_token?: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
};

export type Post = (body: URLSearchParams) => Promise<{ ok: boolean; json(): Promise<unknown> }>;

export function needsRefresh(tokens: KeycloakTokens, now: number): boolean {
  return !tokens.error && (tokens.expiresAt ?? 0) - REFRESH_BEFORE_SECONDS <= now;
}

/** New tokens from the refresh grant, or the same tokens marked `RefreshFailed` (and emptied). */
export async function refresh<T extends KeycloakTokens>(
  tokens: T,
  clientId: string,
  now: number,
  post: Post,
): Promise<T & KeycloakTokens> {
  const failed = {
    ...tokens,
    accessToken: undefined,
    refreshToken: undefined,
    error: "RefreshFailed" as const,
  };
  if (!tokens.refreshToken) return failed;
  try {
    const res = await post(
      new URLSearchParams({
        grant_type: "refresh_token",
        client_id: clientId,
        refresh_token: tokens.refreshToken,
      }),
    );
    if (!res.ok) return failed;
    const body = (await res.json()) as TokenResponse;
    if (!body.access_token || !body.refresh_token || !body.expires_in) return failed;
    return {
      ...tokens,
      accessToken: body.access_token,
      refreshToken: body.refresh_token, // rotated: the old one is already revoked
      idToken: body.id_token ?? tokens.idToken,
      expiresAt: now + body.expires_in,
      error: undefined,
    };
  } catch {
    return failed; // Keycloak unreachable: the session ends rather than using a stale token
  }
}
