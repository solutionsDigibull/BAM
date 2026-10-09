import { notFound } from "next/navigation";

import { devAuthEnabled } from "@/lib/session";

import { ErrorStatesDemo } from "./demo";

export const metadata = { title: "API error states" };

// Dev-only gallery of every problem+json → UI mapping (E1.14). 404 unless SBAM_DEV_AUTH=1.
export default function ErrorStatesPage() {
  if (!devAuthEnabled()) notFound();
  return <ErrorStatesDemo />;
}
