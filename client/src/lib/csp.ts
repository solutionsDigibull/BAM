// Content-Security-Policy with a per-request nonce (A-11, #441). Scripts run only with the nonce
// ('strict-dynamic' lets those scripts load their chunks). Styles keep 'unsafe-inline': antd's CSS-in-JS
// injects <style> and style="" at runtime, and style injection cannot run script (residual risk, #441).
//
// Uploads go straight to tusd. Deployed, it sits behind the portal's own origin (/files/), so
// connect-src 'self' covers it. In local development tusd is its own origin (localhost:1080 by
// default), so only then connect-src also allows that origin: never a path, never a wildcard (#568).
export function buildCsp(nonce: string, dev = false, uploadUrl?: string): string {
  const upload = dev ? origin(uploadUrl) : null;
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${upload ? ` ${upload}` : ""}`,
    "worker-src 'self' blob:", // Monaco and the graph layout run in workers
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

/** The tus endpoint's origin (scheme, host, port), or null for anything that is not an http(s) URL. */
function origin(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.origin : null;
  } catch {
    return null;
  }
}

export function newNonce(): string {
  return btoa(crypto.randomUUID());
}
