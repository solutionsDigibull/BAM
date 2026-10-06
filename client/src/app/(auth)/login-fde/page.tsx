import { devAuthEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";

export default function FdeLoginPage() {
  return <DevLogin portal="fde" enabled={devAuthEnabled()} />;
}
