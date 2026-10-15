import { MyProjectsScreen } from "./projects-screen";

export const metadata = { title: "My projects" };

// FDE-only: (fde)/layout.tsx renders Forbidden for anyone without the sbam-fde role before this page runs.
export default function MyProjectsPage() {
  return <MyProjectsScreen />;
}
