import { redirect } from "next/navigation";

import { landing } from "@/lib/landing";
import { isAdmin, isFde } from "@/lib/roles";
import { getSession } from "@/lib/session";

export default async function Home() {
  const session = await getSession();
  if (session && isAdmin(session.roles)) redirect(landing("admin", session.roles));
  if (session && isFde(session.roles)) redirect(landing("fde", session.roles));
  redirect("/login-fde");
}
