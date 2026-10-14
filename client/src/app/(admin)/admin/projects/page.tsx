import { ProjectsScreen } from "./projects-screen";

export const metadata = { title: "Projects" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default function ProjectsPage() {
  return <ProjectsScreen />;
}
