/**
 * What was typed on the login card, if it is fit to pass to Keycloak as `login_hint` (so its form arrives with the
 * field filled in). A username or an email: any visible text, up to 254 characters, with no whitespace or control
 * characters. Keycloak decides whether the account exists; this only keeps odd input out of the redirect.
 */
export function loginHint(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const value = raw.trim();
  return value.length > 0 && value.length <= 254 && !/[\s\p{Cc}]/u.test(value) ? value : undefined;
}
