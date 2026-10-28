const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;

/** Upstream path for `segments`, or null if it could leave /api/v1 (absolute URL, `..`, odd chars). */
export function upstreamPath(segments: readonly string[]): string | null {
  const [api, version, ...rest] = segments;
  if (api !== "api" || version !== "v1" || rest.length === 0) return null;
  if (!rest.every((s) => SAFE_SEGMENT.test(s) && s !== "." && s !== "..")) return null;
  return `/api/v1/${rest.join("/")}`;
}

const FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const FILE_TYPE = /^[a-z]+\/[a-z0-9.+-]+(; charset=utf-8)?$/;

/**
 * Upstream path and query of a signed download (`/api/v1/files/<key>?exp=&sig=[&name=&type=]`, #398,
 * #402), or null. The key is the storage key (`<tenant>/<project>/…/<sha256>`), so each segment must
 * be plain. Only `exp` (digits), `sig` (the API's hex HMAC) and, together, `name` (a plain file name)
 * and `type` (a media type) are passed on, nothing else from the request: the API checks that they
 * are the ones that were signed.
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
  const name = query.get("name");
  const type = query.get("type");
  if ((name === null) !== (type === null)) return null; // both or neither
  const forwarded = new URLSearchParams({ exp, sig });
  if (name !== null && type !== null) {
    if (!FILE_NAME.test(name) || !FILE_TYPE.test(type)) return null;
    forwarded.set("name", name);
    forwarded.set("type", type);
  }
  return `/api/v1/files/${segments.join("/")}?${forwarded.toString()}`;
}
