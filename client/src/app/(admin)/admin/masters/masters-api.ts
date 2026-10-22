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

export const versionsUrl = (type: MasterType, id: string) =>
  `${entriesResource(type)}/${id}/versions`;

/** The payload field holding the `Status: <value>` fragment (TD §7.3.1). */
export const statusField = (type: MasterType) => (type === "ORGANIZATION" ? "misc_audit" : "misc");

// Mirrors sbam_contracts.masters.extract_status: only a trailing `Status: X` counts.
const TRAILING_STATUS = /(^|[\s;])Status:\s*\S+?\.?\s*$/i;
const title = (s: MasterStatus) => s[0] + s.slice(1).toLowerCase();

/** Misc text with its trailing status replaced (or added): what a status-chip edit saves. */
export function withStatus(misc: string, status: MasterStatus): string {
  const rest = misc
    .trim()
    .replace(TRAILING_STATUS, "$1")
    .trim()
    .replace(/[;\s]+$/, "");
  if (!rest) return `Status: ${title(status)}`;
  return `${rest}${rest.endsWith(".") ? "" : ";"} Status: ${title(status)}`; // "TBD. Status: X"
}

export const RAG_KEYS = [
  "Example",
  "Links / Path",
  "Entity / Node Type",
  "Security / Access Level",
  "Refresh Frequency",
  "ID Field",
  "Parent Entity",
] as const;

export const RAG_TEMPLATE = JSON.stringify(
  Object.fromEntries(RAG_KEYS.map((k) => [k, ""])),
  null,
  2,
);

/** Why a RAG Reference cell is invalid, or null: empty, or a JSON object with exactly the 7 keys. */
export function ragError(text: string): string | null {
  if (!text.trim()) return null;
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return "RAG Reference is not valid JSON";
  }
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return "RAG Reference must be a JSON object";
  const keys = Object.keys(value);
  const missing = RAG_KEYS.filter((k) => !keys.includes(k));
  const extra = keys.filter((k) => !(RAG_KEYS as readonly string[]).includes(k));
  if (missing.length) return `Missing key${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}`;
  if (extra.length) return `Unknown key${extra.length > 1 ? "s" : ""}: ${extra.join(", ")}`;
  if (Object.values(value).some((v) => typeof v !== "string")) return "Every value must be text";
  return null;
}
