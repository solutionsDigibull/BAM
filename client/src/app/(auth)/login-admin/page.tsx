import { devAuthEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";

export default function AdminLoginPage() {
  return <DevLogin portal="admin" enabled={devAuthEnabled()} />;
}
