import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

const path = vi.hoisted(() => ({ current: "/admin/projects" }));
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/app/(auth)/actions", () => ({ signOut: vi.fn(), switchPortal: vi.fn() }));
vi.mock("@/components/ui-kit", () => ({
  NotificationCenter: () => <button type="button">Notifications</button>,
}));

import { ModeToggle } from "./mode-toggle";
import { ADMIN_NAV, MASTERS_NAV } from "./nav";
import { PortalShell } from "./portal-shell";

afterEach(() => {
  cleanup();
  path.current = "/admin/projects";
});

const current = () => screen.getByRole("link", { current: "page" }).textContent;

describe("Admin | Admin Masters mode (#869)", () => {
  test("test_mui_8_t01_the_toggle_follows_the_path_and_links_to_each_mode", () => {
    const { rerender } = render(<ModeToggle />);
    const nav = screen.getByRole("navigation", { name: "Admin mode" });
    expect(current()).toBe("Admin");
    expect(within(nav).getByRole("link", { name: "Admin" }).getAttribute("href")).toBe(
      "/admin/projects",
    );
    expect(within(nav).getByRole("link", { name: "Admin Masters" }).getAttribute("href")).toBe(
      "/admin/masters",
    );
    for (const p of [
      "/admin/masters",
      "/admin/masters/tenant",
      "/admin/masters/projects/0190f3a2-7b1c-7d4e-8f00-000000000001",
    ]) {
      path.current = p;
      rerender(<ModeToggle />);
      expect(current()).toBe("Admin Masters");
    }
    path.current = "/admin/users";
    rerender(<ModeToggle />);
    expect(current()).toBe("Admin");
  });

  test("test_mui_8_t02_the_sidebar_changes_per_mode", () => {
    const shell = () => (
      <PortalShell portal="admin" nav={ADMIN_NAV} canSwitch={false} user="a@b.test">
        <p>body</p>
      </PortalShell>
    );
    const side = (c: HTMLElement) => c.querySelector("#portal-nav") as HTMLElement;
    const labels = (c: HTMLElement) =>
      within(side(c))
        .getAllByRole("link")
        .map((l) => l.textContent)
        .filter((t) => t !== "");

    const admin = render(shell());
    expect(labels(admin.container)).toEqual(ADMIN_NAV.map((i) => i.label));
    expect(labels(admin.container)).not.toContain("Masters");
    cleanup();

    path.current = "/admin/masters/tenant";
    const masters = render(shell());
    expect(labels(masters.container)).toEqual(MASTERS_NAV.map((i) => i.label));
    expect(labels(masters.container)).toEqual(["Projects", "Tenant masters"]);
    expect(masters.container.querySelector(".ant-menu-item-selected")?.textContent).toBe(
      "Tenant masters",
    );
  });
});
