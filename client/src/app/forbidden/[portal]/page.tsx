import { Button } from "antd";
import Link from "next/link";

import { Forbidden } from "@/components/ui-kit";

export const metadata = { title: "Forbidden" };

// Rendered by src/proxy.ts (status 403) when the signed-in roles cannot use the requested portal.
export default async function ForbiddenPage({ params }: { params: Promise<{ portal: string }> }) {
  const admin = (await params).portal === "admin";
  return (
    <Forbidden
      hint={`The ${admin ? "Admin" : "FDE"} portal needs the ${admin ? "sbam-admin" : "sbam-fde"} role.`}
      extra={
        <Link href={admin ? "/projects" : "/admin/projects"}>
          <Button type="primary">Go to the {admin ? "FDE" : "Admin"} portal</Button>
        </Link>
      }
    />
  );
}
