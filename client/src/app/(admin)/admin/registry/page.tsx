import { RegistryView } from "@/components/registry/registry-view";

export const metadata = { title: "Runtime registry" };

export default function RuntimeRegistryPage() {
  return <RegistryView endpoint="admin/registry" />;
}
