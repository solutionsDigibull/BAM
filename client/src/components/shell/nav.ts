export type NavItem = { href: string; label: string };

export const ADMIN_NAV: NavItem[] = [
  { href: "/admin/projects", label: "Projects" },
  { href: "/admin/users", label: "Users & Roles" },
  { href: "/admin/registry", label: "Registry" },
  { href: "/admin/srim-defaults", label: "SRIM defaults" },
  { href: "/admin/token-caps", label: "Token caps" },
  { href: "/admin/tenants", label: "Tenants" },
];

// Admin Masters mode (#869): its own sidebar. Projects is the project list, Tenant masters the tenant-level masters.
export const MASTERS_NAV: NavItem[] = [
  { href: "/admin/masters", label: "Projects" },
  { href: "/admin/masters/tenant", label: "Tenant masters" },
];

export const MASTERS_PREFIX = "/admin/masters";

/** The admin portal has two modes, told apart by the path alone. */
export type AdminMode = "admin" | "masters";
export const adminMode = (pathname: string): AdminMode =>
  pathname === MASTERS_PREFIX || pathname.startsWith(`${MASTERS_PREFIX}/`) ? "masters" : "admin";

export const FDE_NAV: NavItem[] = [
  { href: "/projects", label: "My projects" },
  { href: "/registry", label: "Runtime registry" }, // read-only (E3.12)
];

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
