import { redirect } from "next/navigation";

import { isAdmin, isFde } from "@/lib/roles";
import { getSession } from "@/lib/session";

export default async function Home() {
  const session = await getSession();
  if (session && isAdmin(session.roles)) redirect("/admin/projects");
  if (session && isFde(session.roles)) redirect("/projects");
  redirect("/login-fde");
}
