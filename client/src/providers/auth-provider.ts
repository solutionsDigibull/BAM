// Refine auth provider (E1.14). Sign-in, sign-out and portal guards live server-side (Auth.js +
// src/proxy.ts); this only tells Refine what to do when an API call comes back 401.
import type { AuthProvider } from "@refinedev/core";

const loginPath = () =>
  typeof window !== "undefined" && window.location.pathname.startsWith("/admin")
    ? "/login-admin"
    : "/login-fde";

export const authProvider: AuthProvider = {
  login: async () => ({ success: false, redirectTo: loginPath() }),
  // Sign-out is the shell's server action (clears the Auth.js and dev cookies); nothing calls this.
  logout: async () => ({ success: false, redirectTo: loginPath() }),
  // ponytail: server guards are authoritative; the client never decides who is signed in.
  check: async () => ({ authenticated: true }),
  onError: async (error: { statusCode?: number }) =>
    error?.statusCode === 401 ? { redirectTo: loginPath(), error: error as never } : {},
};
