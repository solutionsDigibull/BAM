import { MastersScreen } from "../masters-screen";

export const metadata = { title: "Tenant masters" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default function TenantMastersPage() {
  return <MastersScreen />;
}
