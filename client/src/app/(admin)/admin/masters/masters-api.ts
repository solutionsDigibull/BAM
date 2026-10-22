import type {
  ImportMode,
  ImportReport,
  MasterDocumentOut,
  MasterStatus,
  MasterType,
} from "@/contracts";
import { API_BASE, toApiError, type ApiError } from "@/providers/data-provider";

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

export const MAX_WORKBOOK_BYTES = 15 * 1024 * 1024; // the server answers 413 above this too

/** Why a picked file can't be imported, or null (the server checks it all again). */
export function workbookProblem(file: { name: string; size: number }): string | null {
  if (!file.name.toLowerCase().endsWith(".xlsx")) return "Only .xlsx workbooks can be imported.";
  if (file.size > MAX_WORKBOOK_BYTES) return "The workbook is larger than 15 MB.";
  return null;
}

/** POST /admin/masters/import as multipart (Refine's custom() only sends JSON). */
export async function importWorkbook(file: File, mode: ImportMode): Promise<ImportReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  const res = await fetch(`${API_BASE}/admin/masters/import`, {
    method: "POST",
    body: form,
    headers: { accept: "application/json" },
    credentials: "same-origin",
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as ImportReport;
}

// --- master documents (E3.11) ----------------------------------------------------------------

export const DOCUMENTS = "admin/master-documents";
export const ROUTED_TYPES = ["ORGANIZATION", "RULES", "AGENTS", "SKILLS"] as const;
export const DOCUMENT_POLL_MS = 5_000;
export const MAX_DOCUMENT_BYTES = 15 * 1024 * 1024; // TD §7.3.2; the server answers 413 above it
// ponytail: parsing is #291; until it lands a CLEAN row stays PENDING. Rows older than this count as
// settled ("waiting for a loader") so an open tab stops polling. Drop the window once #291 parses.
export const PENDING_WINDOW_MS = 10 * 60_000;

/** True while a row can still change on its own: scan unfinished, parsing, or freshly queued. */
export const isActive = (d: MasterDocumentOut, now = Date.now()) =>
  d.scan_status == null ||
  d.parse_status === "PARSING" ||
  (d.parse_status === "PENDING" &&
    d.scan_status === "CLEAN" &&
    now - Date.parse(d.created_at) < PENDING_WINDOW_MS);

/** What to tell the Admin about a refused file, per status the upload can answer. */
export function uploadMessage(error: ApiError): string {
  if (error.statusCode === 413) return "Larger than 15 MB";
  if (error.statusCode === 415) return "File type not supported yet";
  if (error.problem?.code === "MALWARE") return "Quarantined: the file contains malware";
  return error.problem?.detail ?? error.problem?.title ?? error.message ?? "Upload failed";
}

/** POST /admin/master-documents (multipart; the server scans, routes and stores it). */
export async function uploadDocument(file: File): Promise<MasterDocumentOut> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/${DOCUMENTS}`, {
    method: "POST",
    body: form,
    headers: { accept: "application/json" },
    credentials: "same-origin",
  });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as MasterDocumentOut;
}
