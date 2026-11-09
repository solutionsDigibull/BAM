import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Button } from "antd";

import { signOut } from "@/app/(auth)/actions";
import { Forbidden } from "@/components/ui-kit";
import { isAdmin, isFde, isUuid } from "@/lib/roles";
import { getSession, sessionToken } from "@/lib/session";

import { WorkbenchClient } from "./workbench-client";

export const metadata = { title: "Workbench" };

// The proxy already guards /projects/*; this keeps the guard here too (defence in depth), like the FDE layout.
export default async function WorkbenchPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  if (!isUuid(projectId)) notFound();
  const session = await getSession();
  if (!session) redirect("/login-fde");
  if (!isFde(session.roles)) {
    return (
      <Forbidden
        hint="The workbench needs the sbam-fde role."
        extra={
          isAdmin(session.roles) ? (
            <Link href="/admin/projects">
              <Button type="primary">Go to the Admin portal</Button>
            </Link>
          ) : undefined
        }
      />
    );
  }
  const token = await sessionToken();
  return (
    <WorkbenchClient
      projectId={projectId}
      user={typeof token?.email === "string" ? token.email : "FDE"}
      signOut={signOut}
    />
  );
}
