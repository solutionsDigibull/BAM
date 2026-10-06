"use client";

import { Menu, Typography } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { activeHref, type NavItem } from "./nav";

/** Horizontal sub-navigation for a project or workflow. `title` is rendered as text, never HTML. */
export function SectionNav({ title, items }: { title: string; items: NavItem[] }) {
  const selected = activeHref(items, usePathname());
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Title level={4} ellipsis={{ tooltip: title }} style={{ margin: 0 }}>
        {title}
      </Typography.Title>
      <Menu
        mode="horizontal"
        selectedKeys={selected ? [selected] : []}
        items={items.map((i) => ({ key: i.href, label: <Link href={i.href}>{i.label}</Link> }))}
        style={{ background: "transparent" }}
      />
    </div>
  );
}
