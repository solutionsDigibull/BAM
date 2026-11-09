import type { NextConfig } from "next";

// The script-src policy needs a per-request nonce, so it is set by src/proxy.ts (src/lib/csp.ts); these are static.
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Turbopack's dev file cache makes `next dev` recompile after every request on a Docker Desktop (Windows) bind
  // mount, and every rebuild reloads the open page, so a form could not be filled in (#719). Dev only: `next build`
  // keeps its own cache setting. Webpack does not loop; this keeps Turbopack and its speed.
  experimental: { turbopackFileSystemCacheForDev: false },
  headers: async () => [{ source: "/:path*", headers: SECURITY_HEADERS }],
};

export default nextConfig;
