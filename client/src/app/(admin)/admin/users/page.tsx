import { UsersScreen } from "./users-screen";

export const metadata = { title: "Users & Roles" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default function UsersRolesPage() {
  return <UsersScreen />;
}
