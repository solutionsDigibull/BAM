import { defineConfig } from "@playwright/test";

const port = 3917; // unusual on purpose so a dev server for another app isn't reused
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: { baseURL },
  // "keycloak" logs in through the real realm, so it needs a running stack (E2E_BASE_URL) and opts in
  // with E2E_KEYCLOAK=1 (#51). The default project keeps using the dev role cookie.
  projects: [
    { name: "default", testIgnore: /keycloak\.spec\.ts/ },
    ...(process.env.E2E_KEYCLOAK ? [{ name: "keycloak", testMatch: /keycloak\.spec\.ts/ }] : []),
  ],
  // Dev server with the dev-only role cookie enabled; set E2E_BASE_URL to test a running stack.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `pnpm exec next dev --port ${port}`,
        url: baseURL,
        env: { SBAM_DEV_AUTH: "1" },
        reuseExistingServer: false,
        timeout: 180_000,
      },
});
