import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.fn();
let devCookie: string | undefined;

vi.mock("server-only", () => ({}));
vi.mock("@/auth", () => ({ auth: () => auth() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => (devCookie ? { value: devCookie } : undefined) }),
}));

const { getSession } = await import("./session");

describe("getSession (E0.13)", () => {
  beforeEach(() => {
    auth.mockReset();
    devCookie = undefined;
  });
  afterEach(() => vi.unstubAllEnvs());

  it("returns Keycloak session roles when KEYCLOAK_ISSUER is set", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    auth.mockResolvedValue({ roles: ["sbam-fde"], portal: "fde" });
    expect(await getSession()).toEqual({ roles: ["sbam-fde"] });
  });

  it("falls back to the dev cookie when there is no Keycloak session", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    vi.stubEnv("SBAM_DEV_AUTH", "1");
    auth.mockResolvedValue(null);
    devCookie = "sbam-admin";
    expect(await getSession()).toEqual({ roles: ["sbam-admin"] });
  });

  it("does not call Auth.js when Keycloak is not configured", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "");
    expect(await getSession()).toBeNull();
    expect(auth).not.toHaveBeenCalled();
  });
});
