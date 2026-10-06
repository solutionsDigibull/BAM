import { Button } from "antd";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { FDE_NAV } from "@/components/shell/nav";
import { PortalShell } from "@/components/shell/portal-shell";
import { Forbidden } from "@/components/ui-kit";
import { isAdmin, isFde } from "@/lib/roles";
import { getSession } from "@/lib/session";

export default async function FdeLayout({ children }: { children: ReactNode }) {
  const session = await getSession();
  if (!session) redirect("/login-fde");
  if (!isFde(session.roles)) {
    return (
      <Forbidden
        hint="The FDE portal needs the sbam-fde role."
        extra={
          <Link href="/admin/projects">
            <Button type="primary">Go to the Admin portal</Button>
          </Link>
        }
      />
    );
  }
  return (
    <PortalShell portal="fde" nav={FDE_NAV} canSwitch={isAdmin(session.roles)}>
      {children}
    </PortalShell>
  );
}
