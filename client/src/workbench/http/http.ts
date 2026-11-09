// JSON calls to the SBAM API through the server-side proxy. The browser holds no token; errors keep the
// problem+json body so a screen can say what the server said.
import type { ProblemDetail } from "@/contracts";
import { API_BASE, toApiError } from "@/providers/data-provider";

export class ApiFailure extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
    readonly problem?: Partial<ProblemDetail>,
  ) {
    super(message);
    this.name = "ApiFailure";
  }
}

export interface Reply<T> {
  body: T;
  etag: string | null;
}

async function call<T>(
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<Reply<T>> {
  const h = new Headers({ accept: "application/json", ...headers });
  if (body !== undefined) h.set("content-type", "application/json");
  const res = await fetch(`${API_BASE}/${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    const e = await toApiError(res);
    throw new ApiFailure(e.message, e.statusCode, e.problem);
  }
  const parsed = res.status === 204 ? null : ((await res.json()) as T);
  return { body: parsed as T, etag: res.headers.get("etag") };
}

export const getJson = <T>(path: string) => call<T>("GET", path);
export const postJson = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  call<T>("POST", path, body ?? {}, headers);
export const deleteJson = (path: string) => call<null>("DELETE", path);

/** Every row of a cursor-paged list (`{ items, next_cursor }`), up to a safety cap. */
export async function listAll<T>(path: string, pageSize = 100, maxPages = 50): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null | undefined;
  for (let n = 0; n < maxPages; n++) {
    const qs = new URLSearchParams({ limit: String(pageSize) });
    if (cursor) qs.set("cursor", cursor);
    const { body } = await getJson<{ items: T[]; next_cursor?: string | null }>(`${path}?${qs}`);
    items.push(...body.items);
    cursor = body.next_cursor;
    if (!cursor) break;
  }
  return items;
}

/** A fresh key for one start command, so a double click or a retry is the same command. */
export const idempotencyKey = (): string => crypto.randomUUID();
