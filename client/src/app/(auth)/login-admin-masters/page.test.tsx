import { describe, expect, test, vi } from "vitest";

vi.mock("../_login/bullm-login", () => ({ BullmLogin: () => null }));
vi.mock("../failure", () => ({ failureReference: () => undefined }));
vi.mock("@/lib/session", () => ({ devAuthEnabled: () => false, keycloakEnabled: () => true }));

import { safeCallback } from "@/lib/landing";

import AdminMastersLoginPage from "./page";

const props = async (searchParams: { callbackUrl?: string; error?: string }) =>
  (await AdminMastersLoginPage({ searchParams: Promise.resolve(searchParams) })).props;

describe("/login-admin-masters (#869)", () => {
  test("test_mui_8_t05_same_admin_login_landing_on_admin_masters_by_default", async () => {
    const p = await props({});
    expect(p.portal).toBe("admin"); // same Keycloak client and sbam-admin role as /login-admin
    expect(p.callbackUrl).toBe("/admin/masters");
    expect(safeCallback(p.callbackUrl, "admin")).toBe("/admin/masters");
  });

  test("test_mui_8_t05_an_explicit_callback_wins", async () => {
    expect((await props({ callbackUrl: "/admin/masters/tenant" })).callbackUrl).toBe(
      "/admin/masters/tenant",
    );
  });
});
