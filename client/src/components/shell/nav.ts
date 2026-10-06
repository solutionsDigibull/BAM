export type NavItem = { href: string; label: string };

export const ADMIN_NAV: NavItem[] = [
  { href: "/admin/projects", label: "Projects" },
  { href: "/admin/masters", label: "Masters" },
  { href: "/admin/users", label: "Users & Roles" },
  { href: "/admin/registry", label: "Registry" },
  { href: "/admin/tenants", label: "Tenants" },
];

export const FDE_NAV: NavItem[] = [{ href: "/projects", label: "My projects" }];

// No "create Workflow" entry anywhere: SBAM generates the Workflow from the approved SOW.
export const projectNav = (base: string): NavItem[] => [
  { href: base, label: "Overview" },
  { href: `${base}/documents`, label: "Documents" },
  { href: `${base}/knowledge`, label: "Knowledge" },
  { href: `${base}/sow`, label: "SOW" },
  { href: `${base}/workflows`, label: "Workflows" },
];

export const workflowNav = (base: string): NavItem[] => [
  { href: base, label: "Overview" },
  { href: `${base}/isatvon`, label: "ISATVON" },
  { href: `${base}/build`, label: "Build & Test" },
  { href: `${base}/manifests`, label: "Manifests" },
  { href: `${base}/code`, label: "Code" },
  { href: `${base}/certification`, label: "Certification" },
  { href: `${base}/versions`, label: "Versions" },
];

/** Longest nav href that prefixes the path, so nested pages keep their parent highlighted. */
export function activeHref(items: NavItem[], pathname: string): string | undefined {
  return items
    .filter((i) => pathname === i.href || pathname.startsWith(`${i.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href;
}
