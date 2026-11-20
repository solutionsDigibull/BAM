"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { adminMode, MASTERS_NAV } from "./nav";

const MODES = [
  { key: "admin", label: "Admin", href: "/admin/projects" },
  { key: "masters", label: "Admin Masters", href: MASTERS_NAV[0]!.href },
] as const;

/**
 * The Admin | Admin Masters switch in the admin header (#869). The mode is the path (/admin/masters...), so a
 * reload, a bookmark and the back button all agree. Two real links: reachable by Tab, activated by Enter, and the
 * current one is marked with aria-current. Same session: switching never signs in again.
 */
export function ModeToggle() {
  const mode = adminMode(usePathname());
  return (
    <nav className="mode-toggle" aria-label="Admin mode">
      {MODES.map((m) => (
        <Link
          key={m.key}
          href={m.href}
          className="mode-toggle-option"
          aria-current={m.key === mode ? "page" : undefined}
          data-active={m.key === mode}
        >
          {m.label}
        </Link>
      ))}
    </nav>
  );
}
