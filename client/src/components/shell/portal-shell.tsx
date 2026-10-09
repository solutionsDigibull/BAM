"use client";

import { LogoutOutlined, MenuOutlined, SwapOutlined } from "@ant-design/icons";
import { Button, Flex, Layout, Menu, Tag, Typography } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { signOut } from "@/app/(auth)/actions";
import { NotificationBell } from "@/components/ui-kit";

import { activeHref, type NavItem } from "./nav";

const PORTAL = {
  admin: { name: "Admin", other: { href: "/projects", label: "FDE portal" } },
  fde: { name: "FDE", other: { href: "/admin/projects", label: "Admin portal" } },
} as const;

export function PortalShell({
  portal,
  nav,
  canSwitch,
  children,
}: {
  portal: keyof typeof PORTAL;
  nav: NavItem[];
  canSwitch: boolean;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const selected = activeHref(nav, pathname);
  const { name, other } = PORTAL[portal];
  // Below the lg breakpoint the sider hides; antd's own trigger is an unlabelled span, so use a real button.
  const [collapsed, setCollapsed] = useState(false);
  const [narrow, setNarrow] = useState(false);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider
        id="portal-nav"
        width={232}
        breakpoint="lg"
        collapsedWidth={0}
        trigger={null}
        collapsed={collapsed}
        onBreakpoint={(broken) => {
          setNarrow(broken);
          setCollapsed(broken);
        }}
      >
        <Flex align="center" gap={8} style={{ height: 64, padding: "0 20px" }}>
          <Typography.Text strong style={{ color: "#fff", fontSize: 18, letterSpacing: 0.5 }}>
            SBAM
          </Typography.Text>
          <Tag color="geekblue" bordered={false}>
            {name}
          </Tag>
        </Flex>
        <Menu
          theme="dark"
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
        <Layout.Header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 8,
            padding: "0 24px",
            borderBottom: "1px solid #eef0f4",
          }}
        >
          {narrow && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              aria-label={collapsed ? "Open navigation" : "Close navigation"}
              aria-expanded={!collapsed}
              aria-controls="portal-nav"
              onClick={() => setCollapsed((c) => !c)}
              style={{ marginRight: "auto" }}
            />
          )}
          {canSwitch && (
            <Link href={other.href}>
              <Button icon={<SwapOutlined />}>{other.label}</Button>
            </Link>
          )}
          <NotificationBell />
          <form action={signOut}>
            <Button type="text" htmlType="submit" icon={<LogoutOutlined />}>
              Sign out
            </Button>
          </form>
        </Layout.Header>
        <Layout.Content style={{ padding: 24 }}>{children}</Layout.Content>
      </Layout>
    </Layout>
  );
}
