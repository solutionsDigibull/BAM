import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/admin/users/42" }));
vi.mock("@/app/(auth)/actions", () => ({ signOut: vi.fn(), switchPortal: vi.fn() }));
vi.mock("@/components/ui-kit", () => ({
  NotificationCenter: () => <button type="button">Notifications</button>,
}));

import { ADMIN_THEME_KEY, setAdminTheme } from "./admin-theme";
import { ADMIN_NAV } from "./nav";
import { PortalShell } from "./portal-shell";

const shell = (props: Partial<Parameters<typeof PortalShell>[0]> = {}) =>
  render(
    <PortalShell portal="admin" nav={ADMIN_NAV} canSwitch user="asha@tenant-a.test" {...props}>
      <p>page body</p>
    </PortalShell>,
  );

const realMatchMedia = window.matchMedia;

beforeEach(() => {
  window.localStorage.clear();
  act(() => setAdminTheme("light"));
});

afterEach(() => {
  window.matchMedia = realMatchMedia;
  vi.restoreAllMocks();
});

describe("the admin shell in the approved BuLLM look (#785)", () => {
  test("test_mui_6_t01_logo_section_label_nav_and_the_active_item_as_title", () => {
    const { container } = shell();
    expect(screen.getByRole("img", { name: "BuLLM" })).toBeTruthy();
    expect(screen.getByText("Admin workspace")).toBeTruthy();
    const nav = container.querySelector("#portal-nav") as HTMLElement;
    for (const item of ADMIN_NAV) {
      expect(within(nav).getByRole("link", { name: item.label })).toBeTruthy();
    }
    // the nested page keeps its parent selected, and the header names it
    expect(container.querySelector(".ant-menu-item-selected")?.textContent).toBe("Users & Roles");
    expect(container.querySelector(".portal-title")?.textContent).toBe("Users & Roles");
    expect(screen.getByText("page body")).toBeTruthy();
  });

  test("test_mui_6_t02_theme_toggle_switches_persists_and_survives_storage_that_throws", () => {
    const { container } = shell();
    const root = () => container.querySelector(".portal-shell") as HTMLElement;
    expect(root().dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    expect(root().dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(ADMIN_THEME_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeTruthy();

    // storage refusing reads and writes (private mode): the toggle still works, nothing throws
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    fireEvent.click(screen.getByRole("button", { name: "Switch to light mode" }));
    expect(root().dataset.theme).toBe("light");
  });

  test("test_mui_6_t02_the_fde_portal_has_no_theme_toggle_and_stays_light", () => {
    act(() => setAdminTheme("dark"));
    const { container } = shell({
      portal: "fde",
      nav: [{ href: "/projects", label: "My projects" }],
    });
    expect(screen.queryByRole("button", { name: /Switch to (dark|light) mode/ })).toBeNull();
    expect((container.querySelector(".portal-shell") as HTMLElement).dataset.theme).toBe("light");
    expect(screen.getByText("FDE workspace")).toBeTruthy();
  });

  test("test_mui_6_t03_account_menu_shows_who_the_portal_switch_and_sign_out", () => {
    shell();
    const avatar = screen.getByRole("button", { name: "Account menu" });
    expect(avatar.textContent).toBe("A");
    expect(avatar.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(avatar);
    const menu = screen.getByRole("menu", { name: "Account" });
    expect(within(menu).getByText("Signed in as asha@tenant-a.test")).toBeTruthy();
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["FDE portal", "Sign out"]);
    // #927: the switch posts the other portal to switchPortal, it is not a plain link
    const form = items[0]!.closest("form")!;
    expect((form.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("fde");
    expect(items[0]?.getAttribute("href")).toBeNull();
    expect(items[1]?.closest("form")).toBeTruthy(); // posts the signOut action
    // Escape closes it and gives the focus back to the avatar
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Account" })).toBeNull();
    expect(document.activeElement).toBe(avatar);
  });

  test("test_mui_6_t03_without_the_other_role_there_is_no_portal_switch", () => {
    shell({ canSwitch: false });
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    const items = within(screen.getByRole("menu", { name: "Account" })).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Sign out"]);
  });

  test("test_mui_6_t04_below_lg_a_labelled_hamburger_opens_the_nav", () => {
    window.matchMedia = ((query: string) => ({
      ...realMatchMedia(query),
      matches: query.includes("max-width"), // every "below <breakpoint>" query matches: a phone
    })) as typeof window.matchMedia;
    shell();
    const burger = screen.getByRole("button", { name: "Open navigation" });
    expect(burger.getAttribute("aria-controls")).toBe("portal-nav");
    expect(burger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(burger);
    expect(screen.getByRole("button", { name: "Close navigation" })).toBeTruthy();
  });
});

describe("the portal switch (#927) in the account menu", () => {
  test("test_927_the_fde_side_switch_posts_the_admin_portal", () => {
    shell({ portal: "fde", nav: [{ href: "/projects", label: "My projects" }] });
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    const form = screen.getByRole("menuitem", { name: /Admin portal/ }).closest("form")!;
    expect((form.querySelector('input[name="portal"]') as HTMLInputElement).value).toBe("admin");
  });

  test("test_927_an_admin_only_user_gets_no_switch", () => {
    shell({ canSwitch: false });
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.queryByRole("menuitem", { name: /FDE portal/ })).toBeNull();
  });
});
