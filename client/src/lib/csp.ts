// Content-Security-Policy with a per-request nonce (A-11, #441). Scripts run only with the nonce
// ('strict-dynamic' lets those scripts load their chunks). Styles keep 'unsafe-inline': antd's CSS-in-JS
// injects <style> and style="" at runtime, and style injection cannot run script (residual risk, #441).
export function buildCsp(nonce: string, dev = false): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:", // Monaco and the graph layout run in workers
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

export function newNonce(): string {
  return btoa(crypto.randomUUID());
}
