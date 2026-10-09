const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;

/** Upstream path for `segments`, or null if it could leave /api/v1 (absolute URL, `..`, odd chars). */
export function upstreamPath(segments: readonly string[]): string | null {
  const [api, version, ...rest] = segments;
  if (api !== "api" || version !== "v1" || rest.length === 0) return null;
  if (!rest.every((s) => SAFE_SEGMENT.test(s) && s !== "." && s !== "..")) return null;
  return `/api/v1/${rest.join("/")}`;
}
