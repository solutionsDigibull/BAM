import "server-only";

/**
 * A correlation id for a failed sign-in, logged server-side and shown to the user, so support can
 * match the two (E2.5-T08). Only the error code is logged: never tokens or query strings.
 */
export function failureReference(portal: string, error: string | undefined): string | undefined {
  if (!error) return undefined;
  const reference = crypto.randomUUID();
  console.error(
    JSON.stringify({
      level: "ERROR",
      msg: "sign-in failed",
      portal,
      error,
      correlation_id: reference,
    }),
  );
  return reference;
}
