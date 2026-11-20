// Pure helpers of the masters grids (#783): search, history, facets, chips, CSV. No React here.
import type { MasterEntry, MasterTypeInfo } from "@/contracts";

import { cellText } from "./masters-api";

/** Facet filters per master: OR inside one facet, AND across facets (the approved UI's rule). */
export const FACETS: Record<string, { field: string; label: string }[]> = {
  ORGANIZATION: [
    { field: "location", label: "Location" },
    { field: "certifications", label: "Certifications" },
  ],
  AGENTS: [
    { field: "type", label: "Type" },
    { field: "source", label: "Source" },
  ],
  TOOLING: [{ field: "record_status", label: "Status" }],
};

/** Cells that only hold links: shown as links, never edited in the grid (the drawer still can). */
export const LINK_ONLY = new Set(["data_sources", "customers_audience", "systems_tools", "assets"]);
/** Cells that hold a short list: shown as chips. */
export const CHIPS = new Set(["references", "tech_stack", "tools"]);

export const chips = (text: string): string[] =>
  text
    .split(/[\n;,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

export const isUrl = (s: string) => /^https?:\/\/\S+$/i.test(s.trim());

/** Every word of the search must appear in the row (id, label, any cell), case-insensitive. */
export function matchesSearch(e: MasterEntry, search: string): boolean {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [e.row_label, e.stable_id, ...Object.values(e.payload).map(cellText)]
    .join("\n")
    .toLowerCase();
  return words.every((w) => hay.includes(w));
}

export type Facets = Record<string, string[]>;

export function matchesFacets(e: MasterEntry, facets: Facets): boolean {
  return Object.entries(facets).every(([field, picked]) => {
    if (!picked.length) return true;
    const values = chips(cellText(e.payload[field]));
    return picked.some((p) => values.includes(p));
  });
}

/** The distinct values of a facet column across the loaded rows, sorted. */
export const facetValues = (rows: MasterEntry[], field: string): string[] =>
  [...new Set(rows.flatMap((e) => chips(cellText(e.payload[field]))))].sort((a, b) =>
    a.localeCompare(b),
  );

// --- search history: a per-viewer convenience, so localStorage is fine (and optional) ------------

const HISTORY_MAX = 12;
const historyKey = (type: string) => `sbam-masters-search-${type}`;

export function readHistory(type: string): string[] {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(historyKey(type)) ?? "[]");
    return Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string") : [];
  } catch {
    return [];
  }
}

export function pushHistory(type: string, term: string): string[] {
  const t = term.trim();
  if (!t) return readHistory(type);
  const next = [t, ...readHistory(type).filter((s) => s !== t)].slice(0, HISTORY_MAX);
  try {
    window.localStorage.setItem(historyKey(type), JSON.stringify(next));
  } catch {
    // storage refused: the history just isn't kept
  }
  return next;
}

// --- CSV export (client side, from the loaded rows) -----------------------------------------------

const csvCell = (s: string) => {
  // A leading = + - @ would run as a formula in Excel: prefix a quote.
  const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
};

export function toCsv(info: MasterTypeInfo, rows: MasterEntry[]): string {
  const head = [...info.columns.map((c) => c.header), "Status"];
  const lines = rows.map((e) =>
    [...info.columns.map((c) => cellText(e.payload[c.field])), e.status].map(csvCell).join(","),
  );
  return [head.map(csvCell).join(","), ...lines].join("\r\n");
}

export function saveCsv(name: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: "text/csv;charset=utf-8" }));
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  link.click();
  URL.revokeObjectURL(url);
}

/** Enter inside an open multiline editor adds a line (B2); Ctrl/Cmd+Enter saves, Esc cancels. */
export function suppressEnter(p: {
  editing: boolean;
  event: { key: string; ctrlKey: boolean; metaKey: boolean };
}): boolean {
  return p.editing && p.event.key === "Enter" && !p.event.ctrlKey && !p.event.metaKey;
}

export const EDIT_HINT =
  "Click a cell to edit (double-click on most tabs) · Enter adds a line · Ctrl+Enter saves · Esc cancels · each save is a new version";
