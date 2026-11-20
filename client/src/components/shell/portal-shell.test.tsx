import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@/app/(auth)/actions", () => ({ signOut: vi.fn(), switchPortal: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/projects" }));
vi.mock("@/components/ui-kit", () => ({ NotificationCenter: () => null }));

import { PortalShell } from "./portal-shell";

afterEach(cleanup);

describe("the portal switch in the header (#927)", () => {
  test("test_927_a_dual_role_admin_gets_a_switch_that_posts_the_fde_portal", () => {
    const { container } = render(
      <PortalShell portal="admin" nav={[]} canSwitch>
        <p>body</p>
      </PortalShell>,
    );
    const button = screen.getByRole("button", { name: /FDE portal/ });
    const form = button.closest("form")!;
    expect((form.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("fde");
    expect(container.querySelector('a[href="/projects"]')).toBeNull(); // no plain link that would keep the admin token
  });

  test("test_927_an_admin_only_user_gets_no_switch", () => {
    render(
      <PortalShell portal="admin" nav={[]} canSwitch={false}>
        <p>body</p>
      </PortalShell>,
    );
    expect(screen.queryByRole("button", { name: /FDE portal/ })).toBeNull();
  });

  test("test_927_the_fde_side_switch_posts_the_admin_portal", () => {
    render(
      <PortalShell portal="fde" nav={[]} canSwitch>
        <p>body</p>
      </PortalShell>,
    );
    const form = screen.getByRole("button", { name: /Admin portal/ }).closest("form")!;
    expect((form.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("admin");
  });
});
