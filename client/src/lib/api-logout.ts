/**
 * Tell sbam-api that this session signed out (A-32, #458), so a copy of its access token stops
 * working now and not when the token expires. Best effort and short: signing out must never hang
 * or fail because the API is slow or down, and the Keycloak end-session redirect follows either way.
 */
export const LOGOUT_TIMEOUT_MS = 3000;

export async function revokeApiSession(
  accessToken: string | undefined,
  apiUrl: string,
  post: typeof fetch = fetch,
): Promise<boolean> {
  if (!accessToken) return false;
  try {
    const response = await post(`${apiUrl}/api/v1/auth/logout`, {
      method: "POST",
      headers: { authorization: `Bearer ${accessToken}` },
      signal: AbortSignal.timeout(LOGOUT_TIMEOUT_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}
