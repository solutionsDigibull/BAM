import { MastersScreen } from "./masters-screen";

export const metadata = { title: "Masters" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default function MastersPage() {
  return <MastersScreen />;
}
