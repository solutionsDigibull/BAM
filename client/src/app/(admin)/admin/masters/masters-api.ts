import type { MasterStatus, MasterType } from "@/contracts";
import { API_BASE, toApiError } from "@/providers/data-provider";

export const LAYOUT_URL = "admin/master-types";
export const FILETYPES_URL = "admin/filetypes";
export const entriesResource = (type: MasterType) => `admin/masters/${type}`;

// The screen's tabs; Tooling has no screen yet (docs/reference/ui/README.md, open point on #65).
export const SCREEN_TYPES: MasterType[] = ["ORGANIZATION", "RULES", "AGENTS", "SKILLS"];

export const STATUS_COLOR: Record<MasterStatus, string> = {
  PROPOSED: "blue",
  APPROVED: "green",
  ILLUSTRATIVE: "default",
  RETIRED: "red",
};

export const emptyMessage = (tab: string) =>
  `No ${tab} entries yet. Import the BAM 0.2 workbook or download the template to start one.`;

/** A cell as plain text: never HTML, objects (the RAG Reference) as their JSON. */
export const cellText = (value: unknown): string =>
  value === null || value === undefined
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

const fileName = (res: Response, fallback: string) =>
  /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallback;

/** Downloads a file through the proxy (the session token stays server-side, so no plain link). */
export async function download(path: string, fallback: string): Promise<string> {
  const res = await fetch(`${API_BASE}/${path}`);
  if (!res.ok) throw await toApiError(res);
  const name = fileName(res, fallback);
  const url = URL.createObjectURL(await res.blob());
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  link.click();
  URL.revokeObjectURL(url);
  return name;
}
