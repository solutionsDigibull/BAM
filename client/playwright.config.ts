import { defineConfig } from "@playwright/test";

const port = 3917; // unusual on purpose so a dev server for another app isn't reused
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`;
const stack = Boolean(process.env.E2E_STACK);

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  // The real-stack scenarios share one stack (and its workers): one at a time keeps them stable.
  workers: stack ? 1 : undefined,
  use: {
    baseURL,
    // E15.1: what a failed run leaves behind (CI keeps report + traces + videos 14 days)
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  reporter: stack ? [["list"], ["html", { open: "never" }]] : "list",
  // "keycloak" logs in through the real realm, so it needs a running stack (E2E_BASE_URL) and opts in
  // with E2E_KEYCLOAK=1 (#51). The default project keeps using the dev role cookie.
  projects: [
    { name: "default", testIgnore: [/keycloak\.spec\.ts/, /stack\//] },
    ...(process.env.E2E_KEYCLOAK ? [{ name: "keycloak", testMatch: /keycloak\.spec\.ts/ }] : []),
    // E15.1 scenarios E01-E10 against a running stack (E2E_BASE_URL + E2E_STACK=1): real logins,
    // real workers. One retry for a flaky network; a failure on the retry still fails the run.
    ...(stack
      ? [{ name: "stack", testMatch: /stack\/.*\.spec\.ts/, retries: 1, timeout: 10 * 60_000 }]
      : []),
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
