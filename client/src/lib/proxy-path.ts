const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;

/** Upstream path for `segments`, or null if it could leave /api/v1 (absolute URL, `..`, odd chars). */
export function upstreamPath(segments: readonly string[]): string | null {
  const [api, version, ...rest] = segments;
  if (api !== "api" || version !== "v1" || rest.length === 0) return null;
  if (!rest.every((s) => SAFE_SEGMENT.test(s) && s !== "." && s !== "..")) return null;
  return `/api/v1/${rest.join("/")}`;
}

/**
 * Upstream path and query of a signed download (`/api/v1/files/<key>?exp=&sig=`, #398), or null.
 * The key is the storage key (`<tenant>/<project>/…/<sha256>`), so each segment must be plain; only
 * `exp` (digits) and `sig` (the API's hex HMAC) are passed on, nothing else from the request.
 */
export function signedFileUpstream(
  segments: readonly string[],
  query: URLSearchParams,
): string | null {
  if (segments.length === 0) return null;
  if (!segments.every((s) => SAFE_SEGMENT.test(s) && s !== "." && s !== "..")) return null;
  const exp = query.get("exp");
  const sig = query.get("sig");
  if (!exp || !/^\d{1,12}$/.test(exp) || !sig || !/^[0-9a-f]{64}$/.test(sig)) return null;
  return `/api/v1/files/${segments.join("/")}?exp=${exp}&sig=${sig}`;
}
