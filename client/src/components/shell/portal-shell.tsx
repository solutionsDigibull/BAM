"use client";

import { MenuOutlined, MoonOutlined, SunOutlined } from "@ant-design/icons";
import { Button, ConfigProvider, Layout, Menu, theme as antdTheme, type ThemeConfig } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { signOut, switchPortal } from "@/app/(auth)/actions";
import { NotificationCenter } from "@/components/ui-kit";

import { setAdminTheme, useAdminTheme } from "./admin-theme";
import logoDark from "./assets/bullm-logo-dark.png";
import logoLight from "./assets/bullm-logo-light.png";
import { ModeToggle } from "./mode-toggle";
import { activeHref, adminMode, MASTERS_NAV, type NavItem } from "./nav";
import "./portal-shell.css";

// The switch signs in again through the other portal's Keycloak client (#927): its token is the only one that portal's API accepts.
const PORTAL = {
  admin: { name: "Admin", other: { portal: "fde", label: "FDE portal" } },
  fde: { name: "FDE", other: { portal: "admin", label: "Admin portal" } },
} as const;

// The approved Admin Masters look (#785): white sider and header, BuLLM blue for the selection.
const LIGHT = {
  bar: "#ffffff",
  line: "#dce3ee",
  body: "#f6f8fb",
  pick: "#f2f9fd",
  picked: "#006c99",
};
const DARK = {
  bar: "#101e32",
  line: "#22395c",
  body: "#0b1526",
  pick: "#1c2e51",
  picked: "#5b83f2",
};

function shellTheme(dark: boolean): ThemeConfig {
  const c = dark ? DARK : LIGHT;
  return {
    algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    // Dark: the navy surfaces of the approved style.css instead of antd's near-black defaults.
    token: dark
      ? {
          colorPrimary: "#5b83f2",
          colorBgLayout: c.body,
          colorBgContainer: c.bar,
          colorBgElevated: "#142540",
          colorBorder: "#2e4a73",
          colorBorderSecondary: c.line,
        }
      : { colorPrimary: "#006c99" },
    components: {
      // a theme="light" Sider reads lightSiderBg, not siderBg
      Layout: { headerBg: c.bar, lightSiderBg: c.bar, bodyBg: c.body, headerHeight: 64 },
      Menu: {
        itemBg: c.bar,
        itemHeight: 41,
        itemBorderRadius: 8,
        itemMarginInline: 0,
        itemSelectedBg: c.pick,
        itemSelectedColor: c.picked,
        activeBarBorderWidth: 0,
      },
    },
  };
}

export function PortalShell({
  portal,
  nav: portalNav,
  canSwitch,
  user,
  children,
}: {
  portal: keyof typeof PORTAL;
  nav: NavItem[];
  canSwitch: boolean;
  /** Who is signed in (email or name), for the account menu. */
  user?: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  // Admin Masters mode swaps the sidebar (#869); the FDE pages and the Admin mode keep the nav they were given.
  const nav = portal === "admin" && adminMode(pathname) === "masters" ? MASTERS_NAV : portalNav;
  const selected = activeHref(nav, pathname);
  const { name, other } = PORTAL[portal];
  const stored = useAdminTheme();
  // The theme toggle belongs to the admin portal; the FDE pages that share this shell stay light.
  const dark = portal === "admin" && stored === "dark";
  // Below the lg breakpoint the sider hides; antd's own trigger is an unlabelled span, so use a real button.
  const [collapsed, setCollapsed] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const title = nav.find((i) => i.href === selected)?.label ?? name;

  // The shared ui-kit tables read CSS variables (ui-kit/grid.tsx); on <html> they also reach drawers
  // and modals, which render outside the shell.
  useEffect(() => {
    const root = document.documentElement;
    if (dark) root.dataset.sbamTheme = "dark";
    else delete root.dataset.sbamTheme;
    return () => {
      delete root.dataset.sbamTheme;
    };
  }, [dark]);

  return (
    <ConfigProvider theme={shellTheme(dark)}>
      <Layout
        className="portal-shell"
        data-theme={dark ? "dark" : "light"}
        style={{ minHeight: "100vh" }}
      >
        <Layout.Sider
          id="portal-nav"
          className="portal-sider"
          width={216}
          theme="light"
          breakpoint="lg"
          collapsedWidth={0}
          trigger={null}
          collapsed={collapsed}
          onBreakpoint={(broken) => {
            setNarrow(broken);
            setCollapsed(broken);
          }}
        >
          <Link href={nav[0]?.href ?? "/"} className="portal-brand" aria-label="BuLLM home">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={(dark ? logoDark : logoLight).src}
              alt="BuLLM"
              className="portal-logo"
              width={177}
              height={46}
            />
          </Link>
          <div className="portal-nav-label">{name} workspace</div>
          <Menu
            mode="inline"
            selectedKeys={selected ? [selected] : []}
            items={nav.map((item) => ({
              key: item.href,
              label: (
                <Link href={item.href} title={item.label}>
                  {item.label}
                </Link>
              ),
            }))}
          />
        </Layout.Sider>
        {/* minWidth 0 lets the horizontal section menus collapse into "…" instead of widening the page. */}
        <Layout style={{ minWidth: 0 }}>
          <Layout.Header className="portal-header">
            {narrow && (
              <Button
                type="text"
                icon={<MenuOutlined />}
                aria-label={collapsed ? "Open navigation" : "Close navigation"}
                aria-expanded={!collapsed}
                aria-controls="portal-nav"
                onClick={() => setCollapsed((c) => !c)}
              />
            )}
            <span className="portal-title">{title}</span>
            {portal === "admin" && <ModeToggle />}
            <div className="portal-header-right">
              {portal === "admin" && (
                <Button
                  type="text"
                  icon={dark ? <SunOutlined /> : <MoonOutlined />}
                  aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
                  aria-pressed={dark}
                  title={dark ? "Switch to light mode" : "Switch to dark mode"}
                  onClick={() => setAdminTheme(dark ? "light" : "dark")}
                />
              )}
              <NotificationCenter />
              <AccountMenu user={user} other={canSwitch ? other : null} />
            </div>
          </Layout.Header>
          <Layout.Content style={{ padding: 24 }}>{children}</Layout.Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}

/** The avatar and its menu: who is signed in, the other portal (when allowed) and Sign out. */
function AccountMenu({
  user,
  other,
}: {
  user?: string;
  other: { portal: string; label: string } | null;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const initial = (user?.trim()[0] ?? "U").toUpperCase();

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div
      className="portal-account"
      ref={box}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) close(true);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="portal-avatar"
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {initial}
      </button>
      {open && (
        <div className="portal-menu" role="menu" aria-label="Account">
          <p className="portal-menu-who">Signed in as {user ?? "unknown user"}</p>
          {other && (
            <form action={switchPortal}>
              <input type="hidden" name="portal" value={other.portal} />
              <button type="submit" role="menuitem" className="portal-menu-item">
                {other.label}
              </button>
            </form>
          )}
          <form action={signOut}>
            <button type="submit" role="menuitem" className="portal-menu-item">
              Sign out
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
