import { MastersHome } from "./masters-home";

export const metadata = { title: "Admin Masters" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default function MastersHomePage() {
  return <MastersHome />;
}
