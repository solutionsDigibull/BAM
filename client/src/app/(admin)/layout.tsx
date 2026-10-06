import { Button } from "antd";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ADMIN_NAV } from "@/components/shell/nav";
import { PortalShell } from "@/components/shell/portal-shell";
import { Forbidden } from "@/components/ui-kit";
import { isAdmin, isFde } from "@/lib/roles";
import { getSession } from "@/lib/session";

// Server-side guard: wrong portal renders Forbidden and never renders (or fetches) the admin page.
export default async function AdminLayout({ children }: { children: ReactNode }) {
  const session = await getSession();
  if (!session) redirect("/login-admin");
  if (!isAdmin(session.roles)) {
    return (
      <Forbidden
        hint="The Admin portal needs the sbam-admin role."
        extra={
          <Link href="/projects">
            <Button type="primary">Go to the FDE portal</Button>
          </Link>
        }
      />
    );
  }
  return (
    <PortalShell portal="admin" nav={ADMIN_NAV} canSwitch={isFde(session.roles)}>
      {children}
    </PortalShell>
  );
}
