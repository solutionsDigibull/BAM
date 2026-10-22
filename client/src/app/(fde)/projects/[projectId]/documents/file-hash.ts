// SHA-256 of a chosen file, in the browser, so the upload can notice a file that is already in this
// project's recycle bin before it is sent (E4.4: "offer restore instead"). The server stays the
// authority on duplicates; this only saves the FDE an upload that would create a second copy.
import type { DocumentOut } from "@/contracts";

/** Lower-case hex digest, or null where Web Crypto is unavailable (an insecure origin). */
export async function sha256Hex(data: Blob): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  const digest = await subtle.digest("SHA-256", await data.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A REJECTED, deleted or purged document does not count: the server does not link to it either. */
const isLive = (d: DocumentOut): boolean => !d.deleted_at && d.state !== "REJECTED";

/**
 * The recycle-bin document with these bytes, unless a live document of the project already has them
 * (then the server links the upload to that one and there is nothing to restore).
 */
export function binnedCopy(sha256: string, documents: DocumentOut[]): DocumentOut | null {
  if (documents.some((d) => isLive(d) && d.sha256 === sha256)) return null;
  return documents.find((d) => d.deleted_at && d.state !== "PURGED" && d.sha256 === sha256) ?? null;
}
