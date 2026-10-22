// Notification bell + inbox (E10.5): API paths, polling cadence and the deep-link safety check.
// Plain data and functions, so they are easy to test (TD §8.13 for the shape, issue E10.5 for the UI rules).
import type { Notification, NotificationType } from "@/contracts";

export const notificationsPath = () => "notifications";
export const readPath = (id: string) => `notifications/${id}/read`;
export const readAllPath = () => "notifications/read-all";

/** While a session is active the inbox polls every 3-5s (issue E10.5); paused when the tab is hidden. */
export const POLL_MS = 4000;
/** One page is enough to know "more than the badge can show": antd's Badge overflowCount caps at 99. */
export const UNREAD_PAGE_LIMIT = 100;

export const EMPTY_INBOX = "You're all caught up";
export const MARK_READ_FAILED = "Could not mark as read. Try again.";

export interface NotificationPage {
  items: Notification[];
  next_cursor?: string | null;
}

const TYPE_LABEL: Record<NotificationType, string> = {
  SOW_READY: "SOW ready",
  BREAKDOWN_READY: "Breakdown ready",
  SUBSKILL_NEEDS_REVIEW: "Needs review",
  WORKFLOW_CERTIFIED: "Certified",
  WORKFLOW_FROZEN: "Frozen",
  EXPORT_READY: "Export ready",
  DOCUMENT_REJECTED: "Document rejected",
  GENERATION_FAILED: "Generation failed",
  STAGE_COMPLETED: "Stage completed",
  REMINDER: "Reminder",
};

export const typeLabel = (type: NotificationType): string => TYPE_LABEL[type] ?? type;

/**
 * A link is only ever followed in-app. The backend already drops anything outside `/projects/`
 * (TD §8.13), but the client re-checks before navigating: never an absolute URL, a protocol-relative
 * `//host`, or a `javascript:`/other scheme, even if a stale cache or a future API version slips one in.
 */
export function safeLink(link: string | null): string | null {
  if (!link) return null;
  if (!link.startsWith("/projects/")) return null;
  if (link.startsWith("//")) return null;
  try {
    // A relative path parses against any base; if the parsed origin differs, it wasn't relative.
    const parsed = new URL(link, "http://internal.invalid");
    if (parsed.origin !== "http://internal.invalid") return null;
  } catch {
    return null;
  }
  return link;
}
