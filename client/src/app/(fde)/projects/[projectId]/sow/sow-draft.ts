// Unsaved SOW edits that survive a sign-in (E6.5 "session expiry during edit"). An expired session sends
// the browser to the login page, which would lose a draft held only in memory, so the draft is also kept
// in this tab's sessionStorage while it is unsaved, and offered back on the next visit. Never a token,
// only the editor's document; gone once saved or discarded. Storage can be unavailable (private mode,
// quota): every access is guarded, and the screen works without it.
import type { JSONContent } from "@tiptap/react";

import { isSowDoc } from "./sow-schema";

export type StoredDraft = { baseVersion: number; doc: JSONContent; savedAt: string };

const key = (projectId: string) => `sbam.sow.draft.${projectId}`;

export function readDraft(projectId: string): StoredDraft | null {
  try {
    const raw = window.sessionStorage.getItem(key(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredDraft>;
    return typeof parsed.baseVersion === "number" && isSowDoc(parsed.doc)
      ? (parsed as StoredDraft)
      : null;
  } catch {
    return null;
  }
}

export function writeDraft(projectId: string, baseVersion: number, doc: JSONContent): void {
  try {
    const value: StoredDraft = { baseVersion, doc, savedAt: new Date().toISOString() };
    window.sessionStorage.setItem(key(projectId), JSON.stringify(value));
  } catch {
    // no storage: the draft stays in memory only
  }
}

export function clearDraft(projectId: string): void {
  try {
    window.sessionStorage.removeItem(key(projectId));
  } catch {
    // nothing stored
  }
}

/** The document as the server reads it, so formatting noise never counts as an edit: object keys sorted,
 *  null attributes and empty `content` arrays dropped (TipTap adds and drops both freely). */
export function canonical(doc: unknown): string {
  const norm = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const k of Object.keys(v).sort()) {
        const x = (v as Record<string, unknown>)[k];
        if (x === null || x === undefined || (Array.isArray(x) && x.length === 0)) continue;
        out[k] = norm(x);
      }
      return out;
    }
    return v;
  };
  return JSON.stringify(norm(doc));
}
