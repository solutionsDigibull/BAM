import { RegistryView } from "@/components/registry/registry-view";

export const metadata = { title: "Runtime registry" };

// FDE read-only view of the same platform-wide registry (GET /api/v1/registry, E3.12).
export default function FdeRuntimeRegistryPage() {
  return <RegistryView endpoint="registry" />;
}
