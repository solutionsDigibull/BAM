import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getToken = vi.fn();
let devCookie: string | undefined;

vi.mock("server-only", () => ({}));
vi.mock("next-auth/jwt", () => ({ getToken: (...a: unknown[]) => getToken(...a) }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => (devCookie ? { value: devCookie } : undefined) }),
  headers: async () => new Headers(),
}));

const { getSession } = await import("./session");

describe("getSession (E0.13)", () => {
  beforeEach(() => {
    getToken.mockReset();
    vi.stubEnv("AUTH_SECRET", "test-secret");
    devCookie = undefined;
  });
  afterEach(() => vi.unstubAllEnvs());

  it("returns Keycloak session roles when KEYCLOAK_ISSUER is set", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    getToken.mockResolvedValue({ roles: ["sbam-fde"], portal: "fde" });
    expect(await getSession()).toEqual({ roles: ["sbam-fde"] });
  });

  it("e2_5 treats a session whose refresh failed as signed out", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    getToken.mockResolvedValue({ roles: ["sbam-fde"], error: "RefreshFailed" });
    expect(await getSession()).toBeNull();
  });

  it("e2_5 reads the cookie without running Auth.js callbacks (no refresh while rendering)", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    getToken.mockResolvedValue({ roles: ["sbam-admin"] });
    expect(await getSession()).toEqual({ roles: ["sbam-admin"] });
    expect(getToken).toHaveBeenCalledWith(expect.objectContaining({ secret: "test-secret" }));
  });

  it("falls back to the dev cookie when there is no Keycloak session", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "http://kc.test/realms/bullm");
    vi.stubEnv("SBAM_DEV_AUTH", "1");
    getToken.mockResolvedValue(null);
    devCookie = "sbam-admin";
    expect(await getSession()).toEqual({ roles: ["sbam-admin"] });
  });

  it("does not call Auth.js when Keycloak is not configured", async () => {
    vi.stubEnv("KEYCLOAK_ISSUER", "");
    expect(await getSession()).toBeNull();
    expect(getToken).not.toHaveBeenCalled();
  });
});
