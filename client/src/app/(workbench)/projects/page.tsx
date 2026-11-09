import { Button } from "antd";
import Link from "next/link";
import { redirect } from "next/navigation";

import { signOut } from "@/app/(auth)/actions";
import { Forbidden } from "@/components/ui-kit";
import { isAdmin, isFde } from "@/lib/roles";
import { getSession, sessionToken } from "@/lib/session";

import { ProjectsLanding } from "./projects-landing";

export const metadata = { title: "My projects" };

// FDE-only. The proxy already guards /projects; the guard is kept here too (defence in depth), as in the FDE layout.
export default async function MyProjectsPage() {
  const session = await getSession();
  if (!session) redirect("/login-fde");
  if (!isFde(session.roles)) {
    return (
      <Forbidden
        hint="The FDE portal needs the sbam-fde role."
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
    <ProjectsLanding
      user={typeof token?.email === "string" ? token.email : "FDE"}
      signOut={signOut}
    />
  );
}
