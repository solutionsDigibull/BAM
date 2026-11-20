import { afterEach, describe, expect, test, vi } from "vitest";

const { signIn, redirect, state } = vi.hoisted(() => ({
  signIn: vi.fn(),
  redirect: vi.fn((url: string) => {
    throw new Error(`NEXT_REDIRECT ${url}`);
  }),
  state: { keycloak: true },
}));

vi.mock("@/auth", () => ({ signIn, signOut: vi.fn(), providerId: (p: string) => `keycloak-${p}` }));
vi.mock("next/navigation", () => ({ redirect, notFound: vi.fn() }));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/api-logout", () => ({ revokeApiSession: vi.fn() }));
vi.mock("@/lib/session", () => ({
  DEV_ROLES_COOKIE: "sbam_dev_roles",
  devAuthEnabled: () => !state.keycloak,
  keycloakEnabled: () => state.keycloak,
  logoutHint: vi.fn(),
}));

import { switchPortal } from "./actions";

const form = (portal: string) => {
  const f = new FormData();
  f.set("portal", portal);
  return f;
};

afterEach(() => {
  vi.clearAllMocks();
  state.keycloak = true;
});

describe("portal switch for a user with both roles (#927)", () => {
  test("test_927_admin_to_fde_signs_in_again_through_the_fde_client", async () => {
    await switchPortal(form("fde"));
    // a fresh sign-in through keycloak-fde: the session then carries a token with azp=sbam-fde
    expect(signIn).toHaveBeenCalledWith("keycloak-fde", { redirectTo: "/projects" });
    expect(redirect).not.toHaveBeenCalled();
  });

  test("test_927_fde_to_admin_signs_in_again_through_the_admin_client", async () => {
    await switchPortal(form("admin"));
    expect(signIn).toHaveBeenCalledWith("keycloak-admin", { redirectTo: "/admin/projects" });
  });

  test("test_927_an_unknown_portal_value_goes_to_the_fde_portal", async () => {
    await switchPortal(form("../evil"));
    expect(signIn).toHaveBeenCalledWith("keycloak-fde", { redirectTo: "/projects" });
  });

  test("test_927_dev_sign_in_has_no_tokens_and_just_navigates", async () => {
    state.keycloak = false;
    await expect(switchPortal(form("fde"))).rejects.toThrow("NEXT_REDIRECT /projects");
    expect(signIn).not.toHaveBeenCalled();
  });
});
